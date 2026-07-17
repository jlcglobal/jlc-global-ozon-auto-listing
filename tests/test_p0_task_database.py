from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path

from scripts.task_database import (
    due_pending_store_ids,
    initialize,
    migrate_all,
    product_snapshot,
    record_remote_check,
    remote_backoff_seconds,
)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class P0TaskDatabaseTest(unittest.TestCase):
    def test_simulated_30_products_20_categories_10_stores(self) -> None:
        """The P0 state model remains aggregate-safe at launch scale."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for product_number in range(1, 31):
                product_id = f"P{product_number:06d}"
                product_dir = root / "products" / product_id
                _write(product_dir / "input/source.json", {
                    "product_id": product_id,
                    "owner_id": "owner-a",
                    "skus": [{"sku_id": f"sku-{index}"} for index in range(1, 4)],
                })
                _write(product_dir / "status.json", {
                    "product_id": product_id,
                    "category": {"category_id": 1000 + product_number % 20, "type_id": 2000 + product_number % 20},
                })
                stores = {}
                for store_number in range(1, 11):
                    store_id = f"store-{store_number:02d}"
                    state = "SUCCESS" if store_number <= 7 else "PENDING_REMOTE" if store_number <= 9 else "FAILED"
                    task_id = "unknown" if state == "SUCCESS" else f"task-{product_id}-{store_id}"
                    product_ids = [f"{product_number}{store_number}{sku}" for sku in range(1, 4)] if state == "SUCCESS" else ["unknown"] * 3
                    stores[store_id] = {
                        "selected": True,
                        "status": state,
                        "api_write_count": 1,
                        "sku_publications": [
                            {
                                "sku_id": f"sku-{sku}",
                                "offer_id": f"offer-{product_id}-{store_id}-{sku}",
                                "task_id": task_id,
                                "ozon_product_id": product_ids[sku - 1],
                                "action": "CREATE",
                            }
                            for sku in range(1, 4)
                        ],
                    }
                _write(product_dir / "output/store-publications.json", {"product_id": product_id, "stores": stores})

            report = migrate_all(root)
            self.assertEqual(len(report["migrated"]), 30)
            self.assertEqual(report["errors"], [])
            snapshot = product_snapshot(root, "P000001")
            self.assertEqual(snapshot["product"]["target_store_count"], 10)
            self.assertEqual(snapshot["product"]["created_store_count"], 7)
            self.assertEqual(snapshot["product"]["pending_store_count"], 2)
            self.assertEqual(snapshot["product"]["failed_store_count"], 1)
            self.assertEqual(snapshot["product"]["aggregate_status"], "PARTIAL_FAILED")
            self.assertEqual(len(snapshot["stores"]), 10)
            self.assertEqual(len(snapshot["sku_publications"]), 30)

    def test_remote_status_backoff_and_private_database_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "products/P000001"
            _write(product / "input/source.json", {"product_id": "P000001", "skus": [{"sku_id": "sku-1"}]})
            _write(product / "status.json", {})
            _write(product / "output/store-publications.json", {"product_id": "P000001", "stores": {
                "store-a": {"selected": True, "status": "PENDING_REMOTE", "sku_publications": [{"sku_id": "sku-1", "task_id": "task-a", "offer_id": "offer-a"}]},
            }})
            initialize(root)
            migrate_all(root)
            self.assertEqual([remote_backoff_seconds(i) for i in range(5)], [60, 300, 900, 1800, 3600])
            mode = (root / "runtime/task-db.sqlite3").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)
            self.assertEqual(due_pending_store_ids(root, "P000001"), [])
            with closing(sqlite3.connect(root / "runtime/task-db.sqlite3")) as db:
                with db:
                    db.execute("UPDATE tasks SET next_check_at=?", (datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(),))
            self.assertEqual(due_pending_store_ids(root, "P000001"), ["store-a"])
            record_remote_check(root, "P000001", ["store-a"])
            self.assertEqual(due_pending_store_ids(root, "P000001"), [])
            with closing(sqlite3.connect(root / "runtime/task-db.sqlite3")) as db:
                row = db.execute("SELECT read_query_count, next_check_at FROM tasks WHERE task_id='task-a'").fetchone()
            self.assertEqual(row[0], 1)
            self.assertTrue(row[1])
