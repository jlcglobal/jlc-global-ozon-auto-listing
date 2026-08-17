import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))

from ozon_adapter import OzonConfig  # noqa: E402
from ozon_uploader import (  # noqa: E402
    OzonWriteClient,
    UploadGateError,
    assert_production_allowed,
    build_upload_payload,
    execute_upload,
    prepare_upload,
)
from ozon_uploader.client import OzonUploadApiError  # noqa: E402
from ozon_uploader.images import sha256_file, stage_images  # noqa: E402
from ozon_uploader.service import (  # noqa: E402
    SCHEMAS, _images_ingested, _is_official_ozon_image_url, _parse_import_result,
    _remote_content_blockers, _remote_terminal_errors, _resolve_rich_content_for_upload, build_import_items,
    build_preflight, build_product_exists_check, current_image_completeness, load_json, recover_remote_import,
    current_upload_image_gate, sync_image_channel_status, validate,
    ozon_weight_grams, verify_public_image_urls,
)
from ozon_uploader import image_channels  # noqa: E402


class RecordingTransport:
    def __init__(self):
        self.calls = []
        self.offers = []

    def __call__(self, endpoint, payload):
        self.calls.append((endpoint, copy.deepcopy(payload)))
        if endpoint == OzonWriteClient.PRODUCT_IMPORT_ENDPOINT:
            self.offers = [item["offer_id"] for item in payload["items"]]
            return {"result": {"task_id": 70001}}
        if endpoint == OzonWriteClient.IMPORT_INFO_ENDPOINT:
            return {
                "result": {
                    "items": [
                        {
                            "offer_id": offer_id,
                            "product_id": 90000 + index,
                            "status": "imported",
                            "errors": [],
                        }
                        for index, offer_id in enumerate(self.offers, start=1)
                    ]
                }
            }
        if endpoint == OzonWriteClient.PRODUCT_INFO_LIST_ENDPOINT:
            return {
                "items": [
                    {
                        "offer_id": offer_id,
                        "images": [f"https://cdn.example.test/{index}.jpg" for index in range(1, 4)],
                        "primary_image": ["https://cdn.example.test/0.jpg"],
                        "errors": [],
                    }
                    for offer_id in self.offers
                ]
            }
        raise AssertionError(endpoint)


class RemoteContentGateTests(unittest.TestCase):
    def test_blocks_chinese_evidence_and_unit_suffixed_decimal(self):
        items = [{
            "offer_id": "P000006-1",
            "name": "Пластиковая канистра",
            "attributes": [
                {"id": 4384, "values": [{"value": "内盖，见 input/a.jpg"}]},
                {"id": 6378, "values": [{"value": "10 л"}]},
            ],
        }]
        metadata = {"attributes": [
            {"attribute_id": 4384, "type": "String"},
            {"attribute_id": 6378, "type": "Decimal"},
        ]}
        blockers = _remote_content_blockers(items, metadata)
        self.assertTrue(any("含中文" in item for item in blockers))
        self.assertTrue(any("必须只填写数字" in item for item in blockers))

    def test_accepts_clean_russian_text_and_bare_decimal(self):
        items = [{
            "offer_id": "P000006-1",
            "name": "Пластиковая канистра",
            "attributes": [
                {"id": 4384, "values": [{"value": "внутренняя крышка"}]},
                {"id": 6378, "values": [{"value": "2.5"}]},
            ],
        }]
        metadata = {"attributes": [
            {"attribute_id": 4384, "type": "String"},
            {"attribute_id": 6378, "type": "Decimal"},
        ]}
        self.assertEqual(_remote_content_blockers(items, metadata), [])

class FakeTunnel:
    def __init__(self, directory):
        self.directory = directory

    def __enter__(self):
        return self

    def public_image_urls(self, manifest):
        urls = []
        for item in manifest["images"]:
            item["public_url"] = f"https://images.example.test/{item['staged_name']}"
            item["status"] = "served"
            urls.append(item["public_url"])
        manifest["hosting_mode"] = "cloudflare_quick_tunnel"
        manifest["tunnel_url"] = "https://images.example.test"
        return urls

    def __exit__(self, exc_type, exc, traceback):
        return None


class FakeChannelProcess:
    pid = 43210


class ImageChannelRetryTests(unittest.TestCase):
    def test_public_image_ssl_eof_probe_is_diagnostic_not_blocking(self):
        manifest = {
            "images": [{
                "slot": "main-a",
                "role": "variant_main",
                "local_path": "output/generated-images/variant-main/a.png",
                "staged_name": "a.png",
                "public_url": "https://example.test/a.png",
                "sha256": "a" * 64,
                "status": "served",
                "ozon_image_id": "unknown",
                "ozon_url": "unknown",
                "error": "unknown",
            }],
        }
        reason = "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"
        with patch("ozon_uploader.service._verify_public_image_url", return_value={"ok": False, "reason": reason}):
            failures = verify_public_image_urls(manifest)

        self.assertEqual(failures, [])
        self.assertEqual(manifest["images"][0]["status"], "served")
        self.assertIn("local public image probe unavailable", manifest["images"][0]["error"])

    def test_restarts_public_channel_when_first_url_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "products" / "PTEST001"
            output = product_dir / "output"
            (output / "ozon-image-staging").mkdir(parents=True)
            state_path = output / "image-channel-state.json"
            manifest = {
                "schema_version": "1.0.0",
                "product_id": "PTEST001",
                "images": [{
                    "slot": "main-1",
                    "role": "variant_main",
                    "source_sku_id": "1",
                    "staged_name": "01-main.png",
                    "sha256": "sha",
                    "public_url": "unknown",
                    "status": "pending",
                    "error": "unknown",
                }],
            }
            urls = iter([
                "https://bad-public-url.example.test",
                "https://good-public-url.example.test",
            ])

            def fake_popen(*args, **kwargs):
                state_path.write_text(json.dumps({
                    "status": "running",
                    "worker_pid": 43210,
                    "public_url": next(urls),
                }), encoding="utf-8")
                return FakeChannelProcess()

            attempts = []

            def fake_apply(product, value, public_url):
                attempts.append(public_url)
                if "bad-public-url" in public_url:
                    raise image_channels.ImageTunnelError("public url unavailable")
                for item in value["images"]:
                    item["public_url"] = f"{public_url}/{item['staged_name']}"
                    item["status"] = "served"
                value["tunnel_url"] = public_url
                return value

            with patch.object(image_channels, "running_channel_count", return_value=0), \
                 patch.object(image_channels, "process_alive", return_value=False), \
                 patch.object(image_channels.subprocess, "Popen", side_effect=fake_popen), \
                 patch.object(image_channels, "apply_public_urls", side_effect=fake_apply), \
                 patch.dict(os.environ, {"OZON_IMAGE_CHANNEL_START_ATTEMPTS": "2"}):
                result = image_channels.start_image_channel(product_dir, manifest)

            self.assertEqual(attempts, [
                "https://bad-public-url.example.test",
                "https://good-public-url.example.test",
            ])
            self.assertEqual(result["images"][0]["public_url"], "https://good-public-url.example.test/01-main.png")


def client(transport):
    return OzonWriteClient(
        OzonConfig(client_id="test", api_key="secret", shop_name="zhonglian1"),
        transport=transport,
    )


def copy_product(temp_dir, product_id="P000004"):
    source = ROOT / f"products/{product_id}"
    if not source.is_dir():
        raise unittest.SkipTest(f"runtime fixture {product_id} is not installed")
    target = Path(temp_dir) / f"products/{product_id}"
    shutil.copytree(source, target)
    return target


def reset_to_waiting_manual_review(product_dir, task_authorized=False):
    for name in (
        "ozon-idempotency.json", "image-channel-state.json", "image-channel.stop",
        "ozon-image-transfer.json", "ozon-image-update-receipt.json",
    ):
        (product_dir / "output" / name).unlink(missing_ok=True)
    status_path = product_dir / "status.json"
    status = load_json(status_path)
    status["status"] = "WAITING_MANUAL_REVIEW"
    status["current_step"] = "ozon_preflight"
    status["task_authorized"] = task_authorized
    status["ozon"] = {
        "upload_status": "not_started",
        "product_id": "unknown",
        "offer_id": "unknown",
        "task_id": "unknown",
        "last_response": None,
        "errors": [],
    }
    status["history"] = [
        item for item in status["history"]
        if item["to"] not in {"UPLOADING", "UPDATING", "UPLOADED", "OZON_MODERATION"}
    ]
    status["steps"] = [item for item in status["steps"] if item["name"] != "ozon_upload"]
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    return status


def mark_all_color_variants_mapped_for_mock(product_dir):
    path = product_dir / "output/color-variants.json"
    value = load_json(path)
    for item in value["variants"]:
        if item["status"] == "missing":
            item["image"] = "products/P000004/input/main-images/main-003.webp"
            item["source"] = "main_image_match"
            item["resolution_level"] = 2
            item["confidence"] = 0.95
            item["status"] = "mapped"
            item["reason"] = "Test fixture supplies an explicitly verified variant image."
    value["summary"] = {"total": len(value["variants"]), "mapped": len(value["variants"]), "missing": 0}
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def block_main_color_variant_for_mock(product_dir):
    colors_path = product_dir / "output/color-variants.json"
    colors = load_json(colors_path)
    main = colors["variants"][0]
    main.update({
        "image": "missing", "source": "missing", "resolution_level": 4,
        "confidence": 0, "status": "missing", "reason": "Test main SKU missing",
    })
    colors["summary"] = {"total": len(colors["variants"]), "mapped": len(colors["variants"]) - 1, "missing": 1}
    colors_path.write_text(json.dumps(colors, ensure_ascii=False, indent=2) + "\n")
    policy_path = product_dir / "output/color-variant-policy.json"
    policy = load_json(policy_path)
    policy["status"] = "BLOCK"
    policy["missing_count"] = 1
    policy["blocking_variants"] = [{
        "sku_id": main["sku_id"], "sku_name": main["sku_name"], "reason": main["reason"],
    }]
    policy["warning_variants"] = []
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n")


class RichContentVariantMainResolutionTest(unittest.TestCase):
    def test_variant_main_asset_is_resolved_through_image_tunnel(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            content = {
                "version": 0.3,
                "content": [{
                    "widgetName": "raShowcase", "type": "billboard",
                    "blocks": [{
                        "img": {
                            "src": "asset://main-sku-1",
                            "srcMobile": "asset://main-sku-1",
                        },
                        "title": {"content": ["Title"]},
                        "text": {"content": ["Text"]},
                    }],
                }],
            }
            (output / "rich-content.json").write_text(json.dumps({
                "status": "ready_for_upload",
                "attribute_id": 11254,
                "content": content,
            }))
            final_attributes = {"attributes": [{
                "attribute_id": 11254,
                "value": "unresolved",
                "evidence": [],
            }]}
            manifest = {"images": [{
                "slot": "main-sku-1",
                "role": "variant_main",
                "public_url": "https://images.example.test/main-sku-1.png",
            }]}

            resolved = _resolve_rich_content_for_upload(output, final_attributes, manifest)

            rich_value = json.loads(resolved["attributes"][0]["value"])
            image = rich_value["content"][0]["blocks"][0]["img"]
            self.assertEqual(image["src"], "https://images.example.test/main-sku-1.png")
            self.assertEqual(image["srcMobile"], "https://images.example.test/main-sku-1.png")
            self.assertEqual(final_attributes["attributes"][0]["value"], "unresolved")

    def test_missing_rich_attribute_is_added_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            content = {
                "version": 0.3,
                "content": [{
                    "widgetName": "raShowcase", "type": "billboard",
                    "blocks": [{
                        "img": {
                            "src": "asset://main-sku-1",
                            "srcMobile": "asset://main-sku-1",
                        },
                        "title": {"content": ["Title"]},
                        "text": {"content": ["Text"]},
                    }],
                }],
            }
            (output / "rich-content.json").write_text(json.dumps({
                "status": "ready_for_upload",
                "attribute_id": 11254,
                "content": content,
            }))
            final_attributes = {"attributes": []}
            manifest = {"images": [{
                "slot": "main-sku-1",
                "role": "variant_main",
                "public_url": "https://images.example.test/main-sku-1.png",
            }]}

            resolved = _resolve_rich_content_for_upload(output, final_attributes, manifest)

            self.assertEqual(len(resolved["attributes"]), 1)
            self.assertEqual(resolved["attributes"][0]["attribute_id"], 11254)
            self.assertIn("https://images.example.test/main-sku-1.png", resolved["attributes"][0]["value"])
            self.assertEqual(final_attributes["attributes"], [])


class OzonPayloadScalarContractTest(unittest.TestCase):
    def test_separate_cards_receive_distinct_model_values(self):
        draft = {
            "title": "Прозрачная витрина",
            "description_category_id": 17027919,
            "type_id": 95109,
            "skus": [
                {"source_sku_id": "sku-with-shelf", "offer_id": "offer-1"},
                {"source_sku_id": "sku-without-shelf", "offer_id": "offer-2"},
            ],
        }
        config = {
            "sku_prices": [
                {"source_sku_id": "sku-with-shelf", "price": "1000"},
                {"source_sku_id": "sku-without-shelf", "price": "900"},
            ],
            "sku_colors": [],
            "brand": {"attribute_id": 85, "dictionary_value_id": 1, "value": "Нет бренда"},
            "model_name": {"attribute_id": 9048, "value": "205244030958"},
            "type": {"attribute_id": 8229, "dictionary_value_id": 2, "value": "Шкаф-витрина"},
            "currency_code": "RUB",
            "vat": "0",
            "old_price": None,
            "package_dimensions": {"length_mm": 300, "width_mm": 200, "height_mm": 150},
            "package_weight": {"value_g": 1200},
        }
        grouping = {
            "variant_mapping_status": "SEPARATE_CARDS_REQUIRED",
            "upload_strategy": "separate_cards",
            "platform_can_merge": False,
            "variants": [],
        }

        items = build_import_items(
            draft,
            config,
            ["https://images.example.test/main.png"],
            variant_grouping=grouping,
        )

        model_values = [
            next(attribute for attribute in item["attributes"] if attribute["id"] == 9048)["values"][0]["value"]
            for item in items
        ]
        self.assertEqual(model_values, [
            "205244030958 sku-with-shelf",
            "205244030958 sku-without-shelf",
        ])

    def test_fractional_estimated_weight_is_rounded_up_to_int32_grams(self):
        value = ozon_weight_grams(448.5)
        self.assertEqual(value, 449)
        self.assertIsInstance(value, int)

    def test_invalid_weight_is_blocked_before_any_request(self):
        for value in (0, -1, "unknown", float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(UploadGateError):
                    ozon_weight_grams(value)

    def test_unknown_sku_package_measurement_uses_current_config_fallback(self):
        draft = {
            "title": "Термокружка",
            "description_category_id": 17000001,
            "type_id": 90001,
            "skus": [{"source_sku_id": "sku-1", "offer_id": "P000123-sku-1"}],
        }
        config = {
            "sku_prices": [{"source_sku_id": "sku-1", "price": "900.00"}],
            "sku_colors": [],
            "product_dimensions": {"length_mm": 120, "width_mm": 120, "height_mm": 250},
            "product_weight": {"value_g": 500},
            "package_dimensions": {"length_mm": 130, "width_mm": 130, "height_mm": 263},
            "package_weight": {"value_g": 575},
            "brand": {"attribute_id": 31, "dictionary_value_id": 1, "value": "Нет бренда"},
            "model_name": {"attribute_id": 32, "value": "P000123"},
            "type": {"attribute_id": 33, "dictionary_value_id": 2, "value": "Термокружка"},
            "currency_code": "RUB", "vat": "0", "old_price": None,
        }
        final_attributes = {
            "sku_measurements": {
                "sku-1": {
                    "package_dimensions": {
                        "canonical_value": "unknown",
                        "canonical_unit": "mm",
                    },
                    "package_weight": {
                        "canonical_value": "unknown",
                        "canonical_unit": "g",
                    },
                }
            }
        }
        items = build_import_items(
            draft,
            config,
            ["https://images.example.test/main.png"],
            final_attributes=final_attributes,
        )
        self.assertEqual(items[0]["depth"], 130)
        self.assertEqual(items[0]["width"], 130)
        self.assertEqual(items[0]["height"], 263)
        self.assertEqual(items[0]["weight"], 575)

    def test_active_payload_contract_contains_no_inventory_fields(self):
        draft = {
            "title": "Контейнер для хранения",
            "description": "Практичный контейнер для хранения помогает организовать вещи дома. Подходит для полки, шкафа и повседневного использования.",
            "description_category_id": 17000001,
            "type_id": 90001,
            "skus": [{"source_sku_id": "sku-1", "offer_id": "P000123-sku-1"}],
        }
        config = {
            "sku_prices": [{"source_sku_id": "sku-1", "price": "900.00"}],
            "sku_colors": [],
            "package_dimensions": {"length_mm": 300, "width_mm": 200, "height_mm": 150},
            "package_weight": {"value_g": 449},
            "brand": {"attribute_id": 31, "dictionary_value_id": 1, "value": "Нет бренда"},
            "model_name": {"attribute_id": 32, "value": "P000123"},
            "type": {"attribute_id": 33, "dictionary_value_id": 2, "value": "Контейнер"},
            "currency_code": "RUB", "vat": "0", "old_price": None,
        }
        items = build_import_items(draft, config, ["https://images.example.test/main.png"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["description"], draft["description"])
        self.assertTrue(all("stock" not in item and "warehouse_id" not in item for item in items))

    def test_color_image_falls_back_to_sku_main_image(self):
        draft = {
            "title": "Фигурка",
            "description_category_id": 17000001,
            "type_id": 90001,
            "skus": [{"source_sku_id": "sku-1", "offer_id": "P000123-sku-1"}],
        }
        config = {
            "sku_prices": [{"source_sku_id": "sku-1", "price": "900.00"}],
            "sku_colors": [],
            "package_dimensions": {"length_mm": 110, "width_mm": 110, "height_mm": 110},
            "package_weight": {"value_g": 510},
            "brand": {"attribute_id": 31, "dictionary_value_id": 1, "value": "Нет бренда"},
            "model_name": {"attribute_id": 32, "value": "P000123"},
            "type": {"attribute_id": 33, "dictionary_value_id": 2, "value": "Фигурка"},
            "currency_code": "RUB", "vat": "0", "old_price": None,
        }
        items = build_import_items(
            draft,
            config,
            ["https://images.example.test/detail.png"],
            variant_main_image_urls={"sku-1": "https://images.example.test/main.png"},
        )
        self.assertEqual(items[0]["primary_image"], "https://images.example.test/main.png")
        self.assertEqual(items[0]["color_image"], "https://images.example.test/main.png")

    def test_model_attribute_zero_is_local_only_and_not_sent_to_payload(self):
        draft = {
            "title": "Перчатки",
            "description_category_id": 17000001,
            "type_id": 90001,
            "skus": [{"source_sku_id": "sku-1", "offer_id": "P000027-sku-1"}],
        }
        config = {
            "sku_prices": [{"source_sku_id": "sku-1", "price": "900.00"}],
            "sku_colors": [],
            "package_dimensions": {"length_mm": 300, "width_mm": 200, "height_mm": 150},
            "package_weight": {"value_g": 449},
            "brand": {
                "attribute_id": 31,
                "dictionary_value_id": 126745801,
                "value": "Нет бренда",
            },
            "model_name": {"attribute_id": 0, "value": "123456789012"},
            "type": {"attribute_id": 8229, "dictionary_value_id": 93171, "value": "Перчатки"},
            "currency_code": "RUB",
            "vat": "0",
            "old_price": None,
        }
        items = build_import_items(draft, config, ["https://images.example.test/main.png"])
        attribute_ids = [attribute["id"] for attribute in items[0]["attributes"]]
        self.assertNotIn(0, attribute_ids)
        self.assertIn(31, attribute_ids)
        self.assertIn(8229, attribute_ids)

    def test_variant_color_name_is_normalized_before_payload(self):
        draft = {
            "title": "Портативный блендер",
            "description_category_id": 17000001,
            "type_id": 90001,
            "skus": [{"source_sku_id": "sku-1", "offer_id": "P000009-sku-1"}],
        }
        config = {
            "sku_prices": [{"source_sku_id": "sku-1", "price": "900.00"}],
            "sku_colors": [],
            "package_dimensions": {"length_mm": 300, "width_mm": 200, "height_mm": 150},
            "package_weight": {"value_g": 449},
            "brand": {"attribute_id": 31, "dictionary_value_id": 1, "value": "Нет бренда"},
            "model_name": {"attribute_id": 32, "value": "553829441028"},
            "type": {"attribute_id": 33, "dictionary_value_id": 2, "value": "Блендер"},
            "currency_code": "RUB", "vat": "0", "old_price": None,
        }
        grouping = {"variants": [{
            "sku_id": "sku-1",
            "variant_attribute_values": [{
                "attribute_id": 10097,
                "attribute_name": "Название цвета",
                "value": "卡其色1.9L",
            }],
        }]}
        items = build_import_items(
            draft, config, ["https://images.example.test/main.png"], variant_grouping=grouping
        )
        color_values = [
            value["value"]
            for attribute in items[0]["attributes"]
            if attribute["id"] == 10097
            for value in attribute["values"]
        ]
        self.assertEqual(color_values, ["хаки"])

    def test_variant_color_name_without_color_is_omitted_before_payload(self):
        draft = {
            "title": "Портативный блендер",
            "description_category_id": 17000001,
            "type_id": 90001,
            "skus": [{"source_sku_id": "sku-1", "offer_id": "P000009-sku-1"}],
        }
        config = {
            "sku_prices": [{"source_sku_id": "sku-1", "price": "900.00"}],
            "sku_colors": [],
            "package_dimensions": {"length_mm": 300, "width_mm": 200, "height_mm": 150},
            "package_weight": {"value_g": 449},
            "brand": {"attribute_id": 31, "dictionary_value_id": 1, "value": "Нет бренда"},
            "model_name": {"attribute_id": 32, "value": "553829441028"},
            "type": {"attribute_id": 33, "dictionary_value_id": 2, "value": "Блендер"},
            "currency_code": "RUB", "vat": "0", "old_price": None,
        }
        grouping = {"variants": [{
            "sku_id": "sku-1",
            "variant_attribute_values": [{
                "attribute_id": 10097,
                "attribute_name": "Название цвета",
                "value": "601–800 мл",
            }],
        }]}
        items = build_import_items(
            draft, config, ["https://images.example.test/main.png"], variant_grouping=grouping
        )
        self.assertFalse(any(attribute["id"] == 10097 for attribute in items[0]["attributes"]))

    def test_p000021_style_payload_repair_removes_false_cable_length_and_keeps_height(self):
        """Regression for the local P000021 failure: height is not cable length."""
        draft = {
            "title": "Электрическая щетка для гриля",
            "description_category_id": 1,
            "type_id": 2,
            "skus": [{"source_sku_id": "sku-a", "offer_id": "P000021-sku-a"}],
        }
        config = {
            "sku_prices": [{"source_sku_id": "sku-a", "price": "1000"}],
            "sku_colors": [],
            "brand": {"attribute_id": 85, "dictionary_value_id": 1, "value": "Нет бренда"},
            "model_name": {"attribute_id": 9048, "value": "123456789012"},
            "type": {"attribute_id": 8229, "dictionary_value_id": 2, "value": "Щетка для гриля"},
            "currency_code": "RUB", "vat": "0", "old_price": None,
            "package_dimensions": {"length_mm": 300, "width_mm": 200, "height_mm": 150},
            "package_weight": {"value_g": 1200},
        }
        metadata = {"attributes": [
            {"attribute_id": 5391, "attribute_name": "Длина шнура, м", "required": False, "type": "Decimal"},
            {"attribute_id": 4788, "attribute_name": "Высота, см", "required": False, "type": "Decimal", "constraints": {"minimum": 1, "maximum": 100}},
            {"attribute_id": 23171, "attribute_name": "#Хештеги", "required": False, "type": "String"},
            {"attribute_id": 9163, "attribute_name": "Пол", "required": True, "type": "String", "allowed_values": [
                {"id": 22880, "value": "Мужской"},
                {"id": 22881, "value": "Женский"},
            ]},
        ]}
        final_attributes = {
            "common_attributes": [
                {
                    "attribute_id": 23171,
                    "value": "#щеткадлягриля #таннес #модель_два #чисткарешетки #щетка100",
                    "target_value": "#щеткадлягриля #таннес #модель_два #чисткарешетки #щетка100",
                },
                {
                    "attribute_id": 9163,
                    "value": "Мужской; Женский",
                    "target_value": "Мужской; Женский",
                    "dictionary_value_id": None,
                    "dictionary_values": [
                        {"dictionary_value_id": 22880, "value": "Мужской"},
                        {"dictionary_value_id": 22881, "value": "Женский"},
                    ],
                },
            ],
            "attributes_by_sku": {"sku-a": [
                {"attribute_id": 5391, "value": 260, "target_value": 260, "canonical_value": 260,
                 "source": "source.product_attributes.sku_measurement_table", "evidence": ["Высота товара 260 мм"]},
                {"attribute_id": 4788, "value": 11, "target_value": 11, "canonical_value": 110,
                 "source": "source.product_attributes.sku_measurement_table", "evidence": ["Высота товара 110 мм"]},
            ]},
            "sku_measurements": {"sku-a": {
                "package_dimensions": {"canonical_value": {"length_mm": 300, "width_mm": 200, "height_mm": 150}},
                "package_weight": {"canonical_value": 1200},
            }},
        }
        repairs = []
        items = build_import_items(
            draft, config, ["https://images.example.test/main.png"],
            final_attributes=final_attributes, category_metadata=metadata,
            source={"brand": "Таннес"}, field_repair_log=repairs,
        )
        attributes = {item["id"]: item["values"] for item in items[0]["attributes"]}
        self.assertNotIn(5391, attributes)
        self.assertEqual(attributes[4788][0]["value"], "11")
        self.assertEqual(attributes[23171], [{"value": "#щеткадлягриля"}, {"value": "#чисткарешетки"}])
        self.assertEqual(attributes[9163], [
            {"dictionary_value_id": 22880, "value": "Мужской"},
            {"dictionary_value_id": 22881, "value": "Женский"},
        ])
        self.assertTrue(any(item["attribute_id"] == 5391 for item in repairs))
        self.assertTrue(all("stock" not in item and "warehouse_id" not in item for item in items))

        # An optional numeric field outside the current live Ozon range is
        # removed automatically; it is never rewritten into a fabricated value.
        out_of_range = copy.deepcopy(final_attributes)
        out_of_range["attributes_by_sku"]["sku-a"][1]["value"] = 101
        out_of_range["attributes_by_sku"]["sku-a"][1]["target_value"] = 101
        range_repairs = []
        range_items = build_import_items(
            draft, config, ["https://images.example.test/main.png"],
            final_attributes=out_of_range, category_metadata=metadata,
            source={"brand": "Таннес"}, field_repair_log=range_repairs,
        )
        range_attributes = {item["id"]: item["values"] for item in range_items[0]["attributes"]}
        self.assertNotIn(4788, range_attributes)
        self.assertTrue(any(item["attribute_id"] == 4788 for item in range_repairs))

    def test_import_payload_uses_canonical_tags_file_over_stale_compiled_tags(self):
        draft = {
            "title": "Кофеварка для колд брю",
            "description_category_id": 1,
            "type_id": 2,
            "skus": [{
                "source_sku_id": "sku-a",
                "source_sku_name": "9号冰滴",
                "offer_id": "P000123-sku-a",
            }],
        }
        config = {
            "sku_prices": [{"source_sku_id": "sku-a", "price": "1000"}],
            "sku_colors": [],
            "brand": {"attribute_id": 85, "dictionary_value_id": 1, "value": "Нет бренда"},
            "model_name": {"attribute_id": 9048, "value": "coffee-model"},
            "type": {"attribute_id": 8229, "dictionary_value_id": 2, "value": "Кофеварка"},
            "currency_code": "RUB",
            "vat": "0",
            "old_price": None,
            "package_dimensions": {"length_mm": 300, "width_mm": 200, "height_mm": 150},
            "package_weight": {"value_g": 1200},
        }
        metadata = {"attributes": [
            {"attribute_id": 23171, "attribute_name": "#Хештеги", "required": False, "type": "String", "is_collection": False},
        ]}
        stale_final_attributes = {
            "common_attributes": [{
                "attribute_id": 23171,
                "value": "#канистрадлятоплива #металлическаяканистра",
                "target_value": "#канистрадлятоплива #металлическаяканистра",
            }],
        }
        repairs = []

        items = build_import_items(
            draft,
            config,
            ["https://images.example.test/main.png"],
            final_attributes=stale_final_attributes,
            category_metadata=metadata,
            field_repair_log=repairs,
            canonical_tags=["#кофеварка", "#колдбрю"],
        )

        attributes = {item["id"]: item["values"] for item in items[0]["attributes"]}
        self.assertEqual(attributes[23171], [{"value": "#кофеварка #колдбрю"}])
        self.assertTrue(any(item.get("reason") == "canonical_ozon_tags_file" for item in repairs))

    def test_active_client_rejects_inventory_and_unlisted_endpoints(self):
        transport = RecordingTransport()
        uploader = client(transport)
        for endpoint in ("/v2/products/stocks", "/v1/product/pictures/import", "/v2/product/update"):
            with self.assertRaisesRegex(ValueError, "uploader allowlist"):
                uploader._post_json(endpoint, {})
        self.assertEqual(transport.calls, [])

    def test_active_task_id_blocks_second_write_before_any_product_read(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P000123"
            output = product / "output"
            output.mkdir(parents=True)
            (product / "status.json").write_text(json.dumps({"status": "HANDED_OFF_TO_OZON"}))
            (output / "ozon-idempotency.json").write_text(json.dumps({
                "task_id": 70001,
                "api_write_completed": True,
            }))
            transport = RecordingTransport()
            with patch.dict(os.environ, {"UPLOAD_MODE": "production"}):
                with self.assertRaisesRegex(UploadGateError, "禁止重复提交"):
                    execute_upload(product, client(transport))
            self.assertEqual(transport.calls, [])

    def test_active_existing_offer_updates_and_unchanged_content_skips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "products/P000123"
            output = product / "output"
            image = product / "input/main.png"
            output.mkdir(parents=True)
            image.parent.mkdir(parents=True)
            image.write_bytes(b"real-image")
            draft = {
                "title": "Контейнер для хранения", "description": "Описание",
                "description_category_id": 17000001, "type_id": 90001,
                "images": [{"slot": "main-sku-1", "role": "variant_main", "path": "products/P000123/input/main.png"}],
                "skus": [{
                    "source_sku_id": "sku-1", "offer_id": "P000123-sku-1",
                    "display_name_ru": "3 л",
                }],
            }
            attributes = {"attributes": []}
            colors = {"variants": [{"sku_id": "sku-1", "image": "products/P000123/input/main.png"}]}
            config = {"currency_code": "RUB", "sku_prices": [{"source_sku_id": "sku-1", "price": "900.00"}]}
            live = {"items": [{"offer_id": "P000123-sku-1", "id": 50001}]}
            first = build_product_exists_check(product, draft, attributes, colors, config, live_response=live)
            self.assertEqual(first["action"], "update")
            self.assertEqual(first["offers"][0]["action"], "update")
            (output / "ozon-last-upload-hashes.json").write_text(
                json.dumps(first["current_hashes"], ensure_ascii=False)
            )
            second = build_product_exists_check(product, draft, attributes, colors, config, live_response=live)
            self.assertEqual(second["action"], "skip")


class LiveImageGateTest(unittest.TestCase):
    def test_stage_images_keeps_color_sample_alias_when_sku_main_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P999998"
            source = product / "output/generated-images/variant-main/sku-green.png"
            source.parent.mkdir(parents=True)
            from PIL import Image

            Image.new("RGB", (900, 1200), color=(42, 96, 68)).save(source)
            draft = {
                "images": [{
                    "slot": "main-sku-green",
                    "role": "main",
                    "variant_scope": "sku",
                    "source_sku_id": "sku-green",
                    "path": "products/P999998/output/generated-images/variant-main/sku-green.png",
                }],
            }
            color_variants = {
                "variants": [{
                    "sku_id": "sku-green",
                    "status": "mapped",
                    "image": "products/P999998/output/generated-images/variant-main/sku-green.png",
                }],
            }

            manifest = stage_images(product, draft, "2026-07-30T00:00:00Z", color_variants)

            self.assertEqual(validate(manifest, SCHEMAS["images"]), [])
            by_role = {item["role"]: item for item in manifest["images"]}
            self.assertIn("variant_main", by_role)
            self.assertIn("color", by_role)
            self.assertEqual(by_role["color"]["slot"], "color-sku-green")
            self.assertEqual(by_role["color"]["source_sku_id"], "sku-green")
            self.assertEqual(by_role["color"]["local_path"], by_role["variant_main"]["local_path"])
            self.assertEqual(by_role["color"]["staged_name"], by_role["variant_main"]["staged_name"])
            self.assertEqual(by_role["color"]["sha256"], by_role["variant_main"]["sha256"])

    def test_stage_images_adds_jlc_global_watermark_to_staged_copy_only(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P999999"
            source = product / "output/generated-images/detail/detail-001.png"
            source.parent.mkdir(parents=True)
            from PIL import Image

            Image.new("RGB", (900, 1200), color=(238, 241, 235)).save(source)
            before_hash = sha256_file(source)
            draft = {
                "images": [{
                    "slot": "detail-001",
                    "role": "detail",
                    "path": "products/P999999/output/generated-images/detail/detail-001.png",
                }],
            }

            manifest = stage_images(product, draft, "2026-07-27T00:00:00Z")

            item = manifest["images"][0]
            staged = product / "output/ozon-image-staging" / item["staged_name"]
            self.assertEqual(validate(manifest, SCHEMAS["images"]), [])
            self.assertTrue(staged.is_file())
            self.assertEqual(sha256_file(source), before_hash)
            self.assertNotEqual(sha256_file(staged), before_hash)
            self.assertEqual(item["sha256"], sha256_file(staged))
            self.assertTrue(item["watermark_applied"])

    def test_stale_draft_qc_status_does_not_block_current_passed_images(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P000018"
            output = product / "output"
            image_root = output / "generated-images"
            (image_root / "variant-main").mkdir(parents=True)
            (image_root / "detail").mkdir(parents=True)
            from PIL import Image
            import io

            buffer = io.BytesIO()
            Image.new("RGB", (16, 16), color=(240, 240, 240)).save(buffer, format="PNG")
            png = buffer.getvalue()
            slots = [("main-sku-1", "variant-main/sku-1.png", "sku-1")]
            slots.extend((f"detail-{index:03d}", f"detail/detail-{index:03d}.png", "all") for index in range(1, 9))
            for _slot, rel, _sku in slots:
                (image_root / rel).write_bytes(png)
            plan = {
                "main_images": [{
                    "slot": "main-sku-1",
                    "source_sku_id": "sku-1",
                    "output_path": "products/P000018/output/generated-images/variant-main/sku-1.png",
                    "status": "planned",
                }],
                "detail_images": [
                    {
                        "slot": f"detail-{index:03d}",
                        "source_sku_id": "all",
                        "output_path": f"products/P000018/output/generated-images/detail/detail-{index:03d}.png",
                        "status": "planned",
                    }
                    for index in range(1, 9)
                ],
            }
            (output / "image-plan.json").write_text(json.dumps(plan), encoding="utf-8")
            (output / "image-qc-report.json").write_text(json.dumps({
                "decision": "pass",
                "images_checked": [
                    {"slot": slot, "path": f"products/P000018/output/generated-images/{rel}"}
                    for slot, rel, _sku in slots
                ],
            }), encoding="utf-8")
            draft = {
                "category": {"metadata_source": "ozon_seller_api"},
                "description_category_id": 1,
                "type_id": 2,
                "stock": {"quantity": None, "warehouse_id": "unknown"},
                "skus": [{"source_sku_id": "sku-1", "stock": None}],
                "images": [
                    {
                        "slot": slot,
                        "role": "main" if slot.startswith("main") else "detail",
                        "path": f"products/P000018/output/generated-images/{rel}",
                        "qc_status": "not_checked",
                    }
                    for slot, rel, _sku in slots
                ],
            }
            (output / "ozon-draft.json").write_text(json.dumps(draft), encoding="utf-8")
            config = {
                "shop_name": "zhonglian1",
                "brand": {"attribute_id": 31, "dictionary_value_id": 1, "value": "Нет бренда"},
                "type": {"attribute_id": 32, "dictionary_value_id": 2, "value": "Контейнер"},
                "sku_colors": [],
                "sku_prices": [{"source_sku_id": "sku-1", "price": "100.00"}],
                "currency_code": "CNY",
                "vat": "0",
                "stock_mode": "not_set",
                "product_dimensions": {"length_mm": 100, "width_mm": 90, "height_mm": 80},
                "package_dimensions": {"length_mm": 110, "width_mm": 100, "height_mm": 90},
                "product_weight": {"value_g": 100},
                "package_weight": {"value_g": 120},
            }
            metadata = {"category_id": 1, "type_id": 2, "attributes": [
                {"attribute_id": 31, "allowed_values": [{"id": 1, "value": "Нет бренда"}]},
                {"attribute_id": 32, "allowed_values": [{"id": 2, "value": "Контейнер"}]},
            ]}
            manifest = {
                "images": [
                    {
                        "slot": slot,
                        "role": "variant_main" if slot.startswith("main") else "detail",
                        "local_path": f"products/P000018/output/generated-images/{rel}",
                        "public_url": "https://images.example.test/image.png",
                    }
                    for slot, rel, _sku in slots
                ],
            }

            preflight = build_preflight(
                product, draft, {"task_authorized": True}, config, metadata, manifest, "2026-07-20T00:00:00Z"
            )
            generated = next(item for item in preflight["checks"] if item["name"] == "generated_images")
            self.assertTrue(generated["passed"])
            self.assertTrue(all(item["qc_status"] == "pass" for item in draft["images"]))

    def test_current_image_gate_blocks_missing_detail_images_before_write(self):
        """The upload entrypoint must re-check image-plan files before any write."""
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P000005"
            output = product / "output"
            output.mkdir(parents=True)
            main = product / "output/generated-images/variant-main/sku-1.png"
            main.parent.mkdir(parents=True)
            main.write_bytes(bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                "0000000d49444154789c6360f8cfc000000301010018dd8db40000000049454e44ae426082"
            ))
            plan = {
                "main_images": [{"slot": "main-sku-1", "source_sku_id": "sku-1", "output_path": "output/generated-images/variant-main/sku-1.png", "status": "generated"}],
                "detail_images": [{"slot": f"detail-{index:03d}", "output_path": f"output/generated-images/detail/detail-{index:03d}.png", "status": "generated"} for index in range(1, 9)],
            }
            (output / "image-plan.json").write_text(json.dumps(plan), encoding="utf-8")
            (output / "ozon-draft.json").write_text(json.dumps({"skus": [{"source_sku_id": "sku-1"}]}), encoding="utf-8")
            (product / "status.json").write_text(json.dumps({"status": "WAITING_MANUAL_REVIEW"}), encoding="utf-8")
            transport = RecordingTransport()
            with patch.dict(os.environ, {"UPLOAD_MODE": "production"}):
                with self.assertRaises(UploadGateError):
                    execute_upload(product, client(transport))
            image_gate = current_upload_image_gate(product)
            self.assertEqual(image_gate["status"], "FAIL")
            self.assertFalse(image_gate["passed"])
            self.assertEqual(len(transport.calls), 0)
            self.assertFalse(current_image_completeness(product)["passed"])


if __name__ == "__main__":
    unittest.main()
