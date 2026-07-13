import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from scripts.pipeline_runtime import mark_hard_failure


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("owned_workbench_app", APP_PATH)
workbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workbench)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_product(root: Path, product_id: str, owner_id: str, status_name: str = "COLLECTED") -> Path:
    product = root / "products" / product_id
    write_json(product / "input/source.json", {
        "product_id": product_id, "title_cn": f"商品-{owner_id}",
        "source_url": f"https://detail.1688.com/offer/{product_id[1:]}.html",
        "captured_at": "2026-07-13T00:00:00+08:00", "skus": [],
        "main_images": [], "detail_images": [],
    })
    write_json(product / "input/owner.json", {
        "schema_version": "1.0.0", "product_id": product_id,
        "owner_id": owner_id, "owner_name": owner_id,
    })
    write_json(product / "status.json", {
        "product_id": product_id, "status": status_name, "current_step": "queue",
        "progress": 0, "warnings": [], "history": [], "steps": [],
        "error_message": "unknown", "api_write_count": 0,
        "ozon": {"upload_status": "not_started", "errors": []},
    })
    return product


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class WorkbenchOwnershipTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "products").mkdir(parents=True)
        make_product(self.root, "P000001", "alice")
        make_product(self.root, "P000002", "bob", "FAILED_HARD_BLOCKER")
        self.patches = (
            patch.object(workbench, "ROOT", self.root),
            patch.object(workbench, "PRODUCTS_DIR", self.root / "products"),
        )
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def as_operator(self, operator_id: str, role: str = "member"):
        return workbench.CURRENT_OPERATOR.set({
            "id": operator_id, "display_name": operator_id,
            "role": role, "enabled": True,
        })

    def test_product_list_contains_only_current_owner(self):
        token = self.as_operator("alice")
        try:
            result = workbench.workbench_products(page_size=100)
        finally:
            workbench.CURRENT_OPERATOR.reset(token)
        self.assertEqual([item["product_id"] for item in result["items"]], ["P000001"])

    def test_cross_owner_product_is_hidden_as_not_found(self):
        token = self.as_operator("alice")
        try:
            with self.assertRaises(HTTPException) as error:
                workbench.workbench_product_dir("P000002")
        finally:
            workbench.CURRENT_OPERATOR.reset(token)
        self.assertEqual(error.exception.status_code, 404)

    def test_notifications_do_not_reveal_other_owner_failures(self):
        token = self.as_operator("alice")
        try:
            result = workbench.workbench_notifications()
        finally:
            workbench.CURRENT_OPERATOR.reset(token)
        self.assertEqual(result["items"], [])

    async def test_member_cannot_change_global_settings(self):
        token = self.as_operator("alice")
        try:
            with self.assertRaises(HTTPException) as error:
                await workbench.update_workbench_settings(FakeRequest({"auto_mode_enabled": True}))
        finally:
            workbench.CURRENT_OPERATOR.reset(token)
        self.assertEqual(error.exception.status_code, 403)

    async def test_question_answer_is_saved_only_in_owned_product(self):
        product = self.root / "products/P000001"
        write_json(product / "input/pending-question.json", {
            "question_id": "Q-1", "status": "OPEN", "question": "哪个SKU对应大号？",
        })
        token = self.as_operator("alice")
        try:
            result = await workbench.answer_product_question("P000001", FakeRequest({"answer": "SKU-A是大号"}))
        finally:
            workbench.CURRENT_OPERATOR.reset(token)
        self.assertTrue(result["saved"])
        guidance = json.loads((product / "input/operator-guidance.json").read_text())
        self.assertEqual(guidance["answers"][0]["answered_by"], "alice")


class CriticalQuestionCreationTest(unittest.TestCase):
    def make_status(self, product: Path) -> None:
        write_json(product / "status.json", {
            "status": "PROCESSING", "current_step": "variant_rules", "progress": 20,
            "completed_steps": ["collect_source"], "pending_steps": ["variant_rules"],
            "failed_step": "unknown", "retry_count_by_step": {}, "api_write_count": 0,
            "task_authorized": True, "batch_id": "B-TEST", "warnings": [], "steps": [], "history": [],
        })

    def test_only_critical_identity_ambiguity_creates_question(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000001"
            self.make_status(product)
            mark_hard_failure(product, "variant_rules", "SKU mapping ambiguous: 无法确认颜色对应")
            question = json.loads((product / "input/pending-question.json").read_text())
            self.assertEqual(question["status"], "OPEN")

    def test_optional_unknown_does_not_interrupt_user(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000001"
            self.make_status(product)
            mark_hard_failure(product, "field_completion", "optional material is unknown")
            self.assertFalse((product / "input/pending-question.json").exists())


if __name__ == "__main__":
    unittest.main()
