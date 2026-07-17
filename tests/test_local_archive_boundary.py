import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.multi_store_upload import HANDOFF_STATE, _store_result, aggregate_product_status
from scripts.remote_status_worker import run_once
from scripts.task_database import archive_legacy_local_data, database, initialize, sync_publications_json
from ozon_uploader.image_channels import image_channel_ttl


class LocalArchiveBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "products" / "P000001" / "input").mkdir(parents=True)
        (self.root / "products" / "P000001" / "output").mkdir(parents=True)
        (self.root / "products" / "P000001" / "input/source.json").write_text(
            json.dumps({"title_cn": "测试商品", "skus": [{"sku_id": "sku-1"}]}), encoding="utf-8"
        )
        (self.root / "products" / "P000001" / "status.json").write_text(
            json.dumps({"status": "PENDING_REMOTE", "api_write_count": 1, "ozon": {"task_id": "task-1"}}), encoding="utf-8"
        )
        (self.root / "products" / "P000001" / "output/store-publications.json").write_text(json.dumps({
            "stores": {"store-a": {"selected": True, "status": "PENDING_REMOTE", "api_write_count": 1,
                "payload_hash": "hash-1", "sku_publications": [{"sku_id": "sku-1", "task_id": "task-1", "offer_id": "offer-1", "payload_hash": "hash-1"}]}}
        }), encoding="utf-8")
        (self.root / "batches" / "B-TEST" ).mkdir(parents=True)
        (self.root / "batches/B-TEST/batch.json").write_text(json.dumps({"batch_id": "B-TEST", "products": [{"product_id": "P000001"}]}), encoding="utf-8")
        initialize(self.root)
        sync_publications_json(self.root, self.root / "products/P000001")

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive_marks_local_state_and_keeps_immutable_summary(self):
        first = archive_legacy_local_data(self.root, ["P000001"])
        self.assertEqual(first["ozon_write_api_calls"], 0)
        self.assertEqual(first["ozon_read_api_calls"], 0)
        self.assertEqual(first["inventory_api_calls"], 0)
        with database(self.root) as db:
            self.assertEqual(db.execute("select aggregate_status from products where product_id='P000001'").fetchone()[0], "ARCHIVED")
            task = db.execute("select status,next_check_at,task_id,payload_hash,write_attempt_count from tasks where product_id='P000001'").fetchone()
            self.assertEqual(tuple(task), ("ABANDONED", None, "task-1", "hash-1", 1))
            original = db.execute("select archived_at,summary_sha256 from archive_records where entity_type='product' and entity_id='P000001'").fetchone()
        second = archive_legacy_local_data(self.root, ["P000001"])
        with database(self.root) as db:
            current = db.execute("select archived_at,summary_sha256 from archive_records where entity_type='product' and entity_id='P000001'").fetchone()
        self.assertEqual(tuple(original), tuple(current))
        self.assertEqual(second["archived_products"], ["P000001"])
        self.assertTrue((self.root / "products/P000001/input/source.json").is_file())
        self.assertEqual(json.loads((self.root / "batches/B-TEST/batch.json").read_text())["local_lifecycle_status"], "ARCHIVED")

    def test_remote_worker_is_disabled_without_calls(self):
        result = run_once(self.root)
        self.assertTrue(result["disabled"])
        self.assertEqual(result["read_api_calls"], 0)
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)

    def test_task_id_is_local_handoff_terminal(self):
        record = {"selected": True, "sku_publications": [{"sku_id": "sku-1"}]}
        _store_result(record, {"status": {"status": "SUBMITTED", "api_write_count": 1}, "result": {"items": [{"source_sku_id": "sku-1", "task_id": "task-1", "offer_id": "offer-1"}]}})
        self.assertEqual(record["status"], HANDOFF_STATE)
        product = self.root / "products/P000001"
        status = aggregate_product_status(product, {"stores": {"store-a": record}}, self.root)
        self.assertEqual(status["status"], HANDOFF_STATE)
        self.assertEqual(status["next_action"], "complete")

    def test_image_channel_ttl_is_fixed_and_independent(self):
        ttl = image_channel_ttl("2026-07-16T00:00:00+00:00")
        self.assertEqual(ttl["ttl_seconds"], 86400)
        self.assertEqual(ttl["expires_at"], "2026-07-17T00:00:00+00:00")
        self.assertEqual(ttl["close_policy"], "fixed_ttl")


if __name__ == "__main__":
    unittest.main()
