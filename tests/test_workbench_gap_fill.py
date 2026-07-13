import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pipeline_runtime import create_batch  # noqa: E402
from store_publications import (  # noqa: E402
    final_snapshot, load_publications, publication_summary, reconcile_update_version,
    save_publications, select_stores, update_store_result,
)
from workbench_stores import (  # noqa: E402
    delete_store, list_stores, set_enabled, upsert_store, validate_store_read_only,
)

APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("gap_fill_workbench_app", APP_PATH)
workbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workbench)


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_root(root: Path) -> Path:
    write(root / "ozon-adapter/shops.json", {"schema_version": "1.0.0", "shops": []})
    product = root / "products/P000101"
    write(product / "input/source.json", {
        "product_id": "P000101", "title_cn": "测试商品", "source_url": "https://detail.1688.com/offer/101.html",
        "captured_at": "2026-07-12T08:00:00+08:00", "main_images": [{}], "detail_images": [],
        "skus": [
            {"sku_id": "sku-a", "sku_name": "黑色", "purchase_price": 10, "option_values": [{"name": "颜色", "value": "黑色"}]},
            {"sku_id": "sku-b", "sku_name": "白色", "purchase_price": 12, "option_values": [{"name": "颜色", "value": "白色"}]},
        ],
    })
    write(product / "status.json", {"product_id": "P000101", "status": "COLLECTED", "progress": 0, "steps": [], "ozon": {}, "api_write_count": 0})
    write(product / "output/copy-ru.json", {"title_ru": "Товар", "description_ru": "Описание", "keywords_ru": ["товар"]})
    write(product / "output/ozon-category.json", {"category_id": 1, "type_id": 2, "category_name": "Категория", "confidence": .92})
    write(product / "output/ozon-attributes.json", {"summary": {"required_count": 1, "mapped_count": 1}, "missing_required_attributes": [], "attributes": [{"attribute_id": 1, "attribute_name": "Бренд", "value": "Нет бренда", "required": True, "source": "default"}]})
    write(product / "output/pricing-result.json", {"exchange_rate": {"value": 12}, "sku_pricing": [{"sku_id": "sku-a", "purchase_cost_cny": 10, "base_cost_cny": 20, "selling_price_rub": 500, "estimated_profit_cny": 15, "profit_rate_markup": .5}, {"sku_id": "sku-b", "purchase_cost_cny": 12, "base_cost_cny": 22, "selling_price_rub": 560, "estimated_profit_cny": 18, "profit_rate_markup": .5}]})
    image = product / "output/images/main/main-001.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"old-image")
    write(product / "output/image-plan.json", {"main_images": [{"slot": "main-001", "image_type": "main", "output_path": "products/P000101/output/images/main/main-001.png"}], "detail_images": []})
    write(product / "output/image-qc-report.json", {"score": 88, "decision": "pass", "issues": []})
    return product


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class WorkbenchGapFillTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.product = make_root(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def add_store(self, store_id="shop-a"):
        return upsert_store(self.root, {"id": store_id, "display_name": store_id, "client_id": "client-secret", "api_key": "api-secret", "currency": "CNY"})

    def test_01_add_store_saves_registry_and_redacted_response(self):
        item = self.add_store()
        self.assertEqual(item["credentials_display"], "已配置")
        self.assertNotIn("api_key", item)
        self.assertNotIn("client_id", item)

    def test_02_store_secret_file_is_owner_only(self):
        self.add_store()
        mode = stat.S_IMODE((self.root / "ozon-adapter/.env.shop-a").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_03_edit_store_without_credentials_keeps_secret(self):
        self.add_store()
        upsert_store(self.root, {"display_name": "新名称", "notes": "备注"}, store_id="shop-a")
        self.assertEqual(list_stores(self.root)[0]["display_name"], "新名称")
        self.assertIn("api-secret", (self.root / "ozon-adapter/.env.shop-a").read_text())

    def test_04_editing_credentials_resets_validation(self):
        self.add_store()
        validate_store_read_only(self.root, "shop-a", transport=lambda endpoint, payload: {"result": []})
        item = upsert_store(self.root, {"api_key": "new-secret"}, store_id="shop-a")
        self.assertEqual(item["connection_status"], "unverified")

    def test_05_enable_and_disable_store(self):
        self.add_store()
        self.assertEqual(set_enabled(self.root, "shop-a", False)["connection_status"], "disabled")
        self.assertTrue(set_enabled(self.root, "shop-a", True)["enabled"])

    def test_06_delete_store_only_removes_local_config(self):
        self.add_store()
        delete_store(self.root, "shop-a")
        self.assertEqual(list_stores(self.root), [])
        self.assertTrue(self.product.is_dir())

    def test_07_read_only_validation_success(self):
        self.add_store()
        result = validate_store_read_only(self.root, "shop-a", transport=lambda endpoint, payload: {"result": []})
        self.assertEqual(result["connection_status"], "connected")
        self.assertEqual(result["ozon_write_api_calls"], 0)

    def test_08_read_only_validation_failure_is_saved_without_secret(self):
        self.add_store()
        result = validate_store_read_only(self.root, "shop-a", transport=lambda endpoint, payload: (_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual(result["connection_status"], "failed")
        self.assertNotIn("api-secret", json.dumps(result))

    def test_09_read_only_validation_inventory_calls_are_zero(self):
        self.add_store()
        result = validate_store_read_only(self.root, "shop-a", transport=lambda endpoint, payload: {"result": []})
        self.assertEqual(result["inventory_api_calls"], 0)

    def test_10_store_stats_count_publication_associations(self):
        self.add_store()
        select_stores(self.product, ["shop-a"], ["shop-a"])
        self.assertEqual(list_stores(self.root)[0]["associated_product_count"], 1)

    def test_11_publications_start_independent(self):
        data = load_publications(self.product, ["shop-a", "shop-b"])
        self.assertIsNot(data["stores"]["shop-a"], data["stores"]["shop-b"])

    def test_12_store_selection_is_saved(self):
        data = select_stores(self.product, ["shop-a"], ["shop-a", "shop-b"])
        self.assertTrue(data["stores"]["shop-a"]["selected"])
        self.assertFalse(data["stores"]["shop-b"]["selected"])

    def test_13_empty_store_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            select_stores(self.product, [], ["shop-a"])

    def test_14_unknown_store_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            select_stores(self.product, ["missing"], ["shop-a"])

    def test_15_store_price_override_does_not_change_master_pricing(self):
        select_stores(self.product, ["shop-a"], ["shop-a"], {"shop-a": {"sku_prices": {"sku-a": 999}}})
        pub = load_publications(self.product)["stores"]["shop-a"]
        self.assertEqual(pub["sku_publications"][0]["initial_price_rub"], 999)
        self.assertEqual(json.loads((self.product / "output/pricing-result.json").read_text())["sku_pricing"][0]["selling_price_rub"], 500)

    def test_16_task_and_product_ids_do_not_cross_stores(self):
        select_stores(self.product, ["shop-a", "shop-b"], ["shop-a", "shop-b"])
        update_store_result(self.product, "shop-a", "PENDING_REMOTE", [{"sku_id": "sku-a", "task_id": "task-a", "product_id": "product-a"}])
        update_store_result(self.product, "shop-b", "SUCCESS", [{"sku_id": "sku-a", "task_id": "task-b", "product_id": "product-b"}])
        stores = load_publications(self.product)["stores"]
        self.assertEqual(stores["shop-a"]["sku_publications"][0]["task_id"], "task-a")
        self.assertEqual(stores["shop-b"]["sku_publications"][0]["ozon_product_id"], "product-b")

    def test_17_publication_summary_mixed_results(self):
        data = load_publications(self.product, ["a", "b", "c"])
        data["stores"]["a"].update(selected=True, status="SUCCESS")
        data["stores"]["b"].update(selected=True, status="FAILED")
        data["stores"]["c"].update(selected=True, status="PENDING_REMOTE")
        self.assertEqual(publication_summary(data), {"selected": 3, "success": 1, "pending": 1, "failed": 1, "skipped": 0})

    def test_18_final_snapshot_excludes_inventory(self):
        snapshot = final_snapshot(self.product, ["shop-a"], "B-TEST")
        self.assertFalse(snapshot["inventory_fields_included"])

    def test_19_new_batch_defaults_to_manual_upload(self):
        batch = create_batch(self.root, ["P000101"], target_store_ids=["shop-a"])
        self.assertTrue(batch["manual_upload_required"])
        self.assertFalse(batch["auto_upload"])

    def test_20_auto_upload_is_current_batch_only(self):
        auto = create_batch(self.root, ["P000101"], target_store_ids=["shop-a"], auto_upload=True)
        manual = create_batch(self.root, ["P000101"], target_store_ids=["shop-a"])
        self.assertTrue(auto["auto_upload"])
        self.assertFalse(manual["auto_upload"])

    def test_21_batch_saves_target_stores(self):
        batch = create_batch(self.root, ["P000101"], target_store_ids=["shop-a", "shop-b"])
        self.assertEqual(batch["target_store_ids"], ["shop-a", "shop-b"])

    def test_22_product_store_override_is_independent(self):
        batch = create_batch(self.root, ["P000101"], target_store_ids=["a"], product_store_overrides={"P000101": ["b"]})
        self.assertEqual(batch["products"][0]["target_store_ids"], ["b"])

    def test_23_batch_deduplicates_store_ids(self):
        batch = create_batch(self.root, ["P000101"], target_store_ids=["a", "a"])
        self.assertEqual(batch["target_store_ids"], ["a"])

    def test_24_update_version_match_needs_no_write(self):
        self.assertEqual(reconcile_update_version({}, 3, 3)["action"], "MATCHED")

    def test_25_out_of_order_update_corrects_only_once(self):
        result = reconcile_update_version({}, 2, 3)
        self.assertEqual(result["action"], "UPDATE_LATEST_ONCE")
        self.assertEqual(result["version_correction_count"], 1)

    def test_26_second_version_mismatch_requires_manual_review(self):
        result = reconcile_update_version({"version_correction_count": 1}, 2, 3)
        self.assertTrue(result["manual_review"])
        self.assertNotEqual(result["action"], "CREATE")

    def test_27_existing_product_detail_opens_with_store_matrix(self):
        self.add_store()
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            detail = workbench.workbench_product_detail("P000101")
        self.assertIn("publications", detail)
        self.assertEqual(detail["stores"][0]["id"], "shop-a")

    def test_28_prelisting_score_uses_existing_results(self):
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            score = workbench.workbench_product_detail("P000101")["prelisting_assessment"]
        self.assertGreater(score["overall_score"], 0)
        self.assertTrue(score["pricing_advice"]["minimum_rules_respected"])

    def test_29_ai_suggestion_is_non_blocking(self):
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            suggestions = workbench.workbench_product_detail("P000101")["ai_suggestions"]
        self.assertTrue(suggestions[0]["non_blocking"])

    def test_30_search_supports_sku_and_source_url(self):
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            self.assertEqual(workbench.workbench_products(q="sku-b")["total"], 1)
            self.assertEqual(workbench.workbench_products(q="offer/101")["total"], 1)

    async def test_31_image_role_can_be_changed(self):
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            await workbench.update_workbench_image("P000101", "main-001", FakeRequest({"action": "set_role", "role": "detail"}))
        plan = json.loads((self.product / "output/image-plan.json").read_text())
        self.assertEqual(plan["detail_images"][0]["slot"], "main-001")

    async def test_32_image_order_can_move_without_regeneration(self):
        plan = json.loads((self.product / "output/image-plan.json").read_text())
        plan["detail_images"] = [{"slot": "detail-001", "output_path": "unknown"}]
        write(self.product / "output/image-plan.json", plan)
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            result = await workbench.update_workbench_image("P000101", "detail-001", FakeRequest({"action": "move", "direction": "up"}))
        self.assertTrue(result["saved"])

    async def test_32b_keep_image_records_local_learning(self):
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            result = await workbench.update_workbench_image("P000101", "main-001", FakeRequest({"action": "keep"}))
        self.assertTrue(result["learning"]["recorded"])
        self.assertTrue((self.root / "cache/image-feedback.json").is_file())
        plan = json.loads((self.product / "output/image-plan.json").read_text())
        self.assertEqual(plan["main_images"][0]["kept_by"], "studio-owner")

    async def test_33_image_content_replacement_writes_only_selected_slot(self):
        encoded = "data:image/png;base64," + __import__("base64").b64encode(b"new-image").decode()
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            result = await workbench.replace_workbench_image("P000101", "main-001", FakeRequest({"data_url": encoded}))
        self.assertEqual(result["bytes"], 9)
        self.assertEqual((self.product / "output/images/main/main-001.png").read_bytes(), b"new-image")

    def test_34_image_delete_never_calls_ozon_or_inventory(self):
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            result = workbench.delete_workbench_image("P000101", "main-001")
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)

    async def test_35_manual_field_edit_is_locked(self):
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            result = await workbench.save_workbench_draft("P000101", FakeRequest({"title_ru": "Новый"}))
        self.assertIn("title_ru", result["locked_fields"])

    async def test_36_tag_length_limit_is_enforced(self):
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            with self.assertRaises(workbench.HTTPException):
                await workbench.save_workbench_draft("P000101", FakeRequest({"tags": ["#" + "т" * 31]}))

    async def test_37_immutable_risk_rule_cannot_be_downgraded(self):
        with patch.object(workbench, "ROOT", self.root), patch.object(workbench, "PRODUCTS_DIR", self.root / "products"):
            with self.assertRaises(workbench.HTTPException):
                await workbench.update_workbench_risk_rule("inventory_api", FakeRequest({"action": "allow"}))

    def test_38_frontend_contains_real_store_batch_and_image_controls(self):
        html = (ROOT / "collector/local-ingest/static/workbench.html").read_text(encoding="utf-8")
        script = (ROOT / "collector/local-ingest/static/workbench.js").read_text(encoding="utf-8")
        for text in ("添加Ozon店铺", "创建商品批次", "自动模式已关闭"):
            self.assertIn(text, html)
        for text in ("上传至 ${state.selectedStoreIds.size} 家店铺", "店铺发布状态", "按店铺修改售价（可选）", "安全停止", "失败原因：", "上架成功：", "caf-listing-success:"):
            self.assertIn(text, script)

    def test_39_store_cny_price_override_is_saved_without_changing_master(self):
        select_stores(
            self.product, ["shop-a"], ["shop-a"],
            {"shop-a": {"sku_prices": {"sku-a": 720}, "sku_prices_cny": {"sku-a": 60}}},
        )
        publication = load_publications(self.product)["stores"]["shop-a"]["sku_publications"][0]
        self.assertEqual(publication["price_override_cny"], 60)
        self.assertEqual(publication["initial_price_rub"], 720)
        master = json.loads((self.product / "output/pricing-result.json").read_text())
        self.assertEqual(master["sku_pricing"][0]["selling_price_rub"], 500)

    async def test_40_global_auto_mode_defaults_off_and_can_be_enabled(self):
        with patch.object(workbench, "ROOT", self.root):
            self.assertFalse(workbench.workbench_settings()["auto_mode_enabled"])
            result = await workbench.update_workbench_settings(FakeRequest({"auto_mode_enabled": True}))
            self.assertTrue(result["auto_mode_enabled"])
            self.assertEqual(result["ozon_write_api_calls"], 0)
            self.assertEqual(result["inventory_api_calls"], 0)

    def test_41_failed_store_retry_targets_only_that_store(self):
        self.add_store("shop-a")
        data = select_stores(self.product, ["shop-a"], ["shop-a"])
        data["stores"]["shop-a"]["status"] = "FAILED"
        save_publications(self.product, data)
        with patch.object(workbench, "ROOT", self.root), \
             patch.object(workbench, "PRODUCTS_DIR", self.root / "products"), \
             patch.object(workbench, "running_batch_pid", return_value=None), \
             patch.object(workbench, "reserved_product_batches", return_value={}), \
             patch.object(workbench, "connected_store_ids", return_value=["shop-a"]), \
             patch.object(workbench, "launch_or_enqueue_batch", return_value={"status": "started", "pid": 123}):
            result = workbench.retry_failed_store("P000101", "shop-a")
        self.assertEqual(result["store_id"], "shop-a")
        self.assertEqual(result["write_api_calls"], 0)
        status = json.loads((self.product / "status.json").read_text())
        self.assertEqual(status["target_store_ids_for_run"], ["shop-a"])
        batch = json.loads((self.root / "batches" / result["batch_id"] / "batch.json").read_text())
        self.assertEqual(batch["target_store_ids"], ["shop-a"])


if __name__ == "__main__":
    unittest.main()
