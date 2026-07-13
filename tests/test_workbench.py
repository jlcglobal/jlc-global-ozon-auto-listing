import importlib.util
import json
import os
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-field-completion"))
from ozon_field_completion.service import build_tags  # noqa: E402
APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("workbench_app", APP_PATH)
workbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workbench)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_product(root: Path, product_id: str = "P000101") -> Path:
    product_dir = root / "products" / product_id
    write_json(product_dir / "input/source.json", {
        "product_id": product_id,
        "title_cn": "真实测试商品",
        "source_url": "https://detail.1688.com/offer/101.html",
        "captured_at": "2026-07-12T10:00:00+08:00",
        "main_images": [{"local_path": f"products/{product_id}/input/main-images/main-001.jpg"}],
        "detail_images": [],
        "skus": [{"sku_id": "sku-1", "sku_name": "黑色", "purchase_price": 10, "option_values": []}],
    })
    write_json(product_dir / "status.json", {
        "product_id": product_id, "status": "FAILED_HARD_BLOCKER", "current_step": "image_generation",
        "progress": 71, "error_message": "图片质检未通过", "warnings": [], "steps": [], "ozon": {"upload_status": "not_started", "errors": []},
        "api_write_count": 0,
    })
    write_json(product_dir / "output/copy-ru.json", {
        "title_ru": "Тестовый товар", "short_title": "Товар", "description_ru": "Описание",
        "bullets_ru": [{"text_ru": "Преимущество"}], "keywords_ru": ["тест"],
    })
    write_json(product_dir / "output/ozon-category.json", {
        "category_id": 10, "type_id": 20, "category_name": "Категория", "confidence": 0.95,
    })
    write_json(product_dir / "output/ozon-attributes.json", {
        "summary": {"required_count": 1, "mapped_count": 0},
        "missing_required_attributes": [{"attribute_id": 1, "attribute_name": "Модель"}],
        "attributes": [{"attribute_id": 1, "attribute_name": "Модель", "value": "unknown", "required": True, "source": "unknown"}],
    })
    write_json(product_dir / "output/pricing-result.json", {
        "sku_pricing": [{"sku_id": "sku-1", "purchase_cost_cny": 10, "selling_price_rub": 500, "estimated_profit_cny": 12}],
    })
    image_path = product_dir / "output/generated-images/main/main-001.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png")
    write_json(product_dir / "output/image-plan.json", {
        "main_images": [{"slot": "main-001", "image_type": "main", "prompt": "真实商品像素锁", "output_path": f"products/{product_id}/output/generated-images/main/main-001.png"}],
        "detail_images": [{"slot": "detail-001", "image_type": "benefit", "prompt": "功能图", "output_path": f"products/{product_id}/output/generated-images/detail/detail-001.png"}],
    })
    write_json(product_dir / "output/image-qc-report.json", {
        "score": 73, "decision": "reject", "issues": [{"code": "simple_background", "message": "只是换背景", "image_slots": ["main-001"]}],
    })
    thumbnail = product_dir / "input/main-images/main-001.jpg"
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.write_bytes(b"jpg")
    return product_dir


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class WorkbenchTest(unittest.IsolatedAsyncioTestCase):
    def test_live_worker_overrides_stale_stopped_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write_json(product / "status.json", {
                "product_id": "P000101", "status": "STOPPED",
                "current_step": "image_generation", "progress": 71,
                "warnings": [], "steps": [], "ozon": {}, "api_write_count": 0,
            })
            write_json(root / "logs/product-workers/P000101.json", {
                "product_id": "P000101", "pid": os.getpid(),
                "started_at": "2026-07-13T04:31:17+08:00",
            })
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
                card = workbench.workbench_card(product)
            self.assertEqual(detail["status"]["status"], "PROCESSING")
            self.assertEqual(detail["public_state"], "处理中")
            self.assertEqual(card["workflow_bucket"], "生成中")
            self.assertEqual(detail["images"][1]["state"], "GENERATING")

    def test_summary_and_product_list_use_real_product_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                summary = workbench.workbench_summary()
                products = workbench.workbench_products()
            self.assertEqual(summary["counts"]["失败"], 1)
            self.assertEqual(summary["high_risk_count"], 1)
            self.assertEqual(products["total"], 1)

    def test_detail_exposes_copy_images_attributes_pricing_and_risk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
            self.assertEqual(detail["content"]["title_ru"], "Тестовый товар")
            self.assertEqual(len(detail["images"]), 2)
            self.assertEqual(detail["images"][0]["state"], "FAIL")
            self.assertEqual(detail["attributes"]["summary"]["required_count"], 1)
            self.assertEqual(detail["pricing"]["sku_pricing"][0]["selling_price_rub"], 500)
            self.assertEqual(detail["risk"]["level"], "high")

    def test_detail_overlays_generated_final_attribute_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write_json(product / "output/ozon-attributes-final.json", {
                "attributes": [{
                    "attribute_id": 1, "attribute_name": "Модель", "required": True,
                    "value": "MODEL-101", "source": "AI_estimated", "confidence": 0.93,
                    "dictionary_value_id": None, "evidence": ["generated test value"],
                }],
                "required_summary": {"total": 1, "filled": 1, "missing": 0, "missing_attribute_ids": []},
            })
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
            attribute = detail["attributes"]["attributes"][0]
            self.assertEqual(attribute["value"], "MODEL-101")
            self.assertEqual(attribute["source"], "AI_estimated")
            self.assertEqual(detail["attributes"]["summary"]["filled_count"], 1)
            self.assertEqual(detail["attributes"]["missing_required_attributes"], [])

    def test_recovered_low_severity_image_issue_does_not_show_as_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write_json(product / "output/image-qc-report.json", {
                "score": 100, "decision": "pass", "critical_failures": [],
                "issues": [{
                    "code": "initial_russian_overlay_clipped_recovered",
                    "severity": "low", "message": "已修正并通过",
                    "image_slots": ["main-001"],
                }],
            })
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
            self.assertEqual(detail["images"][0]["state"], "PASS")

    async def test_draft_autosave_locks_changed_fields_and_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                result = await workbench.save_workbench_draft("P000101", FakeRequest({"title_ru": "Новый заголовок", "tags": ["#один"]}))
            self.assertTrue(result["saved"])
            self.assertIn("title_ru", result["locked_fields"])
            draft = json.loads((product_dir / "output/workbench-draft.json").read_text(encoding="utf-8"))
            self.assertEqual(draft["version"], 1)
            self.assertTrue((product_dir / "output/workbench-versions.json").is_file())

    async def test_tag_over_30_characters_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                with self.assertRaises(HTTPException) as raised:
                    await workbench.save_workbench_draft("P000101", FakeRequest({"tags": ["#" + "д" * 31]}))
            self.assertEqual(raised.exception.status_code, 422)

    async def test_single_image_request_does_not_queue_whole_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                result = await workbench.queue_single_image_regeneration("P000101", "main-001", FakeRequest({"prompt": "新背景，产品不变"}))
            self.assertTrue(result["queued"])
            request = json.loads((product_dir / "output/image-regeneration-request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["failed_slots"], ["main-001"])
            self.assertTrue(request["preserve_passed_images"])

    async def test_detailed_failed_slots_are_visible_and_can_be_requeued(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root)
            write_json(product_dir / "output/image-regeneration-request.json", {
                "failed_slots": [{"slot": "main-001", "reason": "质检失败"}],
            })
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
                result = await workbench.queue_single_image_regeneration(
                    "P000101", "main-001", FakeRequest({"prompt": "保留真实商品"})
                )
            self.assertEqual(detail["images"][0]["state"], "RETRYING")
            self.assertTrue(result["queued"])
            request = json.loads((product_dir / "output/image-regeneration-request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["failed_slots"], ["main-001"])

    def test_workbench_ui_contains_navigation_and_review_actions(self):
        html = (ROOT / "collector/local-ingest/static/workbench.html").read_text(encoding="utf-8")
        script = (ROOT / "collector/local-ingest/static/workbench.js").read_text(encoding="utf-8")
        self.assertIn("采集箱", html)
        self.assertIn("需要我处理", html)
        self.assertIn("已上架商品", html)
        self.assertIn("自动模式已关闭", html)
        self.assertIn("确认修改并立即上传", script)
        self.assertIn("data-image-action=\"regenerate\"", script)
        self.assertIn("data-draft-field", script)
        self.assertIn("彻底删除", script)
        self.assertIn("按店铺修改售价（可选）", script)
        self.assertIn("确认彻底删除", html)
        self.assertIn("不会删除、撤回或下架Ozon后台商品", html)

    def test_manual_workbench_tags_are_used_only_when_all_30_are_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "P000101"
            tags = [f"#тег{i}" for i in range(30)]
            write_json(product_dir / "output/workbench-draft.json", {"tags": tags})
            result = build_tags(product_dir)
            self.assertEqual(result["tags"], tags)
            self.assertEqual(result["count"], 30)
            write_json(product_dir / "output/workbench-draft.json", {"tags": tags[:29]})
            with self.assertRaises(ValueError):
                build_tags(product_dir)


if __name__ == "__main__":
    unittest.main()
