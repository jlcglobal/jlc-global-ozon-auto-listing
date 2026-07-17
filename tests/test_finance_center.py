import base64
import csv
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "finance-center"))
from finance_center import FinanceCenter, OzonReadOnlyError, decimal_value  # noqa: E402


def csv_payload(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return base64.b64encode(output.getvalue().encode("utf-8-sig")).decode("ascii")


class FinanceCenterTest(unittest.TestCase):
    def make_center(self):
        temporary = tempfile.TemporaryDirectory()
        center = FinanceCenter(Path(temporary.name))
        center.initialize()
        return temporary, center

    @staticmethod
    def seed_store(conn):
        conn.execute(
            "INSERT INTO stores(id,store_name,store_alias,client_id_reference,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("shop-1", "Shop One", "Shop One", "local-reference", "2026-07-01", "2026-07-01"),
        )

    def test_excel_serial_dates_are_normalized_during_import(self):
        self.assertEqual(FinanceCenter._excel_date("46174"), "2026-06-01")
        self.assertEqual(FinanceCenter._excel_date("2026-07-01"), "2026-07-01")

    def test_initialize_upgrades_legacy_unmatched_table_without_losing_rows(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        db_path = Path(temporary.name) / "runtime/finance/finance.sqlite3"
        db_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "CREATE TABLE import_unmatched_rows("
                "id TEXT PRIMARY KEY,file_name TEXT NOT NULL,file_path TEXT NOT NULL,file_type TEXT NOT NULL,"
                "source_row_number INTEGER NOT NULL,amount_rub TEXT NOT NULL,amount_cny TEXT NOT NULL,"
                "reason TEXT NOT NULL,resolution_status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL,"
                "store_id TEXT NOT NULL DEFAULT 'default_store')"
            )
            conn.execute(
                "INSERT INTO import_unmatched_rows(id,file_name,file_path,file_type,source_row_number,amount_rub,amount_cny,reason,created_at) "
                "VALUES('legacy-1','old.xlsx','/old.xlsx','finance',1,'12','1','unmatched','2026-07-01')"
            )
            conn.commit()
        center = FinanceCenter(Path(temporary.name), db_path)
        center.initialize()
        with center.connect(readonly=True) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(import_unmatched_rows)")}
            row = conn.execute("SELECT id,file_path,raw_payload FROM import_unmatched_rows").fetchone()
        self.assertIn("raw_payload", columns)
        self.assertEqual(tuple(row), ("legacy-1", "/old.xlsx", "{}"))

    def test_preview_requires_manual_confirmation_for_money_fields(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        content = csv_payload([{"商业编号": "ABC-1", "采购价": "18.50", "日期": "46174"}])
        preview = center.preview_import(file_name="costs.csv", content_base64=content)
        self.assertEqual(preview["file_kind"], "purchase_cost")
        cost_mapping = next(item for item in preview["mapping_candidates"] if item["target_field"] == "purchase_cost_cny")
        self.assertTrue(cost_mapping["requires_manual_confirmation"])
        self.assertFalse(cost_mapping["auto_selected"])

    def test_purchase_cost_import_can_be_rolled_back_without_touching_other_data(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            self.seed_store(conn)
            conn.execute(
                "INSERT INTO product_master(id,store_id,sku,offer_id,product_name,unit_purchase_cost_cny,purchase_cost_source,created_at,updated_at) "
                "VALUES('product-1','shop-1','SKU-1','OFFER-1','Test','10.00','confirmed','2026-07-01','2026-07-01')"
            )
            conn.execute("INSERT INTO finance_center_meta(key,value,updated_at) VALUES('unrelated','keep','2026-07-01')")
        content = csv_payload([{"店铺": "shop-1", "商业编号": "SKU-1", "采购价": "25.00"}])
        result = center.commit_import(
            file_name="costs.csv", content_base64=content, file_kind="purchase_cost",
            mapping={"店铺": "store_id", "商业编号": "sku", "采购价": "purchase_cost_cny"},
            created_by="owner",
        )
        with center.connect(readonly=True) as conn:
            updated = conn.execute("SELECT unit_purchase_cost_cny FROM product_master WHERE id='product-1'").fetchone()[0]
        self.assertEqual(updated, "25.00")
        center.rollback_import(result["batch_id"], rolled_back_by="owner")
        with center.connect(readonly=True) as conn:
            restored = conn.execute("SELECT unit_purchase_cost_cny FROM product_master WHERE id='product-1'").fetchone()[0]
            unrelated = conn.execute("SELECT value FROM finance_center_meta WHERE key='unrelated'").fetchone()[0]
            imported_costs = conn.execute("SELECT COUNT(*) FROM product_costs WHERE batch_id=?", (result["batch_id"],)).fetchone()[0]
        self.assertEqual(restored, "10.00")
        self.assertEqual(unrelated, "keep")
        self.assertEqual(imported_costs, 0)

    def test_profit_margin_is_stored_as_zero_to_one_decimal(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            self.seed_store(conn)
            conn.execute(
                "INSERT INTO product_master(id,store_id,sku,unit_purchase_cost_cny,purchase_cost_source,created_at,updated_at) "
                "VALUES('product-1','shop-1','SKU-1','20.00','confirmed','2026-07-01','2026-07-01')"
            )
            conn.execute(
                "INSERT INTO orders(id,row_hash,file_hash,posting_number,order_number,sku,offer_id,product_name,order_date,buyer_paid_rub,buyer_paid_cny,status,raw_payload,created_at,store_id) "
                "VALUES('order-1','r','f','POST-1','ORDER-1','SKU-1','SKU-1','Test','2026-07-01','1200','100','delivered','{}','2026-07-01','shop-1')"
            )
            conn.execute(
                "INSERT INTO finance_transactions(id,row_hash,file_hash,matched_order_id,posting_number,sku,amount_rub,amount_cny,platform_commission_cny,logistics_fee_cny,refund_cny,compensation_cny,acquiring_cny,other_fee_cny,raw_payload,created_at,store_id) "
                "VALUES('fin-1','r','f','order-1','POST-1','SKU-1','0','0','10','5','0','0','0','0','{}','2026-07-01','shop-1')"
            )
            conn.execute(
                "INSERT INTO ad_spend_transactions(id,row_hash,file_hash,matched_order_id,occurred_at,posting_number,sku,spend_rub,spend_cny,raw_payload,created_at,store_id) "
                "VALUES('ad-1','r','f','order-1','2026-07-01','POST-1','SKU-1','24','2','{}','2026-07-01','shop-1')"
            )
            center._recompute_order(conn, "order-1")
            snapshot = conn.execute("SELECT final_profit_cny,profit_margin FROM profit_snapshots").fetchone()
        self.assertEqual(snapshot["final_profit_cny"], "63.00")
        self.assertEqual(decimal_value(snapshot["profit_margin"]), decimal_value("0.63"))
        self.assertLessEqual(decimal_value(snapshot["profit_margin"]), 1)

    def test_campaign_level_ad_is_removed_from_order_profit_with_backup_and_rollback_ledger(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            self.seed_store(conn)
            conn.execute(
                "INSERT INTO product_master(id,store_id,sku,unit_purchase_cost_cny,purchase_cost_source,created_at,updated_at) "
                "VALUES('product-1','shop-1','SKU-1','20.00','confirmed','2026-07-01','2026-07-01')"
            )
            conn.execute(
                "INSERT INTO orders(id,row_hash,file_hash,posting_number,order_number,sku,offer_id,product_name,order_date,buyer_paid_rub,buyer_paid_cny,status,raw_payload,created_at,store_id) "
                "VALUES('order-1','r','f','POST-1','ORDER-1','SKU-1','OFFER-1','Test','2026-07-01','1200','100','delivered','{}','2026-07-01','shop-1')"
            )
            conn.execute(
                "INSERT INTO finance_transactions(id,row_hash,file_hash,matched_order_id,posting_number,sku,amount_rub,amount_cny,platform_commission_cny,logistics_fee_cny,refund_cny,compensation_cny,acquiring_cny,other_fee_cny,raw_payload,created_at,store_id) "
                "VALUES('fin-1','r','f','order-1','POST-1','SKU-1','0','0','10','5','0','0','0','0','{}','2026-07-01','shop-1')"
            )
            conn.execute(
                "INSERT INTO ad_spend_transactions(id,row_hash,file_hash,matched_order_id,occurred_at,campaign_name,spend_rub,spend_cny,raw_payload,created_at,store_id) "
                "VALUES('ad-campaign','r','f','order-1','2026-07-01','Campaign total','600','50','{}','2026-07-01','shop-1')"
            )
            center._recompute_order(conn, "order-1")
            conn.execute(
                "UPDATE profit_snapshots SET ad_spend_cny='50.00',final_profit_cny='15.00',profit_margin='0.15' WHERE order_id='order-1'"
            )
        preview = center.invalid_ad_match_preview()
        self.assertEqual(preview["invalid_match_count"], 1)
        self.assertEqual(preview["invalid_spend_cny"], "50.00")
        result = center.repair_invalid_ad_matches(apply=True, created_by="test")
        self.assertEqual(result["status"], "applied")
        self.assertTrue(Path(result["backup_path"]).is_file())
        with center.connect(readonly=True) as conn:
            ad_match = conn.execute("SELECT matched_order_id FROM ad_spend_transactions WHERE id='ad-campaign'").fetchone()[0]
            snapshot = conn.execute("SELECT ad_spend_cny,final_profit_cny FROM profit_snapshots WHERE order_id='order-1'").fetchone()
            unmatched = conn.execute("SELECT COUNT(*) FROM import_unmatched_rows WHERE file_type='ads'").fetchone()[0]
            batch = conn.execute("SELECT status,file_kind FROM finance_import_batches WHERE id=?", (result["batch_id"],)).fetchone()
        self.assertIsNone(ad_match)
        self.assertEqual(tuple(snapshot), ("0.00", "65.00"))
        self.assertEqual(unmatched, 1)
        self.assertEqual(tuple(batch), ("applied", "system_ad_match_repair"))
        overview = center.overview(store_id="all", date_from="2026-07-01", date_to="2026-07-01")
        self.assertEqual(overview["summary"]["fully_covered_order_lines"], 0)
        self.assertEqual(overview["summary"]["confirmed_profit"], "0.00")
        self.assertEqual(overview["summary"]["expected_profit"], "15.00")

    def test_expected_profit_is_unavailable_without_cost_coverage_samples(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            self.seed_store(conn)
            conn.execute(
                "INSERT INTO orders(id,row_hash,file_hash,posting_number,order_number,sku,offer_id,product_name,order_date,buyer_paid_rub,buyer_paid_cny,status,raw_payload,created_at,store_id) "
                "VALUES('order-1','r','f','POST-1','ORDER-1','SKU-1','OFFER-1','Test','2026-07-14','1200','100','awaiting_packaging','{}','2026-07-14','shop-1')"
            )
            center._recompute_order(conn, "order-1")
        overview = center.overview(store_id="all", date_from="2026-07-14", date_to="2026-07-14")
        self.assertEqual(overview["summary"]["sales"], "100.00")
        self.assertFalse(overview["summary"]["expected_profit_available"])
        self.assertIsNone(overview["summary"]["expected_profit"])
        self.assertIsNone(overview["summary"]["expected_margin"])
        self.assertEqual(
            overview["summary"]["expected_profit_missing_sources"],
            ["purchase", "finance", "logistics", "ads"],
        )
        self.assertIsNone(overview["gap_estimates"]["missing_finance"])
        self.assertIn("暂不计算预计利润", overview["gap_estimates"]["method"])

    def test_only_read_only_ozon_endpoints_are_used(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            self.seed_store(conn)
        endpoints = []

        def fake_post(_credentials, endpoint, _payload):
            endpoints.append(endpoint)
            if endpoint == "/v2/posting/fbo/list":
                return {"result": []}
            if endpoint == "/v3/posting/fbs/list":
                return {"result": {"postings": []}}
            if endpoint == "/v3/finance/transaction/list":
                return {"result": {"operations": [], "page_count": 1}}
            raise AssertionError(endpoint)

        configured = [({"id": "shop-1", "display_name": "Shop One"}, "shop-1", {"client_id": "local", "api_key": "local"})]
        with patch.object(center, "_configured_shops", return_value=configured), patch.object(center, "_seller_post", side_effect=fake_post):
            result = center.sync(date_from="2026-07-01", date_to="2026-07-02")
        self.assertTrue(result["success"])
        self.assertEqual(result["ozon_write_api_calls"], 0)
        self.assertEqual(endpoints, [
            "/v2/posting/fbo/list", "/v3/posting/fbs/list", "/v3/finance/transaction/list",
        ])

    def test_ninety_day_finance_sync_uses_safe_twenty_eight_day_windows(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            self.seed_store(conn)
        finance_windows = []

        def fake_post(_credentials, endpoint, payload):
            if endpoint == "/v2/posting/fbo/list":
                return {"result": []}
            if endpoint == "/v3/posting/fbs/list":
                return {"result": {"postings": []}}
            if endpoint == "/v3/finance/transaction/list":
                finance_windows.append(payload["filter"]["date"])
                return {"result": {"operations": [], "page_count": 1}}
            raise AssertionError(endpoint)

        configured = [({"id": "shop-1", "display_name": "Shop One"}, "shop-1", {"client_id": "local", "api_key": "local"})]
        with patch.object(center, "_configured_shops", return_value=configured), patch.object(center, "_seller_post", side_effect=fake_post):
            result = center.sync(date_from="2026-04-17", date_to="2026-07-15")
        self.assertTrue(result["complete"])
        self.assertEqual(len(finance_windows), 4)
        for window in finance_windows:
            start = datetime.fromisoformat(window["from"][:10])
            end = datetime.fromisoformat(window["to"][:10])
            self.assertLessEqual((end - start).days, 27)

    def test_http_400_stops_remaining_stores_and_opens_manual_circuit(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        endpoints = []

        def fake_post(_credentials, endpoint, _payload):
            endpoints.append(endpoint)
            if endpoint == "/v2/posting/fbo/list":
                return {"result": []}
            if endpoint == "/v3/posting/fbs/list":
                return {"result": {"postings": []}}
            if endpoint == "/v3/finance/transaction/list":
                raise OzonReadOnlyError(
                    "Ozon 只读接口返回 HTTP 400", endpoint=endpoint,
                    status_code=400, retryable=False,
                )
            raise AssertionError(endpoint)

        configured = [
            ({"id": f"shop-{index}", "display_name": f"Shop {index}"}, f"shop-{index}", {"client_id": "local", "api_key": "local"})
            for index in range(1, 4)
        ]
        with patch.object(center, "_configured_shops", return_value=configured), patch.object(center, "_seller_post", side_effect=fake_post):
            result = center.sync(date_from="2026-07-15", date_to="2026-07-15")
            with self.assertRaisesRegex(RuntimeError, "自动暂停"):
                center.sync(date_from="2026-07-15", date_to="2026-07-15")
        self.assertFalse(result["complete"])
        self.assertEqual([item["status"] for item in result["stores"]], ["failed", "skipped", "skipped"])
        self.assertEqual(endpoints, [
            "/v2/posting/fbo/list", "/v3/posting/fbs/list", "/v3/finance/transaction/list",
        ])
        with center.connect(readonly=True) as conn:
            runs = conn.execute("SELECT COUNT(*),MAX(read_api_calls) FROM finance_sync_runs").fetchone()
        self.assertEqual(tuple(runs), (1, 3))
        self.assertTrue(center._active_read_circuit(datetime(2026, 7, 14, 23, 6, 0)))

    def test_scheduler_catches_up_after_missed_3pm(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with patch.object(center, "sync", return_value={"success": True}) as mocked:
            result = center.scheduler_tick(datetime(2026, 7, 14, 10, 0, 0))
        self.assertEqual(result["due_day"], "2026-07-13")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(mocked.call_args.kwargs["trigger"], "scheduled_catch_up")

    def test_manual_sync_before_3pm_does_not_suppress_the_daily_3pm_sync(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            center._set_meta(conn, "last_successful_sync_date", "2026-07-14")
            center._set_meta(conn, "last_successful_sync_at", "2026-07-14T12:27:00+08:00")
        with patch.object(center, "sync", return_value={"success": True}) as mocked:
            result = center.scheduler_tick(datetime(2026, 7, 14, 15, 5, 0))
        self.assertEqual(result["status"], "completed")
        mocked.assert_called_once()

    def test_sync_after_3pm_satisfies_the_daily_schedule(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            center._set_meta(conn, "last_successful_sync_date", "2026-07-14")
            center._set_meta(conn, "last_successful_sync_at", "2026-07-14T15:01:00+08:00")
        with patch.object(center, "sync", return_value={"success": True}) as mocked:
            result = center.scheduler_tick(datetime(2026, 7, 14, 15, 5, 0))
        self.assertEqual(result["status"], "not_due")
        mocked.assert_not_called()

    def test_non_retryable_scheduled_failure_is_not_repeated_every_minute(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        failed = {
            "success": False, "complete": False,
            "stores": [{"status": "failed", "error": "HTTP 400", "retryable": False}],
        }
        with patch.object(center, "sync", return_value=failed) as mocked:
            first = center.scheduler_tick(datetime(2026, 7, 14, 15, 5, 0))
            second = center.scheduler_tick(datetime(2026, 7, 14, 15, 6, 0))
        self.assertEqual(first["status"], "blocked_for_day")
        self.assertEqual(second["status"], "blocked_for_day")
        mocked.assert_called_once()

    def test_retryable_scheduled_failure_waits_an_hour_and_retries_only_once(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        failed = {
            "success": False, "complete": False,
            "stores": [{"status": "failed", "error": "HTTP 503", "retryable": True}],
        }
        completed = {"success": True, "complete": True, "stores": [{"status": "success"}]}
        with patch.object(center, "sync", side_effect=[failed, completed]) as mocked:
            first = center.scheduler_tick(datetime(2026, 7, 14, 15, 5, 0))
            waiting = center.scheduler_tick(datetime(2026, 7, 14, 15, 35, 0))
            retried = center.scheduler_tick(datetime(2026, 7, 14, 16, 6, 0))
        self.assertEqual(first["status"], "backoff")
        self.assertEqual(waiting["status"], "backoff")
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(mocked.call_count, 2)

    def test_legacy_failed_schedule_is_blocked_on_restart_without_another_api_batch(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            conn.execute(
                "INSERT INTO finance_sync_runs(id,store_id,started_at,finished_at,date_from,date_to,trigger,status,error,write_api_calls) "
                "VALUES('legacy-failed','shop-1','2026-07-14T23:00:00+08:00','2026-07-14T23:01:00+08:00',"
                "'2026-04-16','2026-07-14','scheduled_catch_up','failed',"
                "'Ozon 只读接口 /v3/finance/transaction/list 返回 HTTP 400',0)"
            )
        with patch.object(center, "sync", return_value={"success": True, "complete": True}) as mocked:
            first = center.scheduler_tick(datetime(2026, 7, 14, 23, 5, 0))
            second = center.scheduler_tick(datetime(2026, 7, 14, 23, 6, 0))
        self.assertEqual(first["status"], "blocked_for_day")
        self.assertEqual(second["status"], "blocked_for_day")
        mocked.assert_not_called()
        self.assertTrue(center._active_read_circuit(datetime(2026, 7, 14, 23, 6, 0)))

    def test_interrupted_running_schedule_is_not_replayed_after_restart(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            center._set_meta(
                conn, "scheduled_sync_state",
                '{"due_day":"2026-07-14","attempt_count":1,"status":"running","last_attempt_at":"2026-07-14T15:05:00"}',
            )
        with patch.object(center, "sync", return_value={"success": True, "complete": True}) as mocked:
            result = center.scheduler_tick(datetime(2026, 7, 14, 15, 10, 0))
        self.assertEqual(result["status"], "blocked_for_day")
        self.assertIn("未自动重放", result["reason"])
        mocked.assert_not_called()

    def test_stale_running_sync_is_recovered_without_write_calls(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            self.seed_store(conn)
            conn.execute(
                "INSERT INTO finance_sync_runs(id,store_id,started_at,date_from,date_to,trigger,status,write_api_calls) "
                "VALUES('stale','shop-1','2026-07-13','2026-07-01','2026-07-13','scheduled','running',0)"
            )
            conn.execute("UPDATE stores SET sync_status='running' WHERE id='shop-1'")
        self.assertEqual(center.recover_interrupted_syncs(), 1)
        with center.connect(readonly=True) as conn:
            run = conn.execute("SELECT status,write_api_calls FROM finance_sync_runs WHERE id='stale'").fetchone()
            store = conn.execute("SELECT sync_status FROM stores WHERE id='shop-1'").fetchone()
        self.assertEqual(tuple(run), ("interrupted", 0))
        self.assertEqual(store[0], "idle")


if __name__ == "__main__":
    unittest.main()
