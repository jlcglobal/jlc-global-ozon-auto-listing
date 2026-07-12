import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import importlib.util
from fastapi import HTTPException

from scripts.validate_product import ROOT, can_start_upload, validate_collector_product, validate_schema


APP_PATH = ROOT / "collector" / "local-ingest" / "app.py"
SPEC = importlib.util.spec_from_file_location("local_ingest_app", APP_PATH)
local_ingest_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_ingest_app)


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
    b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


def sample_payload():
    return {
        "source_platform": "1688",
        "source_url": "https://detail.1688.com/offer/123456789.html",
        "captured_at": "2026-07-10T12:00:00+08:00",
        "page_title": "测试商品 - 1688",
        "title_cn": "测试商品标题",
        "supplier_name": "测试供应商",
        "product_attributes": [
            {
                "name_cn": "材质",
                "value_cn": "unknown",
                "source": "candidate_selector",
                "source_text": "材质：页面未展示"
            }
        ],
        "price_information": {
            "currency": "CNY",
            "price_ranges": [
                {
                    "min_quantity": 2,
                    "price_cny": 12.5,
                    "raw_text": "2件起 ¥12.50"
                }
            ],
            "raw_text": "2件起 ¥12.50"
        },
        "minimum_order_quantity": {
            "value": 2,
            "unit": "件",
            "raw_text": "2件起批"
        },
        "main_images": [
            {"url": "https://img.example.com/main-a.png", "source_order": 0},
            {"url": "https://img.example.com/main-a.png", "source_order": 1}
        ],
        "detail_images": [
            {"url": "https://img.example.com/detail-a.png", "source_order": 0}
        ],
        "skus": [
            {
                "sku_id": "sku-1",
                "sku_name": "红色",
                "option_values": [
                    {
                        "name_cn": "颜色",
                        "value_cn": "红色",
                        "source": "script_init_data",
                        "source_text": "颜色: 红色"
                    }
                ],
                "purchase_price": 12.5,
                "image_url": "https://img.example.com/sku-a.png",
                "availability": "unknown",
                "source_data": {"skuId": "sku-1"}
            }
        ],
        "field_diagnostics": [
            {
                "field": "title_cn",
                "strategy": "candidate_selector",
                "hit": True,
                "failure_reason": "unknown",
                "candidate_count": 1
            }
        ],
        "capture_warnings": [],
        "raw_snapshot": {
            "structured_data_summary": {"script_count": 3},
            "candidate_selectors": {"title": ["h1"]},
            "all_raw_skus": [
                {
                    "sku_id": "sku-1",
                    "sku_name": "红色",
                    "purchase_price": 12.5,
                    "image_url": "https://img.example.com/sku-a.png",
                    "availability": "unknown"
                }
            ],
            "sku_selection_time": "2026-07-10T12:01:00+08:00"
        },
        "selected_sku_ids": ["sku-1"],
        "sku_selection": {
            "original_sku_count": 1,
            "available_sku_count": 1,
            "selected_sku_count": 1,
            "unselected_sku_count": 0,
            "selected_sku_ids": ["sku-1"],
            "selected_at": "2026-07-10T12:01:00+08:00"
        },
        "plugin_version": "0.2.0"
        ,"ozon_category_selection": {
            "category_id": 17027907,
            "type_id": 94462,
            "selected_at": "2026-07-10T12:01:00+08:00",
            "rules_snapshot": {
                "schema_version": "1.0.0",
                "category_id": 17027907,
                "type_id": 94462,
                "category_path": ["Дом и сад", "Аксессуары для приготовления пищи", "Точилка для ножей, ножниц"],
                "category_name_ru": "Точилка для ножей, ножниц",
                "shop_id": "zhonglian1",
                "rules_source": "ozon_seller_api_cache",
                "rules_snapshot_hash": "ed7bf447f109bfd94284e1ec73fccb5d03f7dab3008b2611b8af2257e65d5888",
                "required_attribute_ids": [85],
                "aspect_attribute_ids": [10096],
                "attributes": [
                    {"attribute_id": 85, "attribute_name": "Бренд", "required": True, "is_aspect": False},
                    {"attribute_id": 10096, "attribute_name": "Цвет товара", "required": False, "is_aspect": True}
                ]
            }
        }
    }


def many_sku_payload(total=12, selected=10):
    payload = sample_payload()
    all_skus = []
    selected_skus = []
    for idx in range(total):
        sku = {
            "sku_id": f"sku-{idx + 1}",
            "sku_name": f"黑色 / {idx + 30}cm",
            "option_values": [
                {
                    "name_cn": "颜色",
                    "value_cn": "黑色",
                    "source": "script_init_data",
                    "source_text": "颜色: 黑色"
                },
                {
                    "name_cn": "尺寸",
                    "value_cn": f"{idx + 30}cm",
                    "source": "script_init_data",
                    "source_text": f"尺寸: {idx + 30}cm"
                }
            ],
            "purchase_price": 10 + idx,
            "image_url": f"https://img.example.com/sku-{idx + 1}.png",
            "availability": "unknown",
            "source_data": {"skuId": f"sku-{idx + 1}", "index": idx}
        }
        all_skus.append(copy.deepcopy(sku))
        if idx < selected:
            selected_skus.append({**copy.deepcopy(sku), "selection_order": idx + 1})
    payload["skus"] = selected_skus
    payload["selected_sku_ids"] = [sku["sku_id"] for sku in selected_skus]
    payload["raw_snapshot"]["all_raw_skus"] = all_skus
    payload["sku_selection"] = {
        "original_sku_count": total,
        "available_sku_count": total,
        "selected_sku_count": selected,
        "unselected_sku_count": total - selected,
        "selected_sku_ids": payload["selected_sku_ids"],
        "selected_at": "2026-07-10T12:01:00+08:00"
    }
    return payload


class Stage2CollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.products_dir = Path(self.tmp.name) / "products"
        self.products_dir.mkdir()
        self.products_patch = patch.object(local_ingest_app, "PRODUCTS_DIR", self.products_dir)
        self.products_patch.start()

    def tearDown(self):
        self.products_patch.stop()
        self.tmp.cleanup()

    def fake_download(self, url, timeout=20):
        if "fail" in url:
            raise OSError("network down")
        if "detail-a" in url:
            return PNG_BYTES + b"detail", "image/png"
        return PNG_BYTES, "image/png"

    def test_normal_product_data_write(self):
        with patch.object(local_ingest_app, "download_url", self.fake_download):
            result = local_ingest_app.ingest_capture(sample_payload())
        product_dir = self.products_dir / result["product_id"]
        self.assertEqual(result["status"], "COLLECTED")
        self.assertTrue((product_dir / "input/source.json").is_file())
        self.assertTrue((product_dir / "input/raw-snapshot.json").is_file())
        self.assertEqual(validate_schema(product_dir / "input/source.json", ROOT / "templates/source.schema.json"), [])
        self.assertEqual(validate_schema(product_dir / "status.json", ROOT / "templates/status.schema.json"), [])
        self.assertEqual(validate_collector_product(product_dir), [])
        source = json.loads((product_dir / "input/source.json").read_text(encoding="utf-8"))
        self.assertEqual(source["title_cn"], "测试商品标题")
        self.assertEqual(len(source["product_attributes"]), 1)
        self.assertEqual(len(source["skus"]), 1)
        self.assertEqual(source["skus"][0]["price"], 12.5)
        self.assertEqual(source["skus"][0]["price_source"], "sku_specific_price")
        self.assertFalse(source["skus"][0]["sku_image_missing"])

    def test_missing_fields_are_unknown_or_empty_with_warnings(self):
        payload = sample_payload()
        payload["title_cn"] = None
        payload["supplier_name"] = None
        payload["product_attributes"] = []
        payload["capture_warnings"] = ["title_cn missing"]
        with patch.object(local_ingest_app, "download_url", self.fake_download):
            result = local_ingest_app.ingest_capture(payload)
        source = json.loads((self.products_dir / result["product_id"] / "input/source.json").read_text(encoding="utf-8"))
        self.assertEqual(source["title_cn"], "unknown")
        self.assertEqual(source["supplier_name"], "unknown")
        self.assertIn("title_cn missing", source["capture_warnings"])

    def test_product_id_generation_increments(self):
        with patch.object(local_ingest_app, "download_url", self.fake_download):
            first = local_ingest_app.ingest_capture(sample_payload())
            second = local_ingest_app.ingest_capture({**sample_payload(), "source_url": "https://detail.1688.com/offer/2.html"})
        self.assertEqual(first["product_id"], "P000001")
        self.assertEqual(second["product_id"], "P000002")

    def test_duplicate_url_creates_new_capture_version(self):
        with patch.object(local_ingest_app, "download_url", self.fake_download):
            first = local_ingest_app.ingest_capture(sample_payload())
            with self.assertRaises(HTTPException) as duplicate_error:
                local_ingest_app.ingest_capture(sample_payload())
            payload = sample_payload()
            payload["allow_new_version"] = True
            second = local_ingest_app.ingest_capture(payload)
        self.assertEqual(duplicate_error.exception.status_code, 409)
        self.assertNotEqual(first["product_id"], second["product_id"])
        self.assertEqual(second["duplicate_of"], first["product_id"])
        self.assertTrue(any("user chose to create a new capture version" in item for item in second["warnings"]))

    def test_image_url_and_hash_dedup(self):
        with patch.object(local_ingest_app, "download_url", self.fake_download):
            result = local_ingest_app.ingest_capture(sample_payload())
        source = json.loads((self.products_dir / result["product_id"] / "input/source.json").read_text(encoding="utf-8"))
        self.assertEqual(source["main_images"][1]["download_status"], "skipped_duplicate_url")
        self.assertEqual(source["skus"][0]["local_image_path"], source["main_images"][0]["local_path"])

    def test_image_download_failure_records_warning_without_crash(self):
        payload = sample_payload()
        payload["detail_images"] = [{"url": "https://img.example.com/fail.png", "source_order": 0}]
        with patch.object(local_ingest_app, "download_url", self.fake_download):
            result = local_ingest_app.ingest_capture(payload)
        source = json.loads((self.products_dir / result["product_id"] / "input/source.json").read_text(encoding="utf-8"))
        self.assertEqual(source["detail_images"][0]["download_status"], "failed")
        self.assertTrue(any("Image download failed" in item for item in source["capture_warnings"]))

    def test_missing_sku_image_is_marked_and_not_downloaded(self):
        payload = sample_payload()
        payload["skus"][0]["image_url"] = "unknown"
        payload["skus"][0]["sku_image_missing"] = True
        payload["raw_snapshot"]["all_raw_skus"][0]["image_url"] = "unknown"
        payload["raw_snapshot"]["all_raw_skus"][0]["sku_image_missing"] = True
        with patch.object(local_ingest_app, "download_url", self.fake_download):
            result = local_ingest_app.ingest_capture(payload)
        product_dir = self.products_dir / result["product_id"]
        source = json.loads((product_dir / "input/source.json").read_text(encoding="utf-8"))
        raw_snapshot = json.loads((product_dir / "input/raw-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(source["skus"][0]["image_url"], "unknown")
        self.assertEqual(source["skus"][0]["local_image_path"], "unknown")
        self.assertTrue(source["skus"][0]["sku_image_missing"])
        self.assertEqual(list((product_dir / "input/sku-images").glob("*")), [])
        self.assertEqual(raw_snapshot["sku_debug"]["sku_with_image"], 0)
        self.assertEqual(raw_snapshot["sku_debug"]["missing_image_skus"], ["sku-1"])

    def test_selected_skus_only_are_saved_and_raw_snapshot_keeps_all_skus(self):
        payload = many_sku_payload(total=12, selected=3)
        with patch.object(local_ingest_app, "download_url", self.fake_download):
            result = local_ingest_app.ingest_capture(payload)
        product_dir = self.products_dir / result["product_id"]
        source = json.loads((product_dir / "input/source.json").read_text(encoding="utf-8"))
        raw_snapshot = json.loads((product_dir / "input/raw-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(len(source["skus"]), 3)
        self.assertEqual([sku["selection_order"] for sku in source["skus"]], [1, 2, 3])
        self.assertEqual(raw_snapshot["sku_selection"]["original_sku_count"], 12)
        self.assertEqual(raw_snapshot["sku_selection"]["selected_sku_count"], 3)
        self.assertEqual(len(raw_snapshot["sku_raw_data"]), 12)
        self.assertEqual(raw_snapshot["sku_debug"]["total_skus"], 12)
        self.assertEqual(raw_snapshot["sku_debug"]["sku_with_price"], 12)
        sku_files = list((product_dir / "input/sku-images").glob("*"))
        self.assertLessEqual(len([path for path in sku_files if path.is_file()]), 3)

    def test_zero_selected_skus_is_rejected(self):
        payload = many_sku_payload(total=12, selected=0)
        with self.assertRaises(HTTPException) as err:
            local_ingest_app.ingest_capture(payload)
        self.assertEqual(err.exception.status_code, 422)
        self.assertIn("请至少选择1个SKU", str(err.exception.detail))

    def test_more_than_ten_selected_skus_is_rejected(self):
        payload = many_sku_payload(total=12, selected=11)
        with self.assertRaises(HTTPException) as err:
            local_ingest_app.ingest_capture(payload)
        self.assertEqual(err.exception.status_code, 422)
        self.assertIn("最多选择10个SKU", str(err.exception.detail))

    def test_out_of_stock_selected_sku_is_rejected(self):
        payload = many_sku_payload(total=2, selected=1)
        payload["skus"][0]["availability"] = "out_of_stock"
        with self.assertRaises(HTTPException) as err:
            local_ingest_app.ingest_capture(payload)
        self.assertEqual(err.exception.status_code, 422)
        self.assertIn("无库存", str(err.exception.detail))

    def test_generated_sku_id_is_rejected(self):
        payload = sample_payload()
        payload["skus"][0]["sku_id"] = "script-sku-1"
        payload["selected_sku_ids"] = ["script-sku-1"]
        payload["raw_snapshot"]["all_raw_skus"][0]["sku_id"] = "script-sku-1"
        with self.assertRaises(HTTPException) as err:
            local_ingest_app.ingest_capture(payload)
        self.assertEqual(err.exception.status_code, 422)
        self.assertIn("真实1688 sku_id", str(err.exception.detail))

    def test_shared_price_tier_matching_moq_is_applied_to_all_skus(self):
        payload = many_sku_payload(total=3, selected=3)
        for sku in payload["skus"]:
            sku["purchase_price"] = None
            sku["price_source"] = "price_range"
        payload["minimum_order_quantity"] = {"value": 1, "unit": "个", "raw_text": "1个起批"}
        payload["price_information"] = {
            "currency": "CNY",
            "price_ranges": [{"min_quantity": None, "price_cny": 9.5, "raw_text": "¥9.50 1个起批 ¥9.00 200-7999个 ¥8.00 ≥8000个"}],
            "raw_text": "¥9.50 1个起批 ¥9.00 200-7999个 ¥8.00 ≥8000个",
        }
        price = local_ingest_app.apply_shared_price_tier(payload)
        self.assertEqual(price, 9.5)
        self.assertEqual({sku["purchase_price"] for sku in payload["skus"]}, {9.5})
        self.assertEqual({sku["price_source"] for sku in payload["skus"]}, {"price_range"})

    def test_shared_price_tier_does_not_override_any_sku_specific_price(self):
        payload = many_sku_payload(total=2, selected=2)
        payload["skus"][0]["purchase_price"] = 12.5
        payload["skus"][1]["purchase_price"] = None
        payload["minimum_order_quantity"] = {"value": 1, "unit": "个", "raw_text": "1个起批"}
        payload["price_information"]["raw_text"] = "¥9.50 1个起批"
        self.assertIsNone(local_ingest_app.apply_shared_price_tier(payload))
        self.assertEqual(payload["skus"][0]["purchase_price"], 12.5)
        self.assertIsNone(payload["skus"][1]["purchase_price"])

    def test_status_flow_collected_waits_for_batch_run(self):
        with patch.object(local_ingest_app, "download_url", self.fake_download):
            result = local_ingest_app.ingest_capture(sample_payload())
        status = json.loads((self.products_dir / result["product_id"] / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["history"][0]["to"], "COLLECTING")
        self.assertEqual(status["history"][1]["to"], "COLLECTED")
        self.assertNotIn("review", status)
        self.assertFalse(status["task_authorized"])
        self.assertEqual(status["next_action"], "wait_for_run_task")
        self.assertFalse(can_start_upload(status))

    def test_no_openai_api_client_in_project_code(self):
        matches = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or any(part in {".git", "__pycache__"} for part in path.parts):
                continue
            if path.suffix not in {".py", ".js", ".json", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            forbidden = (
                "import " + "openai",
                "from " + "openai",
                "Open" + "AI(",
                "api." + "openai",
                "OPENAI" + "_API_KEY"
            )
            for needle in forbidden:
                if needle in text and path.name not in {"README.md", "AGENTS.md", "ozon-rules.md"}:
                    matches.append(f"{path}: {needle}")
        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
