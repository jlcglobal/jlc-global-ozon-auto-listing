import base64
import importlib.util
import json
import os
import re
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-field-completion"))
from ozon_field_completion.service import build_tags  # noqa: E402
APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("workbench_app", APP_PATH)
workbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workbench)


VALID_HASHTAGS = [
    "#органайзер", "#хранение", "#порядок", "#кухня", "#дом", "#контейнер",
    "#полка", "#шкаф", "#удобство", "#покупка", "#семья", "#быт",
    "#аккуратно", "#пространство", "#практично", "#ежедневно", "#компактно",
    "#выбор", "#товары", "#решение", "#польза", "#чистота", "#форма",
    "#посуда", "#еда", "#запасы", "#прозрачный", "#крышка", "#размер", "#набор",
]


def fake_category_selection(category_id: int = 10, type_id: int = 20) -> dict:
    return {
        "schema_version": "1.0.0",
        "selection_source": "user_final_choice",
        "selected_at": "2026-07-31T00:00:00+00:00",
        "category_id": category_id,
        "type_id": type_id,
        "category_name_ru": "Фигурки",
        "category_name_zh": "手办",
        "category_path": ["Дом", "Декор", "Фигурки"],
        "category_path_zh": ["家居", "装饰", "手办"],
        "category_label_source": "ozon_seller_api",
        "category_label_language": "ZH_HANS",
        "rules_snapshot": {
            "category_id": category_id,
            "type_id": type_id,
            "category_path": ["Дом", "Декор", "Фигурки"],
            "attributes": [
                {"attribute_id": 85, "attribute_name": "Бренд", "required": True, "is_aspect": False},
                {"attribute_id": 10096, "attribute_name": "Цвет товара", "required": False, "is_aspect": True},
            ],
            "required_attribute_ids": [85],
            "aspect_attribute_ids": [10096],
            "rules_snapshot_hash": "fake-rules-hash",
        },
        "rules_snapshot_hash": "fake-rules-hash",
        "locked_for_batch": True,
        "allow_runtime_rematch": False,
    }


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
        "product_id": product_id, "status": "NEEDS_ATTENTION", "current_step": "image_generation",
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
    image_path = product_dir / "output/generated-images/variant-main/main-001.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png")
    write_json(product_dir / "output/image-plan.json", {
        "main_images": [{"slot": "main-001", "image_type": "main", "prompt": "真实商品像素锁", "output_path": f"products/{product_id}/output/generated-images/variant-main/main-001.png"}],
        "detail_images": [{"slot": "detail-001", "image_type": "benefit", "prompt": "功能图", "output_path": f"products/{product_id}/output/generated-images/detail/detail-001.png"}],
    })
    write_json(product_dir / "output/image-qc-report.json", {
        "score": 73, "decision": "reject", "issues": [{"code": "simple_background", "message": "只是换背景", "image_slots": ["main-001"]}],
    })
    thumbnail = product_dir / "input/main-images/main-001.jpg"
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.write_bytes(b"jpg")
    return product_dir


def make_sku_table_product(root: Path, product_id: str = "P000777", sku_count: int = 10) -> Path:
    product_dir = root / "products" / product_id
    colors = ["красный", "черный", "синий", "зеленый", "белый", "серый", "желтый", "фиолетовый", "оранжевый", "бежевый"]
    skus = []
    product_attributes = [{"name_cn": "材质", "value_cn": "нержавеющая сталь", "source_text": "材质 нержавеющая сталь"}]
    for index in range(1, sku_count + 1):
        sku_id = f"SKU-{index:02d}"
        capacity = 400 + index * 100
        color = colors[(index - 1) % len(colors)]
        sku_name = f"{color} {capacity}ml"
        length = 10 + index
        width = 6 + index
        height = 8 + index
        weight = 200 + index * 25
        skus.append({
            "sku_id": sku_id,
            "sku_name": sku_name,
            "selected": True,
            "purchase_price": 10 + index,
            "option_values": [
                {"name_cn": "颜色", "value_cn": color},
                {"name_cn": "容量", "value_cn": f"{capacity}ml"},
                {"name_cn": "规格", "value_cn": f"модель {index:02d}"},
            ],
        })
        product_attributes.extend([
            {"name_cn": f"SKU尺寸 - {sku_name}", "value_cn": f"{length}cm×{width}cm×{height}cm", "source_text": f"SKU尺寸 - {sku_name} {length}cm×{width}cm×{height}cm"},
            {"name_cn": f"SKU重量 - {sku_name}", "value_cn": f"{weight}g", "source_text": f"SKU重量 - {sku_name} {weight}g"},
        ])
    write_json(product_dir / "input/source.json", {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "collection_id": "C-SKU-TABLE",
        "source_kind": "workbench_collection",
        "title_cn": "十SKU工作台测试商品",
        "source_url": "https://detail.1688.com/offer/777.html",
        "captured_at": "2026-07-18T10:00:00+08:00",
        "product_attributes": product_attributes,
        "skus": skus,
    })
    write_json(product_dir / "status.json", {
        "product_id": product_id, "status": "COLLECTED", "current_step": "collection",
        "progress": 10, "warnings": [], "steps": [], "ozon": {"upload_status": "not_started", "errors": []},
        "api_write_count": 0,
    })
    write_json(product_dir / "input/category-selection.json", {
        "category_id": 10, "type_id": 20, "category_name": "Категория",
    })
    write_json(product_dir / "output/ozon-category.json", {
        "category_id": 10, "type_id": 20, "category_name": "Категория", "confidence": 1,
    })
    write_json(product_dir / "output/ozon-category-attributes.json", {
        "category_id": 10,
        "type_id": 20,
        "attributes": [
            {"attribute_id": 10097, "attribute_name": "Название цвета", "type": "String", "required": True, "is_aspect": True},
            {"attribute_id": 3001, "attribute_name": "Объем, л", "type": "Decimal", "required": True, "is_aspect": True},
            {"attribute_id": 3101, "attribute_name": "Размер", "type": "String", "required": False, "is_aspect": True},
        ],
    })
    write_json(product_dir / "output/copy-ru.json", {
        "title_ru": "Тестовый товар", "short_title": "Товар", "description_ru": "Описание",
        "bullets_ru": [], "keywords_ru": VALID_HASHTAGS,
    })
    return product_dir


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class WorkbenchTest(unittest.IsolatedAsyncioTestCase):
    async def test_ozon_browser_snapshot_downloads_ozon_gallery_images_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloaded = []

            def fake_download_url(url, timeout=20, allowed_host_suffixes=None):
                downloaded.append((url, allowed_host_suffixes))
                raise AssertionError("inline Ozon browser images should not require backend network download")

            inline_a = "data:image/jpeg;base64," + base64.b64encode(b"browser-image-a").decode("ascii")
            inline_b = "data:image/jpeg;base64," + base64.b64encode(b"browser-image-b").decode("ascii")

            payload = {
                "plugin_version": "0.4.13",
                "source_url": "https://www.ozon.ru/product/test-figure-123/",
                "title": "Фигурка коллекционная",
                "length_mm": 120,
                "width_mm": 80,
                "height_mm": 60,
                "weight_g": 400,
                "selling_price_cny": 99,
                "ozon_category_selection": fake_category_selection(),
                "image_urls": [
                    "https://ir.ozone.ru/s3/multimedia-1-r/c600/111.jpg",
                    "https://st.ozone.ru/s3/ozon-fonts/onest.woff2",
                    "https://ir.ozone.ru/s3/cms/52/te7/wc1000/banner.jpg",
                    "https://ir.ozone.ru/s3/video-71/abc/cover/wc1000/cover.jpg",
                    "https://cdn1.ozonusercontent.com/s3/marketing-api/banners/F4/xY/wc1000/banner.jpg",
                    "https://ir.ozone.ru/s3/multimedia-1-n/wc1000/222.jpg",
                    "https://ir.ozone.ru/s3/multimedia-1-x/wc1000/333.jpg",
                ],
                "images": [
                    {"url": "https://ir.ozone.ru/s3/multimedia-1-r/c600/111.jpg", "data_url": inline_a, "content_type": "image/jpeg", "byte_size": 15},
                    {"url": "https://ir.ozone.ru/s3/multimedia-1-n/wc1000/222.jpg", "data_url": inline_b, "content_type": "image/jpeg", "byte_size": 15},
                    {"url": "https://ir.ozone.ru/s3/multimedia-1-x/wc1000/333.jpg", "source": "ozon_browser_dom"},
                ],
                "category_path": [],
                "page_text": "reference",
            }
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "build_selection", return_value=fake_category_selection()), \
                 patch.object(workbench, "download_url", side_effect=fake_download_url):
                result = await workbench.create_collector_ozon_reference_page(FakeRequest(payload))

            self.assertEqual(result["write_api_calls"], 0)
            self.assertEqual(result["inventory_api_calls"], 0)
            task = result["task"]
            self.assertEqual(task["captured_image_count"], 2)
            self.assertEqual(downloaded, [])
            capture = json.loads((root / task["capture_artifact_path"]).read_text(encoding="utf-8"))
            self.assertTrue(all(image["download_status"] == "downloaded" for image in capture["images"]))
            self.assertTrue(any("已跳过后端直连下载" in warning for warning in capture["warnings"]))
            self.assertFalse(any("ozon-fonts" in url or "/cms/" in url or "/video-" in url or "marketing-api" in url for url in capture["image_urls"]))
            raw_snapshot = json.loads((root / "runtime/ozon-reference-tasks" / task["task_id"] / "browser-snapshot.raw.json").read_text(encoding="utf-8"))
            self.assertTrue(raw_snapshot["images"][0]["has_inline_image_data"])
            self.assertNotIn("data_url", raw_snapshot["images"][0])

    async def test_ozon_reference_tasks_accept_multiple_links_without_ozon_or_inventory_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "text": "\n".join([
                    "https://www.ozon.ru/product/test-product-123/?from=share",
                    "ozon.ru/product/another-product-456",
                    "https://www.ozon.ru/product/test-product-123/?from=duplicate",
                ]),
                "length_mm": 120,
                "width_mm": 80,
                "height_mm": 60,
                "weight_g": 400,
                "selling_price_cny": 99,
                "ozon_category_selection": fake_category_selection(),
                "store_ids": ["store-a"],
            }
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "build_selection", return_value=fake_category_selection()), \
                 patch.object(workbench, "connected_store_ids", return_value=["store-a"]):
                result = await workbench.create_workbench_ozon_reference_tasks(FakeRequest(payload))
                listed = workbench.workbench_ozon_reference_tasks()

            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["created_count"], 2)
            self.assertEqual(result["duplicate_count"], 0)
            self.assertEqual(result["write_api_calls"], 0)
            self.assertEqual(result["inventory_api_calls"], 0)
            self.assertEqual(listed["total"], 2)
            self.assertTrue(all(item["inventory_submission_enabled"] is False for item in listed["items"]))
            self.assertTrue(all(item["write_api_calls"] == 0 for item in listed["items"]))
            self.assertTrue(all(item["inventory_api_calls"] == 0 for item in listed["items"]))
            self.assertEqual(
                sorted(item["source_url"] for item in listed["items"]),
                [
                    "https://ozon.ru/product/another-product-456",
                    "https://ozon.ru/product/test-product-123",
                ],
            )

    async def test_ozon_reference_tasks_reject_non_ozon_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "connected_store_ids", return_value=["store-a"]):
                with self.assertRaises(HTTPException) as raised:
                    await workbench.create_workbench_ozon_reference_tasks(FakeRequest({
                        "text": "https://example.com/product/1",
                        "store_ids": ["store-a"],
                    }))

            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn("只支持 Ozon 商品卡链接", str(raised.exception.detail))

    async def test_ozon_reference_tasks_keep_manual_inputs_per_link_only(self):
        html = """
        <html><head>
          <meta property="og:title" content="Товар">
          <meta property="og:image" content="https://cdn.ozon.ru/reference-a.jpg">
        </head></html>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category = fake_category_selection()
            payload = {
                "items": [
                    {
                        "url": "https://www.ozon.ru/product/first-111/",
                        "length_mm": 120,
                        "width_mm": 80,
                        "height_mm": 40,
                        "weight_g": 350,
                        "selling_price_cny": 49.9,
                        "ozon_category_selection": category,
                    },
                    {
                        "url": "https://www.ozon.ru/product/second-222/",
                        "length_mm": 220,
                        "width_mm": 180,
                        "height_mm": 90,
                        "weight_g": 950,
                        "selling_price_cny": 129,
                        "ozon_category_selection": category,
                    },
                ],
                "store_ids": ["store-a"],
            }
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "build_selection", return_value=category), \
                 patch.object(workbench, "connected_store_ids", return_value=["store-a"]), \
                 patch.object(workbench, "download_public_ozon_page", return_value=(html, "text/html; charset=utf-8")), \
                 patch.object(workbench, "download_url", return_value=(b"image-bytes", "image/jpeg")):
                created = await workbench.create_workbench_ozon_reference_tasks(FakeRequest(payload))
                processed = workbench.process_ozon_reference_tasks_once(limit=5)
                listed = workbench.workbench_ozon_reference_tasks()

            self.assertEqual(created["created_count"], 2)
            self.assertEqual(processed["processed_count"], 2)
            by_url = {item["source_url"]: item for item in listed["items"]}
            self.assertEqual(by_url["https://ozon.ru/product/first-111"]["status"], "waiting_ai_design")
            self.assertEqual(by_url["https://ozon.ru/product/second-222"]["display_status"], "等待AI生成商品卡")
            first = by_url["https://ozon.ru/product/first-111"]["manual_inputs"]
            second = by_url["https://ozon.ru/product/second-222"]["manual_inputs"]
            self.assertEqual(first["package_dimensions_mm"], {"length_mm": 120.0, "width_mm": 80.0, "height_mm": 40.0})
            self.assertEqual(first["package_weight_g"], 350.0)
            self.assertEqual(first["selling_price_cny"], 49.9)
            self.assertEqual(first["ozon_category_selection"]["category_id"], 10)
            self.assertEqual(second["package_dimensions_mm"], {"length_mm": 220.0, "width_mm": 180.0, "height_mm": 90.0})
            self.assertEqual(second["package_weight_g"], 950.0)
            self.assertEqual(second["selling_price_cny"], 129.0)
            first_brief = json.loads((root / by_url["https://ozon.ru/product/first-111"]["brief_artifact_path"]).read_text(encoding="utf-8"))
            second_brief = json.loads((root / by_url["https://ozon.ru/product/second-222"]["brief_artifact_path"]).read_text(encoding="utf-8"))
            first_generation = json.loads((root / by_url["https://ozon.ru/product/first-111"]["generation_artifact_path"]).read_text(encoding="utf-8"))
            first_designer = json.loads((root / by_url["https://ozon.ru/product/first-111"]["designer_input_artifact_path"]).read_text(encoding="utf-8"))
            first_ai_request = json.loads((root / by_url["https://ozon.ru/product/first-111"]["ai_design_request_artifact_path"]).read_text(encoding="utf-8"))
            self.assertEqual(first_brief["operator_inputs"]["selling_price_cny"], 49.9)
            self.assertEqual(second_brief["operator_inputs"]["selling_price_cny"], 129.0)
            self.assertTrue(first_generation["ready_for_ecommerce_design"])
            self.assertEqual(first_generation["missing_fields"], [])
            self.assertTrue(first_generation["generation_contract"]["reverse_reference_images_for_real_photo_prompt"])
            self.assertTrue(first_generation["generation_contract"]["do_not_submit_inventory"])
            self.assertTrue(first_designer["ready_for_ai_design"])
            self.assertEqual(first_designer["operator_inputs"]["ozon_category_selection"]["category_id"], 10)
            self.assertEqual(first_designer["attribute_contract"]["final_ozon_category"]["type_id"], 20)
            self.assertTrue(first_designer["copy_contract"]["must_be_own_listing"])
            self.assertEqual(first_designer["image_contract"]["reference_mode"], "visual_feel_only")
            self.assertIn("提交库存", first_designer["forbidden"])
            self.assertIn("Ozon Seller API create/update", first_ai_request["must_not_call"])
            self.assertIn("listing-design-draft.json", first_ai_request["expected_output_path"])
            self.assertIn("不得读取 products/", first_ai_request["prompt"])
            self.assertEqual(first_brief["write_api_calls"], 0)
            self.assertEqual(second_brief["inventory_api_calls"], 0)

    async def test_ozon_reference_task_uses_fitkun_inline_images_without_backend_download(self):
        html = """
        <html><head>
          <meta property="og:title" content="Фигурка для рабочего стола">
          <meta property="og:image" content="https://ir.ozone.ru/s3/multimedia-x/wc1000/server-only.jpg">
        </head></html>
        """
        inline_a = "data:image/jpeg;base64," + base64.b64encode(b"fitkun-image-a").decode("ascii")
        inline_b = "data:image/jpeg;base64," + base64.b64encode(b"fitkun-image-b").decode("ascii")
        downloaded = []

        def fake_download_url(url, timeout=20, allowed_host_suffixes=None):
            downloaded.append((url, allowed_host_suffixes))
            raise AssertionError("FITKUN inline images should not require backend Ozon image download")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category = fake_category_selection()
            payload = {
                "items": [{
                    "url": "https://www.ozon.ru/product/figure-fitkun-999/",
                    "length_mm": 120,
                    "width_mm": 80,
                    "height_mm": 60,
                    "weight_g": 400,
                    "selling_price_cny": 99,
                    "ozon_category_selection": category,
                    "fitkun_images": [
                        {"url": "https://ir.ozone.ru/s3/multimedia-fitkun/wc1000/a.jpg", "data_url": inline_a, "content_type": "image/jpeg", "byte_size": 14},
                        {"url": "https://ir.ozone.ru/s3/multimedia-fitkun/wc1000/b.jpg", "data_url": inline_b, "content_type": "image/jpeg", "byte_size": 14},
                    ],
                }],
                "store_ids": ["store-a"],
            }
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "PRODUCTS_DIR", root / "products"), \
                 patch.object(workbench, "build_selection", return_value=category), \
                 patch.object(workbench, "connected_store_ids", return_value=["store-a"]), \
                 patch.object(workbench, "download_public_ozon_page", return_value=(html, "text/html; charset=utf-8")), \
                 patch.object(workbench, "download_url", side_effect=fake_download_url):
                created = await workbench.create_workbench_ozon_reference_tasks(FakeRequest(payload))
                processed = workbench.process_ozon_reference_tasks_once(limit=5)
                listed = workbench.workbench_ozon_reference_tasks()

            self.assertEqual(created["write_api_calls"], 0)
            self.assertEqual(created["inventory_api_calls"], 0)
            self.assertEqual(created["items"][0]["fitkun_image_count"], 2)
            self.assertEqual(processed["processed_count"], 1)
            self.assertEqual(processed["failed_count"], 0)
            self.assertEqual(downloaded, [])
            self.assertEqual(listed["items"][0]["captured_image_count"], 2)
            capture = json.loads((root / listed["items"][0]["capture_artifact_path"]).read_text(encoding="utf-8"))
            self.assertEqual([image["download_status"] for image in capture["images"]], ["downloaded", "downloaded"])
            self.assertTrue(any("FITKUN" in warning and "已跳过后端直连下载" in warning for warning in capture["warnings"]))
            self.assertEqual(capture["fact_policy"]["ozon_api_write_calls"], 0)
            self.assertEqual(capture["fact_policy"]["inventory_api_calls"], 0)

    async def test_ozon_reference_task_can_import_fitkun_images_after_creation(self):
        inline_image = "data:image/jpeg;base64," + base64.b64encode(b"fitkun-late-image").decode("ascii")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category = fake_category_selection()
            payload = {
                "items": [{
                    "url": "https://www.ozon.ru/product/figure-late-fitkun-1001/",
                    "length_mm": 120,
                    "width_mm": 80,
                    "height_mm": 60,
                    "weight_g": 400,
                    "selling_price_cny": 99,
                    "ozon_category_selection": category,
                }],
                "store_ids": ["store-a"],
            }
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "PRODUCTS_DIR", root / "products"), \
                 patch.object(workbench, "build_selection", return_value=category), \
                 patch.object(workbench, "connected_store_ids", return_value=["store-a"]):
                created = await workbench.create_workbench_ozon_reference_tasks(FakeRequest(payload))
                task_id = created["items"][0]["task_id"]
                imported = await workbench.import_workbench_ozon_reference_fitkun_images(task_id, FakeRequest({
                    "fitkun_images": [{
                        "url": "https://ir.ozone.ru/s3/multimedia-fitkun/wc1000/late.jpg",
                        "data_url": inline_image,
                        "content_type": "image/jpeg",
                        "byte_size": 17,
                    }]
                }))
                listed = workbench.workbench_ozon_reference_tasks()

            self.assertEqual(imported["status"], "imported")
            self.assertEqual(imported["imported_count"], 1)
            self.assertEqual(imported["total_fitkun_image_count"], 1)
            self.assertEqual(imported["task"]["fitkun_image_count"], 1)
            self.assertEqual(imported["write_api_calls"], 0)
            self.assertEqual(imported["inventory_api_calls"], 0)
            self.assertEqual(listed["items"][0]["fitkun_image_count"], 1)

    async def test_ozon_reference_ai_design_generates_listing_draft_without_write_calls(self):
        html = """
        <html><head>
          <meta property="og:title" content="Фигурка для рабочего стола">
          <meta name="description" content="Коллекционная фигурка для декора и подарка">
          <meta property="og:image" content="https://cdn.ozon.ru/reference-a.jpg">
        </head></html>
        """

        def fake_ai_runner(task_dir: Path, request: dict) -> None:
            workbench.atomic_write_json(task_dir / "listing-design-draft.json", {
                "schema_version": "1.0.0",
                "task_id": request["task_id"],
                "source_kind": "ozon_reference_listing",
                "source_url": request["source_url"],
                "generated_at": "2026-07-31T00:00:00+00:00",
                "mode": "create_without_inventory",
                "inventory_submission_enabled": False,
                "own_listing_copy_ru": {
                    "seo_title_ru": "Фигурка для рабочего стола декоративная коллекционная для подарка и интерьера",
                    "short_title_ru": "Фигурка декоративная",
                    "description_ru": (
                        "Декоративная фигурка подходит для рабочего стола, полки или зоны коллекции. "
                        "Она помогает добавить аккуратный визуальный акцент в интерьер и подходит для подарка. "
                        "Перед покупкой проверьте размеры и место установки."
                    ),
                    "selling_points_ru": ["Для декора", "Для подарка", "Для коллекции"],
                    "hashtags_ru": ["#фигурка", "#декор", "#подарок"],
                },
                "attribute_draft": {"fillable": [], "estimated": [], "unknown": []},
                "visual_reference_analysis": {"camera_feel": "real handheld product photo"},
                "image_prompt_plan": [
                    {
                        "image_role": "main",
                        "visual_goal": "Показать товар как реальную фотографию продавца",
                        "shot_type": "close product photo",
                        "prompt": "real handheld phone product photo on desktop, natural light, shallow depth of field",
                    }
                ],
                "risks": [],
                "next_action": "等待生成图片和正式商品卡编译",
                "write_api_calls": 0,
                "inventory_api_calls": 0,
            })

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category = fake_category_selection()
            payload = {
                "items": [{
                    "url": "https://www.ozon.ru/product/figure-999/",
                    "length_mm": 120,
                    "width_mm": 80,
                    "height_mm": 60,
                    "weight_g": 400,
                    "selling_price_cny": 99,
                    "ozon_category_selection": category,
                }],
                "store_ids": ["store-a"],
            }
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "PRODUCTS_DIR", root / "products"), \
                 patch.object(workbench, "build_selection", return_value=category), \
                 patch.object(workbench, "connected_store_ids", return_value=["store-a"]), \
                 patch.object(workbench, "download_public_ozon_page", return_value=(html, "text/html; charset=utf-8")), \
                 patch.object(workbench, "download_url", return_value=(b"image-bytes", "image/jpeg")), \
                 patch.object(workbench, "run_ozon_reference_codex_design", side_effect=fake_ai_runner):
                await workbench.create_workbench_ozon_reference_tasks(FakeRequest(payload))
                captured = workbench.process_ozon_reference_tasks_once(limit=5)
                generated = workbench.process_ozon_reference_ai_design_once(limit=5)
                listed = workbench.workbench_ozon_reference_tasks()

            self.assertEqual(captured["processed_count"], 1)
            self.assertEqual(generated["processed_count"], 1)
            self.assertEqual(generated["failed_count"], 0)
            self.assertEqual(generated["write_api_calls"], 0)
            self.assertEqual(generated["inventory_api_calls"], 0)
            self.assertEqual(listed["items"][0]["status"], "listing_draft_ready")
            self.assertEqual(listed["items"][0]["display_status"], "商品卡草稿已生成")
            draft_path = root / listed["items"][0]["listing_draft_artifact_path"]
            self.assertTrue(draft_path.is_file())
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            self.assertEqual(draft["write_api_calls"], 0)
            self.assertEqual(draft["inventory_api_calls"], 0)
            self.assertEqual(draft["own_listing_copy_ru"]["hashtags_ru"], ["#фигурка", "#декор", "#подарок"])
            created_product_id = listed["items"][0]["created_product_id"]
            self.assertRegex(created_product_id, r"^P[0-9]{6}$")
            product_dir = root / "products" / created_product_id
            self.assertTrue(product_dir.is_dir())
            source = json.loads((product_dir / "input/source.json").read_text(encoding="utf-8"))
            status = json.loads((product_dir / "status.json").read_text(encoding="utf-8"))
            category_file = json.loads((product_dir / "input/category-selection.json").read_text(encoding="utf-8"))
            output_category = json.loads((product_dir / "output/ozon-category.json").read_text(encoding="utf-8"))
            category_attributes = json.loads((product_dir / "output/ozon-category-attributes.json").read_text(encoding="utf-8"))
            tags = json.loads((product_dir / "output/ozon-tags.json").read_text(encoding="utf-8"))
            title = json.loads((product_dir / "output/title-ru.json").read_text(encoding="utf-8"))
            self.assertEqual(source["source_kind"], "ozon_reference_draft")
            self.assertEqual(source["source_platform"], "ozon_reference")
            self.assertEqual(category_file["category_id"], 10)
            self.assertEqual(output_category["match_status"], "api_confirmed")
            self.assertEqual(category_attributes["required_attribute_ids"], [85])
            self.assertEqual(status["status"], "OZON_REFERENCE_DRAFT")
            self.assertEqual(status["api_write_count"], 0)
            self.assertEqual(tags["tags"], ["#фигурка", "#декор", "#подарок"])
            self.assertIn("Фигурка", title["title_ru"])
            self.assertTrue(any((product_dir / "input/main-images").glob("*")))
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                product_list = workbench.workbench_products()
            self.assertIn(created_product_id, {item["product_id"] for item in product_list["items"]})
            self.assertEqual(product_list["items"][0]["thumbnail_url"], f"/api/inbox/products/{created_product_id}/thumbnail")
            with self.assertRaises(ValueError) as blocked:
                workbench.create_batch(root, [created_product_id], target_store_ids=["store-a"], auto_upload=True)
            self.assertIn("not a 1688 capture", str(blocked.exception))

    async def test_ozon_reference_draft_run_uses_reference_image_pipeline_not_formal_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P000086"
            (product_dir / "input/main-images").mkdir(parents=True, exist_ok=True)
            (product_dir / "input/detail-images").mkdir(parents=True, exist_ok=True)
            (product_dir / "output").mkdir(parents=True, exist_ok=True)
            write_json(product_dir / "input/source.json", {
                "schema_version": "1.0.0",
                "product_id": "P000086",
                "collection_id": "COL-OZONREF-TEST",
                "source_kind": "ozon_reference_draft",
                "source_platform": "ozon_reference",
                "source_url": "https://www.ozon.ru/product/test-1/",
                "title_cn": "Ozon参考草稿：测试商品",
                "main_images": [],
                "detail_images": [],
                "skus": [{"sku_id": "ozon-reference-sku-1", "sku_name": "测试商品"}],
            })
            write_json(product_dir / "status.json", {
                "schema_version": "1.0.0",
                "product_id": "P000086",
                "status": "OZON_REFERENCE_DRAFT",
                "current_step": "ozon_reference_listing_draft",
                "progress": 35,
                "ozon": {"upload_status": "not_started", "task_id": "unknown"},
                "api_write_count": 0,
            })
            write_json(product_dir / "output/image-plan.json", {
                "schema_version": "1.0.0",
                "product_id": "P000086",
                "source_kind": "ozon_reference_draft",
                "main_images": [{"slot": "main-001", "prompt": "real photo", "output_path": "unknown"}],
                "detail_images": [],
            })
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "PRODUCTS_DIR", root / "products"), \
                 patch.object(workbench, "validate_formal_product_input", side_effect=AssertionError("formal gate must not run for Ozon reference drafts")), \
                 patch.object(workbench, "launch_ozon_reference_image_generation", return_value={
                     "status": "queued",
                     "message": "已开始Ozon参考实拍风生图",
                     "write_api_calls": 0,
                     "inventory_api_calls": 0,
                 }) as launched:
                result = await workbench.run_single_workbench_product("P000086", FakeRequest({}))

            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["batch_id"], "ozon_reference_image_generation")
            self.assertEqual(result["write_api_calls"], 0)
            self.assertEqual(result["inventory_api_calls"], 0)
            launched.assert_called_once_with(product_dir)

    def test_ozon_reference_readiness_does_not_show_formal_input_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P000086"
            (product_dir / "input").mkdir(parents=True, exist_ok=True)
            (product_dir / "output").mkdir(parents=True, exist_ok=True)
            write_json(product_dir / "input/source.json", {
                "schema_version": "1.0.0",
                "product_id": "P000086",
                "source_kind": "ozon_reference_draft",
            })
            status = {
                "status": "OZON_REFERENCE_IMAGES_PARTIAL",
                "current_step": "ozon_reference_image_generation",
                "progress": 62,
            }
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "validate_formal_product_input", side_effect=AssertionError("formal gate must not run")):
                readiness = workbench.production_readiness_state(product_dir, status, {"main_images": [{}]})
            self.assertFalse(readiness["blocking"])
            self.assertTrue(readiness["formal_input_valid"])
            self.assertEqual(readiness["state"], "ozon_reference_images_partial")
            self.assertIn("点击继续", readiness["message"])

    def test_ozon_reference_ui_state_drives_smart_command_center_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P000086"
            (product_dir / "input").mkdir(parents=True, exist_ok=True)
            write_json(product_dir / "input/source.json", {
                "schema_version": "1.0.0",
                "product_id": "P000086",
                "source_kind": "ozon_reference_draft",
            })
            status = {
                "status": "OZON_REFERENCE_IMAGES_GENERATED",
                "current_step": "ozon_reference_image_generation",
                "progress": 70,
            }
            readiness = workbench.production_readiness_state(product_dir, status, {"main_images": [{}]})
            ui_state = workbench.product_ui_state(
                product_dir,
                status,
                readiness,
                {"level": "low", "items": []},
                [{"url": "/api/workbench/products/P000086/assets/generated-images/detail-001.png"}],
            )
            self.assertEqual(ui_state["kind"], "ozon_reference")
            self.assertEqual(ui_state["state"], "ozon_reference_images_generated")
            self.assertEqual(ui_state["tone"], "ok")
            self.assertFalse(ui_state["blocking"])
            self.assertEqual(ui_state["primary_action"]["id"], "view_details")
            self.assertIn("没有提交 Ozon", ui_state["message"])

    def test_ozon_reference_image_timeout_keeps_partial_report_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P000086"
            (product_dir / "input/main-images").mkdir(parents=True, exist_ok=True)
            (product_dir / "output/generated-images/variant-main").mkdir(parents=True, exist_ok=True)
            (product_dir / "output/generated-images/detail").mkdir(parents=True, exist_ok=True)
            (product_dir / "logs").mkdir(parents=True, exist_ok=True)
            write_json(product_dir / "input/source.json", {
                "schema_version": "1.0.0",
                "product_id": "P000086",
                "source_kind": "ozon_reference_draft",
                "main_images": [],
                "detail_images": [],
            })
            write_json(product_dir / "output/image-plan.json", {
                "schema_version": "1.0.0",
                "product_id": "P000086",
                "source_kind": "ozon_reference_draft",
                "main_images": [{
                    "slot": "main-001",
                    "prompt": "real photo",
                    "output_path": "products/P000086/output/generated-images/variant-main/main-001.png",
                }],
                "detail_images": [{
                    "slot": "detail-001",
                    "image_type": "detail",
                    "prompt": "real detail",
                    "output_path": "products/P000086/output/generated-images/detail/detail-001.png",
                }],
            })
            (product_dir / "output/generated-images/variant-main/main-001.png").write_bytes(b"png")
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "PRODUCTS_DIR", root / "products"), \
                 patch.object(workbench, "ozon_reference_image_codex_command", return_value=["codex", "exec"]), \
                 patch.object(workbench.subprocess, "run", side_effect=workbench.subprocess.TimeoutExpired(["codex"], 1)):
                workbench.run_ozon_reference_image_generation_once(product_dir)
            report = json.loads((product_dir / "output/ozon-reference-image-generation-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "PARTIAL")
            self.assertEqual(report["generated_slots"], ["products/P000086/output/generated-images/variant-main/main-001.png"])
            self.assertEqual(report["write_api_calls"], 0)
            self.assertEqual(report["inventory_api_calls"], 0)

    async def test_ozon_reference_ai_design_failure_has_chinese_reason(self):
        html = """
        <html><head>
          <meta property="og:title" content="Товар">
          <meta property="og:image" content="https://cdn.ozon.ru/reference-a.jpg">
        </head></html>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category = fake_category_selection()
            payload = {
                "items": [{
                    "url": "https://www.ozon.ru/product/fail-ai-999/",
                    "length_mm": 100,
                    "width_mm": 80,
                    "height_mm": 50,
                    "weight_g": 300,
                    "selling_price_cny": 88,
                    "ozon_category_selection": category,
                }],
                "store_ids": ["store-a"],
            }
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "PRODUCTS_DIR", root / "products"), \
                 patch.object(workbench, "build_selection", return_value=category), \
                 patch.object(workbench, "connected_store_ids", return_value=["store-a"]), \
                 patch.object(workbench, "download_public_ozon_page", return_value=(html, "text/html; charset=utf-8")), \
                 patch.object(workbench, "download_url", return_value=(b"image-bytes", "image/jpeg")), \
                 patch.object(workbench, "run_ozon_reference_codex_design", side_effect=RuntimeError("model unavailable")):
                await workbench.create_workbench_ozon_reference_tasks(FakeRequest(payload))
                workbench.process_ozon_reference_tasks_once(limit=5)
                generated = workbench.process_ozon_reference_ai_design_once(limit=5)
                listed = workbench.workbench_ozon_reference_tasks()

            self.assertEqual(generated["processed_count"], 0)
            self.assertEqual(generated["failed_count"], 1)
            self.assertEqual(listed["items"][0]["status"], "failed")
            self.assertIn("AI商品卡生成失败", listed["items"][0]["message"])
            self.assertEqual(listed["items"][0]["write_api_calls"], 0)
            self.assertEqual(listed["items"][0]["inventory_api_calls"], 0)

    async def test_ozon_reference_tasks_reject_missing_inputs_before_queue(self):
        html = """
        <html>
          <head>
            <meta property="og:title" content="Тестовая фигурка для Ozon">
            <meta name="description" content="Описание конкурента для SEO анализа">
            <meta property="og:image" content="https://cdn.ozon.ru/reference-a.jpg">
            <script type="application/ld+json">
              {
                "@type": "Product",
                "name": "Тестовая фигурка для Ozon",
                "description": "Подробное описание товара",
                "image": ["https://cdn.ozon.ru/reference-b.jpg"],
                "offers": {"price": "1290", "priceCurrency": "RUB"}
              }
            </script>
          </head>
          <body><img data-src="https://cdn.ozon.ru/reference-c.webp"></body>
        </html>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "text": "https://www.ozon.ru/product/test-reference-789/",
                "store_ids": ["store-a"],
            }
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "connected_store_ids", return_value=["store-a"]), \
                 patch.object(workbench, "download_public_ozon_page", return_value=(html, "text/html; charset=utf-8")), \
                 patch.object(workbench, "download_url", return_value=(b"image-bytes", "image/jpeg")):
                with self.assertRaises(HTTPException) as raised:
                    await workbench.create_workbench_ozon_reference_tasks(FakeRequest(payload))

            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn("开始 Ozon 参考自动生产前必须先补齐", str(raised.exception.detail))
            self.assertFalse((root / "runtime" / workbench.OZON_REFERENCE_TASKS_FILENAME).exists())

    async def test_ozon_reference_tasks_capture_failure_has_chinese_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category = fake_category_selection()
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "build_selection", return_value=category), \
                 patch.object(workbench, "connected_store_ids", return_value=["store-a"]), \
                 patch.object(workbench, "download_public_ozon_page", side_effect=OSError("network down")):
                await workbench.create_workbench_ozon_reference_tasks(FakeRequest({
                    "text": "https://www.ozon.ru/product/fail-reference-789/",
                    "length_mm": 120,
                    "width_mm": 80,
                    "height_mm": 60,
                    "weight_g": 400,
                    "selling_price_cny": 99,
                    "ozon_category_selection": category,
                    "store_ids": ["store-a"],
                }))
                processed = workbench.process_ozon_reference_tasks_once(limit=5)
                listed = workbench.workbench_ozon_reference_tasks()

            self.assertEqual(processed["processed_count"], 0)
            self.assertEqual(processed["failed_count"], 1)
            self.assertEqual(listed["items"][0]["status"], "failed")
            self.assertIn("公开商品卡抓取失败", listed["items"][0]["message"])
            self.assertEqual(listed["items"][0]["write_api_calls"], 0)
            self.assertEqual(listed["items"][0]["inventory_api_calls"], 0)

    def test_manual_test_product_is_not_listed_as_a_formal_workbench_product(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            source_path = product / "input/source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["source_kind"] = "manual_test"
            source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                self.assertEqual(workbench.owned_product_dirs(), [])

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

    def test_sqlite_handoff_overrides_stale_99_percent_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            stale = json.loads((product / "status.json").read_text(encoding="utf-8"))
            stale.update({"status": "NEEDS_ATTENTION", "progress": 95, "current_step": "ozon_upload"})
            snapshot = {
                "product": {
                    "aggregate_status": "HANDED_OFF_TO_OZON",
                    "updated_at": "2026-07-18T01:53:32+08:00",
                },
                "stores": [{
                    "id": 1, "store_id": "store-a", "selected": 1,
                    "api_write_count": 1,
                }],
                "sku_publications": [{
                    "publication_id": 1, "sku_id": "sku-a",
                    "offer_id": "OFFER-A", "task_id": "TASK-A",
                    "ozon_product_id": "unknown",
                }],
            }
            with patch.object(workbench, "cutover_active", return_value=True), \
                 patch.object(workbench, "product_snapshot", return_value=snapshot), \
                 patch.object(workbench, "active_product_worker", return_value=None):
                effective = workbench.effective_product_status(product, stale)
            self.assertEqual(effective["status"], "PENDING_REMOTE")
            self.assertEqual(effective["progress"], 99)
            self.assertEqual(effective["api_write_count"], 1)
            self.assertEqual(effective["ozon"]["task_id"], "TASK-A")
            self.assertEqual(effective["ozon"]["offer_id"], "OFFER-A")
            self.assertEqual(effective["next_action"], "read_only_status_query")
            self.assertEqual(effective["current_step"], "ozon_upload")

    def test_sqlite_handoff_with_unwritten_selected_store_stays_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            stale = json.loads((product / "status.json").read_text(encoding="utf-8"))
            stale.update({"status": "HANDED_OFF_TO_OZON", "progress": 100, "current_step": "ozon_upload"})
            snapshot = {
                "product": {
                    "aggregate_status": "HANDED_OFF_TO_OZON",
                    "updated_at": "2026-07-18T01:53:32+08:00",
                },
                "stores": [
                    {"id": 1, "store_id": "store-a", "selected": 1, "status": "HANDED_OFF_TO_OZON", "api_write_count": 1},
                    {"id": 2, "store_id": "store-b", "selected": 1, "status": "SELECTED", "api_write_count": 0},
                ],
                "sku_publications": [{
                    "publication_id": 1, "sku_id": "sku-a",
                    "offer_id": "OFFER-A", "task_id": "TASK-A",
                    "ozon_product_id": "unknown",
                }],
            }
            with patch.object(workbench, "cutover_active", return_value=True), \
                 patch.object(workbench, "product_snapshot", return_value=snapshot), \
                 patch.object(workbench, "active_product_worker", return_value=None):
                effective = workbench.effective_product_status(product, stale)
            self.assertEqual(effective["status"], "PARTIAL")
            self.assertEqual(effective["progress"], 95)
            self.assertEqual(effective["next_action"], "ozon_upload")
            self.assertIn("ozon_upload", effective["pending_steps"])

    def test_sqlite_failed_store_is_shown_as_needs_attention_with_retry_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            stale = json.loads((product / "status.json").read_text(encoding="utf-8"))
            stale.update({"status": "NEEDS_ATTENTION", "progress": 95, "current_step": "ozon_upload"})
            snapshot = {
                "product": {
                    "aggregate_status": "FAILED",
                    "updated_at": "2026-07-18T01:53:32+08:00",
                },
                "stores": [{
                    "id": 1, "store_id": "store-a", "selected": 1,
                    "status": "FAILED", "api_write_count": 0,
                    "last_error": "cloudflared not reachable",
                }],
                "sku_publications": [{
                    "publication_id": 1, "sku_id": "sku-a",
                    "offer_id": "OFFER-A", "task_id": "unknown",
                    "ozon_product_id": "unknown",
                }],
            }
            with patch.object(workbench, "cutover_active", return_value=True), \
                 patch.object(workbench, "product_snapshot", return_value=snapshot), \
                 patch.object(workbench, "active_product_worker", return_value=None):
                effective = workbench.effective_product_status(product, stale)
            self.assertEqual(effective["status"], "NEEDS_ATTENTION")
            self.assertEqual(effective["failed_step"], "ozon_upload")
            self.assertEqual(effective["next_action"], "retry_failed_store")
            self.assertEqual(effective["error_message"], "cloudflared not reachable")
            self.assertEqual(effective["api_write_count"], 0)

    def test_old_formal_input_is_blocked_before_submit_but_readonly_after_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            plan = json.loads((product / "output/image-plan.json").read_text(encoding="utf-8"))
            blocked = workbench.production_readiness_state(product, {"status": "COLLECTED"}, plan)
            readonly = workbench.production_readiness_state(product, {"status": "HANDED_OFF_TO_OZON"}, plan)
            self.assertTrue(blocked["blocking"])
            self.assertEqual(blocked["state"], "formal_input_blocked")
            self.assertFalse(readonly["blocking"])
            self.assertEqual(readonly["state"], "submitted_read_only")

    async def test_handed_off_product_rejects_all_local_content_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            status = json.loads((product / "status.json").read_text(encoding="utf-8"))
            status.update({"status": "HANDED_OFF_TO_OZON", "progress": 100})
            write_json(product / "status.json", status)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                with self.assertRaises(HTTPException) as draft_error:
                    await workbench.save_workbench_draft(
                        "P000101", FakeRequest({"title_ru": "Нельзя изменить"})
                    )
                with self.assertRaises(HTTPException) as image_error:
                    await workbench.queue_single_image_regeneration(
                        "P000101", "main-001", FakeRequest({"prompt": "Нельзя изменить"})
                    )
                with self.assertRaises(HTTPException) as store_error:
                    await workbench.save_product_store_selection(
                        "P000101", FakeRequest({"store_ids": []})
                    )
            self.assertEqual(draft_error.exception.status_code, 409)
            self.assertEqual(image_error.exception.status_code, 409)
            self.assertEqual(store_error.exception.status_code, 409)
            self.assertFalse((product / "output/image-regeneration-request.json").exists())

    def test_analysis_risks_are_not_hidden_by_empty_runtime_risk_list(self):
        risk = workbench.calculate_risk(
            {"status": "COLLECTED"}, {}, {"missing_required_attributes": []}, {},
            analysis={"risks": [{"area": "category_consistency", "level": "high", "message": "类目与商品语义不一致", "blocking": False}]},
            readiness_state={"state": "current_rules", "blocking": False},
        )
        self.assertEqual(risk["level"], "high")
        self.assertEqual(risk["items"][0]["code"], "analysis_category_consistency")

    def test_summary_and_product_list_use_real_product_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                summary = workbench.workbench_summary()
                products = workbench.workbench_products()
            self.assertEqual(summary["counts"]["需要处理"], 1)
            self.assertEqual(summary["high_risk_count"], 1)
            self.assertEqual(products["total"], 1)
            self.assertEqual(products["items"][0]["workflow_bucket"], "需要处理")

    def test_product_list_and_risks_use_lightweight_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            with patch.object(workbench, "ROOT", root), \
                 patch.object(workbench, "PRODUCTS_DIR", root / "products"), \
                 patch.object(workbench, "workbench_product_detail", side_effect=AssertionError("full detail should not load")):
                card = workbench.workbench_card(product)
                products = workbench.workbench_products()
                risks = workbench.workbench_risks()
            self.assertEqual(card["product_id"], "P000101")
            self.assertEqual(products["total"], 1)
            self.assertEqual(risks["items"][0]["product_id"], "P000101")

    def test_product_list_exposes_canonical_status_for_handed_off_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            status_path = product / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status.update({
                "status": "HANDED_OFF_TO_OZON",
                "current_step": "ozon_upload",
                "progress": 100,
                "next_action": "complete",
                "error_message": "已提交Ozon，后续请在Ozon商品卡后台处理",
                "ozon": {"upload_status": "handed_off", "task_id": "5223115360", "errors": []},
            })
            write_json(status_path, status)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                card = workbench.workbench_products()["items"][0]
            self.assertEqual(card["status"], "PENDING_REMOTE")
            self.assertEqual(card["raw_status"], "PENDING_REMOTE")
            self.assertEqual(card["state"], "等待Ozon处理")
            self.assertEqual(card["handoff_message"], "已提交Ozon，正在等待Ozon生成商品卡；本地可执行只读状态查询。")

    def test_detail_exposes_copy_images_attributes_pricing_and_risk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
            self.assertEqual(detail["content"]["title_ru"], "Тестовый товар")
            self.assertEqual(len(detail["images"]), 2)
            self.assertEqual(detail["images"][0]["state"], "FAIL")
            self.assertNotIn("qc_dimensions", detail["images"][0])
            self.assertEqual(detail["attributes"]["summary"]["required_count"], 1)
            self.assertEqual(detail["pricing"]["sku_pricing"][0]["selling_price_rub"], 500)
            self.assertEqual(detail["risk"]["level"], "high")

    def test_detail_uses_compiled_attributes_without_full_dictionary_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write_json(product / "output/ozon-attributes.json", {
                "attributes": [{
                    "attribute_id": 10,
                    "attribute_name": "Материал",
                    "required": True,
                    "value": "unknown",
                    "source": "unknown",
                    "allowed_values": [
                        {"id": index, "value": f"material-{index}"}
                        for index in range(5000)
                    ],
                }],
            })
            write_json(product / "output/ozon-attributes-final.json", {
                "schema_version": "1.0.0",
                "product_id": "P000101",
                "attributes": [{
                    "attribute_id": 10,
                    "attribute_name": "Материал",
                    "required": True,
                    "value": "Стекло",
                    "source": "1688",
                    "confidence": 1.0,
                    "dictionary_value_id": 77,
                }],
            })
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
            attribute = detail["attributes"]["attributes"][0]
            self.assertEqual(attribute["value"], "Стекло")
            self.assertNotIn("allowed_values", attribute)
            self.assertLess(len(json.dumps(detail["attributes"], ensure_ascii=False)), 5000)

    def test_detail_groups_each_sku_with_own_main_and_shared_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            source = json.loads((product / "input/source.json").read_text(encoding="utf-8"))
            source["skus"] = [
                {"sku_id": "sku-1", "sku_name": "350毫升", "purchase_price": 10, "option_values": [{"value": "350毫升"}]},
                {"sku_id": "sku-2", "sku_name": "400毫升", "purchase_price": 11, "option_values": [{"value": "400毫升"}]},
            ]
            write_json(product / "input/source.json", source)
            plan = {
                "main_images": [
                    {"slot": "main-sku-1", "image_type": "main", "variant_scope": "sku", "source_sku_id": "sku-1", "output_path": "products/P000101/output/generated-images/variant-main/sku-1.png"},
                    {"slot": "main-sku-2", "image_type": "main", "variant_scope": "sku", "source_sku_id": "sku-2", "output_path": "products/P000101/output/generated-images/variant-main/sku-2.png"},
                ],
                "detail_images": [
                    {"slot": "detail-001", "image_type": "benefit", "variant_scope": "shared", "source_sku_id": "all", "shared_across_variants": True, "output_path": "products/P000101/output/generated-images/detail/detail-001.png"},
                    {"slot": "detail-002", "image_type": "scene", "variant_scope": "shared", "source_sku_id": "all", "shared_across_variants": True, "output_path": "products/P000101/output/generated-images/detail/detail-002.png"},
                ],
            }
            write_json(product / "output/image-plan.json", plan)
            for relative in (
                "output/generated-images/variant-main/sku-1.png", "output/generated-images/variant-main/sku-2.png",
                "output/generated-images/detail/detail-001.png", "output/generated-images/detail/detail-002.png",
            ):
                path = product / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"png")
            write_json(product / "output/image-qc-report.json", {
                "score": 100, "decision": "pass", "critical_failures": [], "issues": [],
            })
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
            self.assertEqual(len(detail["image_groups"]), 2)
            self.assertEqual(
                [group["main_image"]["slot"] for group in detail["image_groups"]],
                ["main-sku-1", "main-sku-2"],
            )
            self.assertEqual(
                [[item["slot"] for item in group["detail_images"]] for group in detail["image_groups"]],
                [["detail-001", "detail-002"], ["detail-001", "detail-002"]],
            )
            self.assertEqual({item["source_sku_id"] for item in detail["images"][:2]}, {"sku-1", "sku-2"})

    def test_detail_overlays_generated_final_attribute_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write_json(product / "output/ozon-attributes.json", {
                "summary": {"required_count": 1, "mapped_count": 0},
                "missing_required_attributes": [{"attribute_id": 1, "attribute_name": "Материал"}],
                "attributes": [{
                    "attribute_id": 1, "attribute_name": "Материал",
                    "value": "unknown", "required": True, "source": "unknown",
                }],
            })
            write_json(product / "output/ozon-attributes-final.json", {
                "attributes": [{
                    "attribute_id": 1, "attribute_name": "Материал", "required": True,
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

    def test_detail_autofills_model_name_with_stable_product_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
            attribute = detail["attributes"]["attributes"][0]
            self.assertRegex(attribute["value"], r"^\d{12}$")
            self.assertEqual(attribute["source"], "AI_estimated")
            self.assertEqual(detail["attributes"]["summary"]["missing_count"], 0)
            self.assertEqual(detail["attributes"]["missing_required_attributes"], [])

    def test_detail_prefers_collected_measurements_over_stale_draft_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write_json(product / "output/ozon-upload-config.json", {
                "product_weight": {"value_g": 7400, "source": "source.product_attributes.sku_measurement_table.weight"},
                "product_dimensions": {"length_mm": 495, "width_mm": 350, "height_mm": 560, "source": "source.product_attributes.sku_measurement_table"},
                "package_weight": {"value_g": 7700, "source": "pricing_rules.package_estimation"},
                "package_dimensions": {"length_mm": 505, "width_mm": 360, "height_mm": 570, "source": "pricing_rules.package_estimation"},
            })
            write_json(product / "output/ozon-attributes.json", {
                "summary": {"required_count": 0, "mapped_count": 0},
                "missing_required_attributes": [],
                "attributes": [
                    {"attribute_id": 4497, "attribute_name": "Вес с упаковкой, г", "value": "unknown", "required": False, "source": "unknown"},
                    {"attribute_id": 8416, "attribute_name": "Ширина, см", "value": "unknown", "required": False, "source": "unknown"},
                ],
            })
            write_json(product / "output/ozon-attributes-final.json", {
                "attributes": [
                    {"attribute_id": 4497, "attribute_name": "Вес с упаковкой, г", "value": 7700, "source": "AI_estimated", "evidence": ["pricing_rules.package_estimation"]},
                    {"attribute_id": 8416, "attribute_name": "Ширина, см", "value": 35, "source": "1688", "evidence": ["source.product_attributes.sku_measurement_table"]},
                ],
            })
            write_json(product / "output/workbench-draft.json", {"attributes": {"4497": "1150", "8416": "20"}})
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                detail = workbench.workbench_product_detail("P000101")
            by_id = {
                str(item["attribute_id"]): item
                for item in detail["attributes"]["attributes"]
            }
            self.assertEqual(by_id["4497"]["value"], 7700)
            self.assertEqual(by_id["8416"]["value"], 35)
            self.assertNotEqual(by_id["4497"]["source"], "人工修改")

    async def test_draft_save_ignores_system_model_attribute_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                await workbench.save_workbench_draft(
                    "P000101",
                    FakeRequest({"attributes": {"1": "人工标题旧值", "999": "可编辑值"}}),
                )
            draft = json.loads((product_dir / "output/workbench-draft.json").read_text(encoding="utf-8"))
            self.assertNotIn("1", draft.get("attributes") or {})
            self.assertEqual(draft["attributes"]["999"], "可编辑值")

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

    async def test_sku_override_api_saves_empty_dimensions_and_category_dynamic_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root)
            source_path = product_dir / "input/source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["skus"] = [
                {"sku_id": "sku-1", "sku_name": "黑色", "purchase_price": 10, "option_values": [{"name_cn": "颜色", "value_cn": "黑色"}]},
                {"sku_id": "sku-2", "sku_name": "银色", "purchase_price": 12, "option_values": [{"name_cn": "颜色", "value_cn": "银色"}]},
            ]
            source["product_attributes"] = [{"name_cn": "材质", "value_cn": "金属"}]
            write_json(source_path, source)
            write_json(product_dir / "output/ozon-category-attributes.json", {
                "category_id": 10,
                "type_id": 20,
                "attributes": [
                    {"attribute_id": 10097, "attribute_name": "Название цвета", "type": "String", "required": True, "is_aspect": True},
                    {"attribute_id": 3001, "attribute_name": "Размер", "type": "String", "required": False, "is_aspect": True},
                ],
            })
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                initial = workbench.workbench_product_detail("P000101")
                self.assertIn("10097", initial["skus"][0]["sku_row"]["dynamic_attributes"])
                await workbench.save_workbench_draft("P000101", FakeRequest({"sku_overrides": {"sku-1": {"product_length_mm": 125}}}))
                await workbench.save_workbench_draft("P000101", FakeRequest({"sku_overrides": {"sku-1": {"product_width_mm": 80}}}))
                await workbench.save_workbench_draft("P000101", FakeRequest({"sku_overrides": {"sku-1": {"product_height_mm": 60}}}))
                await workbench.save_workbench_draft("P000101", FakeRequest({"sku_overrides": {"sku-1": {
                    "color": "черный",
                    "specification_text": "20cm",
                    "product_weight_g": 300,
                    "attribute:3001": "20cm",
                }}}))
                detail = workbench.workbench_product_detail("P000101")
                await workbench.save_workbench_draft("P000101", FakeRequest({"sku_overrides": {"sku-1": {"color": ""}}}))

            rows = {item["sku_id"]: item["sku_row"] for item in detail["skus"]}
            self.assertEqual(rows["sku-1"]["product_dimensions"]["canonical_value"], {"length_mm": 125, "width_mm": 80, "height_mm": 60})
            self.assertEqual(rows["sku-1"]["specification"]["canonical_value"], "20cm")
            self.assertEqual(rows["sku-1"]["product_weight"]["canonical_value"], 300)
            self.assertEqual(rows["sku-1"]["dynamic_attributes"]["3001"]["canonical_value"], "20cm")
            self.assertNotEqual(rows["sku-2"]["product_weight"]["canonical_value"], 300)
            overrides = json.loads((product_dir / "input/workbench-sku-overrides.json").read_text(encoding="utf-8"))
            self.assertNotIn(
                ("sku-1", "color"),
                {(item["sku_id"], item["field_name"]) for item in overrides.get("overrides") or []},
            )

    async def test_workbench_persists_ten_sku_rows_and_selected_overrides_by_sku_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_sku_table_product(root, sku_count=10)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                initial = workbench.workbench_product_detail("P000777")
                self.assertEqual(len(initial["skus"]), 10)
                initial_rows = {item["sku_id"]: item["sku_row"] for item in initial["skus"]}
                self.assertEqual(len(initial_rows), 10)
                self.assertIn("10097", initial_rows["SKU-01"]["dynamic_attributes"])
                self.assertNotEqual(initial_rows["SKU-01"]["color"]["canonical_value"], initial_rows["SKU-06"]["color"]["canonical_value"])

                await workbench.save_workbench_draft("P000777", FakeRequest({"sku_overrides": {
                    "SKU-01": {
                        "color": "бирюзовый",
                        "specification_text": "ручной 01",
                        "product_weight_g": 1111,
                        "product_length_mm": 211,
                        "product_width_mm": 121,
                        "product_height_mm": 81,
                    },
                    "SKU-06": {
                        "color": "оливковый",
                        "specification_text": "ручной 06",
                        "product_weight_g": 1666,
                        "product_length_mm": 266,
                        "product_width_mm": 166,
                        "product_height_mm": 126,
                    },
                    "SKU-10": {
                        "color": "графитовый",
                        "specification_text": "ручной 10",
                        "product_weight_g": 1999,
                        "product_length_mm": 299,
                        "product_width_mm": 199,
                        "product_height_mm": 159,
                    },
                }}))
                refreshed = workbench.workbench_product_detail("P000777")

            rows = {item["sku_id"]: item["sku_row"] for item in refreshed["skus"]}
            self.assertEqual(len(rows), 10)
            self.assertEqual(rows["SKU-01"]["color"]["canonical_value"], "бирюзовый")
            self.assertEqual(rows["SKU-01"]["specification"]["canonical_value"], "ручной 01")
            self.assertEqual(rows["SKU-01"]["product_dimensions"]["canonical_value"], {"length_mm": 211, "width_mm": 121, "height_mm": 81})
            self.assertEqual(rows["SKU-06"]["color"]["canonical_value"], "оливковый")
            self.assertEqual(rows["SKU-06"]["product_weight"]["canonical_value"], 1666)
            self.assertEqual(rows["SKU-10"]["color"]["canonical_value"], "графитовый")
            self.assertEqual(rows["SKU-10"]["product_dimensions"]["canonical_value"]["height_mm"], 159)
            self.assertEqual(rows["SKU-02"]["product_weight"]["canonical_value"], 250)
            overrides = json.loads((root / "products/P000777/input/workbench-sku-overrides.json").read_text(encoding="utf-8"))
            changed_ids = {item["sku_id"] for item in overrides["overrides"]}
            self.assertEqual(changed_ids, {"SKU-01", "SKU-06", "SKU-10"})

    async def test_tag_over_30_characters_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = make_product(root)
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                result = await workbench.save_workbench_draft("P000101", FakeRequest({"tags": ["#" + "д" * 31, "#товар_1000"]}))
            self.assertTrue(result["saved"])
            draft = json.loads((product_dir / "output/workbench-draft.json").read_text(encoding="utf-8"))
            self.assertEqual(draft["tags"], ["#" + "д" * 29, "#товар"])

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
        # 2026-08-14：真实服务的前端是 collector/workbench-command-center/dist 构建产物，
        # 旧 static/workbench.* 四件套已不再被任何路由回源（见 docs/audit-20260814-main-flow.md）。
        dist = ROOT / "collector/workbench-command-center/dist"
        html = (dist / "index.html").read_text(encoding="utf-8")
        bundle = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted((dist / "assets").glob("index-*.js"))
        )
        css = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted((dist / "assets").glob("index-*.css"))
        )
        for text in ("采集箱", "处理中", "需要处理", "自动模式", "彻底删除", "上传Ozon", "售价", "修改", "等待 AI 生成商品卡"):
            self.assertIn(text, bundle)
        self.assertIn(".attention-item", css)
        self.assertIn(".activity-stream", css)
        self.assertRegex(html, r"/assets/index-[A-Za-z0-9_-]+\.js")
        self.assertRegex(html, r"/assets/index-[A-Za-z0-9_-]+\.css")
        self.assertIn("target_store_id_source", (ROOT / "collector/local-ingest/app.py").read_text(encoding="utf-8"))

    def test_missing_sku_reference_error_requests_manual_sku_confirmation(self):
        result = workbench.friendly_pipeline_error({
            "failed_step": "image_generation",
            "error_message": "MISSING_REQUIRED_SKU_REFERENCE: 缺少真实参考图",
        })
        self.assertEqual(result["title"], "SKU缺少参考图")
        self.assertEqual(result["action"], "绑定SKU参考图")
        self.assertEqual(result["tab"], "sku")
        self.assertIn("从本商品已采集图片中选择", result["message"])

    def test_workbench_static_files_disable_stale_browser_cache(self):
        with patch.object(workbench, "trigger_image_cleanup"), \
             patch.object(workbench, "ensure_image_status_monitor"), \
             patch.object(workbench, "sync_remote_ozon_status_once"):
            page = workbench.workbench_page()
        self.assertEqual(page.headers.get("cache-control"), "no-store, max-age=0")
        self.assertEqual(workbench.workbench_css().status_code, 307)
        self.assertEqual(workbench.workbench_css().headers.get("location"), "/command-center?v=2026-08-01-ui-state-v1")
        self.assertEqual(workbench.workbench_js().status_code, 307)
        self.assertEqual(workbench.workbench_js().headers.get("location"), "/command-center?v=2026-08-01-ui-state-v1")
        self.assertEqual(workbench.workbench_future_css().status_code, 307)
        self.assertEqual(workbench.workbench_future_css().headers.get("location"), "/command-center?v=2026-08-01-ui-state-v1")
        asset_dir = workbench.COMMAND_CENTER_DIST_DIR / "assets"
        command_center_assets = sorted(asset_dir.glob("index-*.js"))
        if command_center_assets:
            asset = workbench.command_center_asset(command_center_assets[0].name)
            self.assertEqual(asset.headers.get("cache-control"), "no-store, max-age=0")
        logo = workbench.jlc_global_logo()
        self.assertEqual(logo.media_type, "image/png")
        self.assertIn("immutable", logo.headers.get("cache-control"))

    def test_legacy_workbench_url_redirects_to_command_center(self):
        response = workbench.workbench_redirect()
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers.get("location"), "/command-center?v=2026-08-01-ui-state-v1")
        legacy = workbench.workbench_legacy_page()
        self.assertEqual(legacy.status_code, 307)
        self.assertEqual(legacy.headers.get("location"), "/command-center?v=2026-08-01-ui-state-v1")

    def test_unversioned_command_center_redirects_to_current_version(self):
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/command-center",
            "headers": [],
            "query_string": b"",
        })
        response = workbench.command_center_alias(request)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers.get("location"), "/command-center?v=2026-08-01-ui-state-v1")

    def test_manual_workbench_tags_are_normalized_without_filler(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "P000101"
            tags = VALID_HASHTAGS[:30]
            write_json(product_dir / "output/workbench-draft.json", {"tags": tags})
            result = build_tags(product_dir)
            self.assertEqual(result["tags"], tags)
            self.assertEqual(result["count"], 30)
            write_json(product_dir / "output/workbench-draft.json", {"tags": ["#товар_1000", *tags[:28]]})
            completed = build_tags(product_dir)
            self.assertEqual(completed["count"], 28)
            self.assertTrue(all(re.fullmatch(r"#[А-Яа-яЁё]+", item) for item in completed["tags"]))

    def test_designer_tags_replace_stale_materialized_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "P000101"
            researched = VALID_HASHTAGS[:30]
            stale = ["#старыйтег", "#старый_1000", *VALID_HASHTAGS[:28]]
            write_json(product_dir / "output/ozon-ecommerce-design.json", {
                "listing": {"hashtags": researched},
            })
            write_json(product_dir / "output/ozon-tags.json", {"tags": stale})
            result = build_tags(product_dir)
            self.assertEqual(result["tags"], researched)
            self.assertIn("ozon-ecommerce-design.json", result["source_refs"][0])


if __name__ == "__main__":
    unittest.main()
