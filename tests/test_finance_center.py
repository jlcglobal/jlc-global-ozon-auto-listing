import base64
import csv
import io
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "finance-center"))
from finance_center import FinanceCenter, OzonReadOnlyError, decimal_value, safe_json  # noqa: E402


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

    def test_initialize_is_idempotent_across_parallel_reads(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        center = FinanceCenter(Path(temporary.name))
        center.initialize()
        errors = []

        def initialize_again():
            try:
                center.initialize()
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        workers = [threading.Thread(target=initialize_again) for _ in range(12)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        self.assertEqual(errors, [])
        with center.connect(readonly=True) as conn:
            version = conn.execute(
                "SELECT value FROM finance_center_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(version, "1.0.2")

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

    def test_purchase_order_import_separates_skus_and_sums_repeated_sku_rows(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            conn.execute(
                "INSERT INTO stores(id,store_name,store_alias,client_id_reference,created_at,updated_at) "
                "VALUES('default_store','zhonglian1','zhonglian1','local-reference','2026-08-01','2026-08-01')"
            )

            def add_order(row_id, posting, sku, offer_id, quantity):
                conn.execute(
                    "INSERT INTO orders(id,row_hash,file_hash,posting_number,order_number,sku,offer_id,product_name,"
                    "order_date,buyer_paid_rub,buyer_paid_cny,status,raw_payload,created_at,store_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row_id, row_id, "seller", posting, posting, sku, offer_id, offer_id,
                        "2026-08-01", "1200", "100", "delivered",
                        '{"product":{"quantity":' + str(quantity) + '}}', "2026-08-01", "default_store",
                    ),
                )
                conn.execute(
                    "INSERT INTO product_master(id,store_id,sku,offer_id,product_name,purchase_cost_source,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,'missing','2026-08-01','2026-08-01')",
                    ("product-" + row_id, "default_store", sku, offer_id, offer_id),
                )

            add_order("five-seed", "POST-5", "SKU-5", "OFFER-5", 1)
            add_order("seven-seed", "POST-7", "SKU-7", "OFFER-7", 1)
            add_order("five-multi", "POST-M", "SKU-5-M", "OFFER-5", 2)
            add_order("seven-multi", "POST-M", "SKU-7-M", "OFFER-7", 1)

        content = csv_payload([
            {"店铺": "zhonglian1", "名称": "5L", "订单号": "POST-5", "采购成本": "20"},
            {"店铺": "zhonglian1", "名称": "7L", "订单号": "POST-7", "采购成本": "21"},
            {"店铺": "zhonglian1", "名称": "5L", "订单号": "POST-M", "采购成本": "20"},
            {"店铺": "zhonglian1", "名称": "5L", "订单号": "POST-M", "采购成本": "20"},
            {"店铺": "zhonglian1", "名称": "7L", "订单号": "POST-M", "采购成本": "21"},
        ])
        result = center.commit_import(
            file_name="purchase.csv", content_base64=content, file_kind="purchase_cost",
            mapping={"店铺": "store_id", "名称": "product_name", "订单号": "order_number", "采购成本": "purchase_cost_cny"},
            created_by="owner",
        )
        self.assertEqual(result["matched_count"], 5)
        self.assertEqual(result["unmatched_count"], 0)
        with center.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT sku,purchase_cost_cny FROM purchase_order_match WHERE posting_number='POST-M' "
                "ORDER BY sku"
            ).fetchall()
            snapshots = conn.execute(
                "SELECT sku,purchase_cost_cny,unit_purchase_cost_cny,purchase_cost_source "
                "FROM profit_snapshots WHERE posting_number='POST-M' ORDER BY sku"
            ).fetchall()
        self.assertEqual([(row["sku"], row["purchase_cost_cny"]) for row in rows], [("OFFER-5", "40.00"), ("OFFER-7", "21.00")])
        self.assertEqual(
            [(row["purchase_cost_cny"], row["unit_purchase_cost_cny"], row["purchase_cost_source"]) for row in snapshots],
            [("40.00", "20.00", "order_purchase_record"), ("21.00", "21.00", "order_purchase_record")],
        )

        center.rollback_import(result["batch_id"], rolled_back_by="owner")
        with center.connect(readonly=True) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM purchase_order_match WHERE source_file='purchase.csv'").fetchone()[0],
                0,
            )

    def test_single_sku_cost_input_updates_same_sku_across_stores_and_can_rollback(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            self.seed_store(conn)
            conn.execute(
                "INSERT INTO stores(id,store_name,store_alias,status,sync_status,created_at,updated_at) "
                "VALUES('shop-2','Shop 2','Shop 2','active','idle','2026-07-01','2026-07-01')"
            )
            for index, store_id in enumerate(("shop-1", "shop-2"), start=1):
                conn.execute(
                    "INSERT INTO product_master(id,store_id,sku,offer_id,unit_purchase_cost_cny,purchase_cost_source,created_at,updated_at) "
                    "VALUES(?,?,?,?,NULL,'missing','2026-07-01','2026-07-01')",
                    (f"product-{index}", store_id, f"OZON-{index}", "SHARED-SKU"),
                )
                conn.execute(
                    "INSERT INTO orders(id,row_hash,file_hash,posting_number,order_number,sku,offer_id,product_name,order_date,buyer_paid_rub,buyer_paid_cny,status,raw_payload,created_at,store_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"order-{index}", f"r{index}", "f", f"POST-{index}", f"ORDER-{index}",
                        f"OZON-{index}", "SHARED-SKU", "Test", "2026-07-01", "1200", "100", "delivered",
                        safe_json({"product": {"quantity": 2 if index == 1 else 1}}), "2026-07-01", store_id,
                    ),
                )
                center._recompute_order(conn, f"order-{index}")
            conn.execute(
                "INSERT INTO purchase_order_match(id,store_id,order_id,posting_number,sku,purchase_cost_cny,source_file,source_row,matched_at,created_at) "
                "VALUES('purchase-1','shop-1','order-1','POST-1','OZON-1','40.00','old.xlsx',2,'2026-07-01','2026-07-01')"
            )
            center._recompute_order(conn, "order-1")
        result = center.set_sku_purchase_cost(sku="SHARED-SKU", purchase_cost_cny="12", created_by="owner")
        self.assertEqual(result["affected_order_count"], 2)
        self.assertEqual(result["affected_store_count"], 2)
        with center.connect(readonly=True) as conn:
            masters = conn.execute(
                "SELECT store_id,unit_purchase_cost_cny,purchase_cost_source FROM product_master ORDER BY store_id"
            ).fetchall()
            purchase = conn.execute("SELECT purchase_cost_cny FROM purchase_order_match WHERE id='purchase-1'").fetchone()[0]
            snapshots = conn.execute(
                "SELECT order_id,purchase_cost_cny FROM profit_snapshots ORDER BY order_id"
            ).fetchall()
        self.assertEqual([tuple(row) for row in masters], [
            ("shop-1", "12.00", "manual_sku_cost"),
            ("shop-2", "12.00", "manual_sku_cost"),
        ])
        self.assertEqual(purchase, "24.00")
        self.assertEqual([tuple(row) for row in snapshots], [("order-1", "24.00"), ("order-2", "12.00")])

        center.rollback_import(result["batch_id"], rolled_back_by="owner")
        with center.connect(readonly=True) as conn:
            purchase = conn.execute("SELECT purchase_cost_cny FROM purchase_order_match WHERE id='purchase-1'").fetchone()[0]
            snapshots = conn.execute(
                "SELECT order_id,purchase_cost_cny,purchase_cost_source FROM profit_snapshots ORDER BY order_id"
            ).fetchall()
        self.assertEqual(purchase, "40.00")
        self.assertEqual([tuple(row) for row in snapshots], [
            ("order-1", "40.00", "order_purchase_record"),
            ("order-2", "0.00", "missing"),
        ])

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

    def test_ozon_finance_ad_operations_are_counted_once_and_supersede_imported_ads(self):
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
                "INSERT INTO finance_transactions(id,row_hash,file_hash,matched_order_id,posting_number,sku,occurred_at,operation_type,amount_rub,amount_cny,platform_commission_cny,logistics_fee_cny,refund_cny,compensation_cny,acquiring_cny,other_fee_cny,raw_payload,created_at,store_id) "
                "VALUES('fin-1','r1','f','order-1','POST-1','SKU-1','2026-07-01','sale','0','0','10','5','0','0','0','0','{}','2026-07-01','shop-1')"
            )
            conn.execute(
                "INSERT INTO finance_transactions(id,row_hash,file_hash,occurred_at,operation_type,service_name,amount_rub,amount_cny,raw_payload,created_at,store_id) "
                "VALUES('ad-cpo','r2','f','2026-07-01','OperationPromotionWithCostPerOrder','Продвижение с оплатой за заказ','-144','-12','{}','2026-07-01','shop-1')"
            )
            conn.execute(
                "INSERT INTO finance_transactions(id,row_hash,file_hash,occurred_at,operation_type,service_name,amount_rub,amount_cny,raw_payload,created_at,store_id) "
                "VALUES('ad-cpc','r3','f','2026-07-01','OperationMarketplaceCostPerClick','Оплата за клик','-96','-8','{}','2026-07-01','shop-1')"
            )
            conn.execute(
                "INSERT INTO ad_spend_transactions(id,row_hash,file_hash,occurred_at,campaign_name,spend_rub,spend_cny,raw_payload,created_at,store_id) "
                "VALUES('legacy-ad','r4','f','2026-07-01','Legacy campaign','600','50','{}','2026-07-01','shop-1')"
            )
            center._recompute_order(conn, "order-1")
        overview = center.overview(store_id="all", date_from="2026-07-01", date_to="2026-07-01")
        self.assertEqual(overview["summary"]["ad_spend"], "20.00")
        self.assertEqual(overview["summary"]["expected_profit"], "45.00")
        self.assertEqual(overview["coverage"]["ads"], 1.0)
        self.assertEqual(overview["advertising"]["source"], "ozon_finance")
        self.assertEqual(overview["advertising"]["api_record_count"], 2)
        self.assertEqual(overview["advertising"]["imported_record_count"], 0)
        self.assertIn("Ozon Finance", overview["warnings"][1])

    def test_operation_level_delivery_expenses_are_classified_as_logistics(self):
        delivery = FinanceCenter._service_buckets({
            "operation_type": "MarketplaceRedistributionOfDeliveryServicesOperation",
            "operation_type_name": "Перевыставление услуг доставки",
            "amount": "-400", "services": [],
        }, decimal_value("10"))
        agency = FinanceCenter._service_buckets({
            "operation_type": "OperationMarketplaceAgencyFeeAggregator3PLGlobal",
            "operation_type_name": "транспортно-экспедиционных услуг",
            "amount": "-100", "services": [],
        }, decimal_value("10"))
        ads = FinanceCenter._service_buckets({
            "operation_type": "OperationMarketplaceCostPerClick",
            "operation_type_name": "Оплата за клик",
            "amount": "-200", "services": [],
        }, decimal_value("10"))
        self.assertEqual(delivery["logistics"], decimal_value("40"))
        self.assertEqual(agency["logistics"], decimal_value("10"))
        self.assertEqual(sum(ads.values()), decimal_value("0"))

    def test_unsettled_order_uses_same_sku_history_and_period_ad_allocation(self):
        temporary, center = self.make_center()
        self.addCleanup(temporary.cleanup)
        with center.connect() as conn:
            self.seed_store(conn)
            conn.execute(
                "INSERT INTO product_master(id,store_id,sku,offer_id,unit_purchase_cost_cny,purchase_cost_source,created_at,updated_at) "
                "VALUES('product-1','shop-1','SKU-1','OFFER-1','30.00','confirmed','2026-07-01','2026-07-01')"
            )
            for order_id, posting, sales, status, order_date in (
                ("settled", "POST-1", "100", "delivered", "2026-07-01"),
                ("pending", "POST-2", "200", "awaiting_deliver", "2026-07-01"),
            ):
                conn.execute(
                    "INSERT INTO orders(id,row_hash,file_hash,posting_number,order_number,sku,offer_id,product_name,order_date,buyer_paid_rub,buyer_paid_cny,status,raw_payload,created_at,store_id) "
                    "VALUES(?,?,?,?,?,'SKU-1','OFFER-1','Test',?,'0',?,?, '{}','2026-07-01','shop-1')",
                    (order_id, order_id, "f", posting, posting, order_date, sales, status),
                )
            conn.execute(
                "INSERT INTO finance_transactions(id,row_hash,file_hash,matched_order_id,posting_number,sku,occurred_at,operation_type,amount_rub,amount_cny,platform_commission_cny,logistics_fee_cny,refund_cny,compensation_cny,acquiring_cny,other_fee_cny,raw_payload,created_at,store_id) "
                "VALUES('fin-1','r1','f','settled','POST-1','SKU-1','2026-07-01','sale','0','0','10','20','0','0','0','0','{}','2026-07-01','shop-1')"
            )
            conn.execute(
                "INSERT INTO finance_transactions(id,row_hash,file_hash,occurred_at,operation_type,service_name,amount_rub,amount_cny,raw_payload,created_at,store_id) "
                "VALUES('ad-1','r2','f','2026-07-01','OperationMarketplaceCostPerClick','Оплата за клик','-300','-30','{}','2026-07-01','shop-1')"
            )
            center._recompute_order(conn, "settled")
            center._recompute_order(conn, "pending")
        result = center.orders(store_id="all", date_from="2026-07-01", date_to="2026-07-01", limit=10)
        items = {item["posting_number"]: item for item in result["items"]}
        self.assertEqual(items["POST-1"]["finance_fee_cny"], "10.00")
        self.assertEqual(items["POST-1"]["logistics_cny"], "20.00")
        self.assertEqual(items["POST-1"]["ad_spend_cny"], "10.00")
        self.assertEqual(items["POST-1"]["profit_cny"], "30.00")
        self.assertEqual(items["POST-2"]["finance_fee_cny"], "20.00")
        self.assertEqual(items["POST-2"]["logistics_cny"], "40.00")
        self.assertEqual(items["POST-2"]["ad_spend_cny"], "20.00")
        self.assertEqual(items["POST-2"]["profit_cny"], "90.00")
        self.assertEqual(items["POST-2"]["cost_sources"]["finance"], "same_sku_history")
        self.assertEqual(items["POST-2"]["cost_sources"]["ads"], "period_sales_allocation")

        overview = center.overview(store_id="all", date_from="2026-07-01", date_to="2026-07-01")
        self.assertEqual(overview["summary"]["ozon_fees"], "30.00")
        self.assertEqual(overview["summary"]["logistics"], "60.00")
        self.assertEqual(overview["summary"]["ad_spend"], "30.00")
        self.assertEqual(overview["summary"]["expected_profit"], "120.00")

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
