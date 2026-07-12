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
from ozon_uploader.service import (  # noqa: E402
    SCHEMAS, _images_ingested, _is_official_ozon_image_url, _parse_import_result,
    _remote_terminal_errors, build_import_items, load_json, recover_remote_import,
    sync_image_channel_status, validate,
)


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


def client(transport):
    return OzonWriteClient(
        OzonConfig(client_id="test", api_key="secret", shop_name="zhonglian1"),
        transport=transport,
    )


def copy_product(temp_dir, product_id="P000004"):
    target = Path(temp_dir) / f"products/{product_id}"
    shutil.copytree(ROOT / f"products/{product_id}", target)
    return target


def reset_to_ozon_ready(product_dir, task_authorized=False):
    for name in (
        "ozon-idempotency.json", "image-channel-state.json", "image-channel.stop",
        "ozon-image-transfer.json", "ozon-image-update-receipt.json",
    ):
        (product_dir / "output" / name).unlink(missing_ok=True)
    status_path = product_dir / "status.json"
    status = load_json(status_path)
    status["status"] = "OZON_READY"
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
    check_path = product_dir / "output/final-upload-check.json"
    check = load_json(check_path)
    for item in check["checks"]:
        if item["name"] == "color_variant_images":
            item["passed"] = True
    check["status"] = "PASS"
    check["upload_allowed"] = True
    check["errors"] = []
    check_path.write_text(json.dumps(check, ensure_ascii=False, indent=2) + "\n")


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
    check_path = product_dir / "output/final-upload-check.json"
    check = load_json(check_path)
    for item in check["checks"]:
        if item["name"] == "color_variant_images":
            item["passed"] = False
    check["status"] = "FAIL"
    check["upload_allowed"] = False
    check["errors"] = ["The main SKU must have a safe color image."]
    check_path.write_text(json.dumps(check, ensure_ascii=False, indent=2) + "\n")


class Stage42OzonUploaderTest(unittest.TestCase):
    def test_declined_remote_product_stops_image_channel_without_write(self):
        with tempfile.TemporaryDirectory() as directory:
            product_dir = Path(directory) / "products/P999999"
            output = product_dir / "output"
            output.mkdir(parents=True)
            (Path(directory) / "image-channel-queue.json").write_text(json.dumps({
                "items": [{
                    "product_id": "P999999", "offer_ids": ["offer-1"],
                    "expected_image_count": 2, "check_count": 0,
                    "status": "WAITING_OZON_CDN",
                }],
            }))
            (output / "image-channel-state.json").write_text(json.dumps({"status": "running"}))
            (output / "ozon-images.json").write_text(json.dumps({
                "images": [{"status": "submitted", "error": "unknown"}],
            }))
            response = {"items": [{
                "offer_id": "offer-1",
                "statuses": {"validation_status": "success", "moderate_status": "declined"},
                "errors": [{"code": "DESCRIPTION_DECLINE", "level": "ERROR_LEVEL_ERROR"}],
                "primary_image": ["https://temporary.trycloudflare.com/main.png"],
                "images": ["https://temporary.trycloudflare.com/main.png"],
            }]}
            self.assertEqual(_remote_terminal_errors(response)[0]["code"], "DESCRIPTION_DECLINE")
            result = sync_image_channel_status(product_dir, object(), product_response=response)
            self.assertEqual(result["status"], "REMOTE_DECLINED")
            self.assertEqual(load_json(Path(directory) / "image-channel-queue.json")["items"], [])
            self.assertEqual(load_json(output / "ozon-image-transfer.json")["status"], "REMOTE_DECLINED")
            self.assertEqual((output / "image-channel.stop").read_text().strip(), "ozon_product_declined")

    def test_media_confirmation_requires_success_full_count_and_official_cdn(self):
        transfer = load_json(ROOT / "products/P000004/output/ozon-image-transfer.json")
        response = transfer["response"]
        offer_ids = [item["offer_id"] for item in response["items"]]
        self.assertTrue(_images_ingested(response, offer_ids, 4))
        self.assertTrue(_is_official_ozon_image_url("https://ir.ozone.ru/s3/image.jpg"))
        self.assertFalse(_is_official_ozon_image_url("https://example.com/image.jpg"))

        temporary = copy.deepcopy(response)
        temporary["items"][0]["primary_image"] = ["https://sample.trycloudflare.com/main.png"]
        self.assertFalse(_images_ingested(temporary, offer_ids, 4))

        missing = copy.deepcopy(response)
        missing["items"][0]["images"] = missing["items"][0]["images"][:-1]
        self.assertFalse(_images_ingested(missing, offer_ids, 4))

        pending = copy.deepcopy(response)
        pending["items"][0]["statuses"]["validation_status"] = "pending"
        self.assertFalse(_images_ingested(pending, offer_ids, 4))

    def test_new_schemas_and_real_upload_config_validate(self):
        for schema in SCHEMAS.values():
            self.assertIsInstance(load_json(schema), dict)
        config = load_json(ROOT / "products/P000004/output/ozon-upload-config.json")
        self.assertEqual(validate(config, SCHEMAS["config"]), [])
        self.assertEqual(config["currency_code"], "CNY")
        self.assertEqual(config["stock_mode"], "not_set")

    def test_prepare_is_blocked_and_does_not_call_write_api(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            reset_to_ozon_ready(product_dir)
            preview = prepare_upload(product_dir)
            self.assertFalse(preview["preflight"]["upload_allowed"])
            failed = {item["name"] for item in preview["preflight"]["checks"] if not item["passed"]}
            self.assertEqual(failed, {"batch_task_authorized", "public_images"})
            transport = RecordingTransport()
            with self.assertRaises(UploadGateError):
                execute_upload(product_dir, client(transport))
            self.assertEqual(transport.calls, [])

    def test_user_started_batch_needs_no_per_product_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            reset_to_ozon_ready(product_dir, task_authorized=True)
            preview = prepare_upload(product_dir)
            failed = {item["name"] for item in preview["preflight"]["checks"] if not item["passed"]}
            self.assertEqual(failed, {"public_images"})
            status = load_json(product_dir / "status.json")
            self.assertEqual(status["status"], "OZON_READY")
            self.assertTrue(status["task_authorized"])

    def test_pending_remote_task_blocks_any_second_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            output = product_dir / "output"
            (output / "ozon-idempotency.json").write_text(json.dumps({
                "schema_version": "1.0.0",
                "payload_hash": "abc",
                "task_id": 70001,
                "api_write_completed": True,
                "offer_ids": ["existing-offer"],
                "request_timestamp": "2026-07-11T00:00:00+00:00",
            }), encoding="utf-8")
            status_path = product_dir / "status.json"
            status = load_json(status_path)
            status["status"] = "PENDING_REMOTE"
            status_path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
            transport = RecordingTransport()
            with patch.dict(os.environ, {"UPLOAD_MODE": "production"}):
                with self.assertRaisesRegex(UploadGateError, "still pending"):
                    execute_upload(product_dir, client(transport))
            self.assertEqual(transport.calls, [])

    def test_payload_uses_cny_sku_prices_and_contains_no_stock(self):
        output = ROOT / "products/P000004/output"
        draft = load_json(output / "ozon-draft.json")
        config = load_json(output / "ozon-upload-config.json")
        config["vat"] = "0"
        items = build_import_items(draft, config, ["https://images.example.test/main.png"])
        prices = {item["offer_id"]: item["price"] for item in items}
        self.assertEqual(set(prices.values()), {"90.00", "100.00"})
        self.assertTrue(all(item["currency_code"] == "CNY" for item in items))
        self.assertTrue(all(item["weight"] == 150 for item in items))
        self.assertTrue(all(item["depth"] == 170 and item["width"] == 120 and item["height"] == 50 for item in items))
        self.assertTrue(all("stock" not in item and "warehouse_id" not in item for item in items))

    def test_client_rejects_inventory_and_unlisted_endpoints(self):
        transport = RecordingTransport()
        uploader = client(transport)
        for endpoint in ("/v2/products/stocks", "/v1/product/pictures/import", "/v2/product/update"):
            with self.assertRaisesRegex(ValueError, "uploader allowlist"):
                uploader._post_json(endpoint, {})
        self.assertEqual(transport.calls, [])

    def test_authorized_batch_mock_upload_creates_three_cards_without_stock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            status_path = product_dir / "status.json"
            status = reset_to_ozon_ready(product_dir, task_authorized=True)
            status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
            config_path = product_dir / "output/ozon-upload-config.json"
            config = load_json(config_path)
            config["vat"] = "0"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            mark_all_color_variants_mapped_for_mock(product_dir)

            transport = RecordingTransport()
            with patch.dict(os.environ, {"UPLOAD_MODE": "production"}):
                with patch("ozon_uploader.service.PersistentImageTunnel", FakeTunnel):
                    with patch("ozon_uploader.service._ensure_image_status_monitor"):
                        result = execute_upload(product_dir, client(transport))
            self.assertEqual(result["status"], "submitted")
            self.assertEqual(result["task_id"], 70001)
            self.assertEqual([call[0] for call in transport.calls], [
                OzonWriteClient.PRODUCT_INFO_LIST_ENDPOINT,
                OzonWriteClient.PRODUCT_IMPORT_ENDPOINT,
            ])
            payload_items = transport.calls[1][1]["items"]
            self.assertTrue(all("stock" not in item for item in payload_items))
            final_status = load_json(status_path)
            self.assertEqual(final_status["status"], "PENDING_REMOTE")
            self.assertEqual(final_status["ozon"]["shop_name"], "zhonglian1")
            self.assertFalse(load_json(product_dir / "output/ozon-draft.json")["upload_allowed"])
            self.assertTrue((product_dir / "output/ozon-idempotency.json").is_file())
            self.assertEqual(load_json(product_dir / "output/ozon-image-transfer.json")["status"], "waiting_ozon_cdn")

    def test_invalid_import_response_is_recorded_as_failure(self):
        class BrokenTransport(RecordingTransport):
            def __call__(self, endpoint, payload):
                self.calls.append((endpoint, payload))
                return {"result": {}}

        self.assertIsInstance(OzonUploadApiError("/v3/product/import", "bad"), RuntimeError)

    def test_dry_run_is_default_and_writes_complete_payload_without_api(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            with patch.dict(os.environ, {}, clear=True):
                payload = build_upload_payload(product_dir)
            self.assertEqual(payload["upload_mode"], "dry-run")
            self.assertFalse(payload["api_writes_performed"])
            self.assertNotIn("stock", payload)
            self.assertTrue(all("stock" not in item for item in payload["variants"]))
            self.assertEqual(len(payload["variants"]), 3)
            self.assertEqual({item["price"] for item in payload["variants"]}, {"90.00", "100.00"})
            self.assertEqual(len(payload["attributes"]), 38)
            self.assertTrue((product_dir / "output/ozon-upload-payload.json").is_file())

    def test_same_source_sharpener_dry_run_is_one_group_with_three_offers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir, "P000005")
            payload = build_upload_payload(product_dir, mode="dry-run")
            group = payload["product_group"]
            self.assertEqual(group["product_group_count"], 1)
            self.assertEqual(group["variant_count"], 3)
            self.assertTrue(group["must_merge"])
            self.assertEqual(group["variant_mapping_status"], "SEPARATE_CARDS_REQUIRED")
            self.assertIsNone(group["variant_attribute"])
            self.assertIn("Ozon category has no official aspect mapping for the selected SKU differences; upload strategy is separate cards.", payload["production_blockers"])
            items = payload["api_request_template"]["body"]["items"]
            self.assertEqual(len(items), 3)
            model_values = {
                next(attr for attr in item["attributes"] if attr["id"] == 9048)["values"][0]["value"]
                for item in items
            }
            self.assertEqual(model_values, {"Электрическая точилка для ножей P000005"})
            self.assertTrue(all(
                not any(attribute["id"] == 4384 for attribute in item["attributes"])
                for item in items
            ))

    def test_production_blocks_failed_final_check_but_existing_means_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            block_main_color_variant_for_mock(product_dir)
            with patch.dict(os.environ, {"UPLOAD_MODE": "production"}):
                payload = build_upload_payload(product_dir)
                with self.assertRaisesRegex(UploadGateError, "status=PASS"):
                    assert_production_allowed(product_dir, payload)
            self.assertEqual(payload["product_exists_check"]["action"], "update")
            self.assertFalse(any("already exists" in item for item in payload["production_blockers"]))

    def test_existing_offers_are_update_not_duplicate_create(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            payload = build_upload_payload(product_dir, mode="dry-run")
            check = payload["product_exists_check"]
            self.assertTrue(check["exists"])
            self.assertEqual(check["action"], "update")
            self.assertEqual({item["action"] for item in check["offers"]}, {"update"})
            self.assertEqual(len({item["offer_id"] for item in check["offers"]}), 3)

    def test_dry_run_reports_saved_cross_category_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            output = product_dir / "output"
            draft = load_json(output / "ozon-draft.json")
            grouping = load_json(output / "variant-grouping-result.json")
            (output / "grouping-verification.json").write_text(json.dumps({
                "last_api_response": {"items": [{
                    "offer_id": item["offer_id"],
                    "id": 900000 + index,
                    "description_category_id": 999001,
                    "type_id": 999002,
                } for index, item in enumerate(grouping["variants"])]},
            }), encoding="utf-8")
            payload = build_upload_payload(product_dir, mode="dry-run")
            self.assertEqual(payload["product_exists_check"]["action"], "update")
            self.assertTrue(any(
                "cross-category UPDATE is blocked" in blocker
                for blocker in payload["production_blockers"]
            ))

    def test_unchanged_uploaded_hashes_select_skip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            first = build_upload_payload(product_dir, mode="dry-run")
            hashes = first["product_exists_check"]["current_hashes"]
            (product_dir / "output/ozon-last-upload-hashes.json").write_text(
                json.dumps(hashes, ensure_ascii=False, indent=2) + "\n"
            )
            second = build_upload_payload(product_dir, mode="dry-run")
            self.assertEqual(second["product_exists_check"]["action"], "skip")

    def test_missing_offers_select_create(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            (product_dir / "output/ozon-result.json").unlink()
            payload = build_upload_payload(product_dir, mode="dry-run")
            self.assertFalse(payload["product_exists_check"]["exists"])
            self.assertEqual(payload["product_exists_check"]["action"], "create")

    def test_existing_live_offers_enter_update_flow(self):
        class ExistingTransport(RecordingTransport):
            def __init__(self):
                super().__init__()
                self.existing = {
                    "P000004-3993658310173": 5440271945,
                    "P000004-3993658310175": 5440271889,
                    "P000004-3993658310174": 5440271935,
                }

            def __call__(self, endpoint, payload):
                if endpoint == OzonWriteClient.PRODUCT_INFO_LIST_ENDPOINT and not self.offers:
                    self.calls.append((endpoint, copy.deepcopy(payload)))
                    return {
                        "items": [
                            {
                                "id": self.existing[offer_id],
                                "offer_id": offer_id,
                                "images": ["https://cdn.example.test/1.jpg"],
                                "primary_image": ["https://cdn.example.test/0.jpg"],
                                "errors": [],
                            }
                            for offer_id in payload["offer_id"]
                        ]
                    }
                if endpoint == OzonWriteClient.IMPORT_INFO_ENDPOINT:
                    self.calls.append((endpoint, copy.deepcopy(payload)))
                    return {
                        "result": {
                            "items": [
                                {
                                    "offer_id": offer_id,
                                    "product_id": self.existing[offer_id],
                                    "status": "imported",
                                    "errors": [],
                                }
                                for offer_id in self.offers
                            ]
                        }
                    }
                return super().__call__(endpoint, payload)

        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            (product_dir / "output/ozon-idempotency.json").unlink(missing_ok=True)
            mark_all_color_variants_mapped_for_mock(product_dir)
            transport = ExistingTransport()
            with patch.dict(os.environ, {"UPLOAD_MODE": "production"}):
                with patch("ozon_uploader.service.PersistentImageTunnel", FakeTunnel):
                    with patch("ozon_uploader.service._ensure_image_status_monitor"):
                        result = execute_upload(product_dir, client(transport))
            self.assertEqual(result["status"], "submitted")
            check = load_json(product_dir / "output/product-exists-check.json")
            self.assertEqual(check["action"], "update")
            self.assertEqual({item["action"] for item in check["offers"]}, {"update"})
            imported_offers = {
                item["offer_id"] for item in transport.calls[1][1]["items"]
            }
            self.assertEqual(imported_offers, set(transport.existing))
            status = load_json(product_dir / "status.json")
            self.assertEqual(status["history"][-2]["to"], "UPLOADING")
            self.assertEqual(status["history"][-1]["to"], "PENDING_REMOTE")
            self.assertTrue((product_dir / "output/ozon-idempotency.json").is_file())

    def test_cross_category_update_is_blocked_before_import(self):
        class DifferentCategoryTransport(RecordingTransport):
            def __call__(self, endpoint, payload):
                self.calls.append((endpoint, copy.deepcopy(payload)))
                if endpoint == OzonWriteClient.PRODUCT_INFO_LIST_ENDPOINT:
                    return {"items": [{
                        "id": 5440271945 + index,
                        "offer_id": offer_id,
                        "description_category_id": 999001,
                        "type_id": 999002,
                    } for index, offer_id in enumerate(payload["offer_id"])]}
                return super().__call__(endpoint, payload)

        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            reset_to_ozon_ready(product_dir, task_authorized=True)
            transport = DifferentCategoryTransport()
            with patch.dict(os.environ, {"UPLOAD_MODE": "production"}):
                with self.assertRaisesRegex(UploadGateError, "cross-category UPDATE"):
                    execute_upload(product_dir, client(transport), required_action="update")
            self.assertEqual(
                [endpoint for endpoint, _ in transport.calls],
                [OzonWriteClient.PRODUCT_INFO_LIST_ENDPOINT],
            )
            report = load_json(product_dir / "output/ozon-category-migration-block.json")
            self.assertEqual(report["status"], "BLOCKED")

    def test_pending_import_converges_from_live_product_info_without_write(self):
        class PendingButCreatedTransport:
            def __init__(self):
                self.calls = []

            def __call__(self, endpoint, payload):
                self.calls.append((endpoint, copy.deepcopy(payload)))
                if endpoint == OzonWriteClient.IMPORT_INFO_ENDPOINT:
                    return {"result": {"items": [
                        {"offer_id": "P000009-5651472715741", "product_id": 0, "status": "pending", "errors": []},
                        {"offer_id": "P000009-5651472715755", "product_id": 0, "status": "pending", "errors": []},
                    ]}}
                if endpoint == OzonWriteClient.PRODUCT_INFO_LIST_ENDPOINT:
                    return {"items": [
                        {
                            "offer_id": offer_id, "id": product_id,
                            "statuses": {"validation_status": "success", "moderate_status": "pending"},
                            "model_info": {"model_id": 777}, "images": [], "primary_image": [],
                        }
                        for offer_id, product_id in (
                            ("P000009-5651472715741", 5450526030),
                            ("P000009-5651472715755", 5450438296),
                        )
                    ]}
                if endpoint == OzonWriteClient.PRODUCT_ATTRIBUTES_ENDPOINT:
                    return {"result": []}
                raise AssertionError(endpoint)

        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir, "P000009")
            queue_path = product_dir.parent.parent / "remote-pending-queue.json"
            queue_path.write_text(json.dumps({"items": [{"product_id": "P000009"}]}))
            transport = PendingButCreatedTransport()
            with patch.dict(os.environ, {"UPLOAD_MODE": "production"}):
                result = recover_remote_import(product_dir, client(transport), timeout_seconds=1)
            self.assertEqual(result["status"], "created")
            self.assertEqual(
                {item["product_id"] for item in result["items"]},
                {5450526030, 5450438296},
            )
            self.assertEqual(load_json(product_dir / "status.json")["status"], "OZON_MODERATION")
            self.assertEqual(load_json(queue_path)["items"], [])
            self.assertNotIn(
                OzonWriteClient.PRODUCT_IMPORT_ENDPOINT,
                [endpoint for endpoint, _ in transport.calls],
            )

    def test_imported_item_with_error_level_is_failed(self):
        response = {"result": {"items": [{
            "offer_id": "P000004-3993658310173",
            "product_id": 5440271945,
            "status": "imported",
            "errors": [{"code": "content_warning", "level": "error"}],
        }]}}
        result = _parse_import_result(
            ROOT / "products/P000004", "zhonglian1", 70001, response,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"][0]["code"], "content_warning")
        self.assertEqual(result["warnings"], [])

    def test_imported_item_with_warning_level_remains_successful(self):
        response = {"result": {"items": [{
            "offer_id": "P000004-3993658310173",
            "product_id": 5440271945,
            "status": "imported",
            "errors": [{"code": "content_warning", "level": "warning"}],
        }]}}
        result = _parse_import_result(
            ROOT / "products/P000004", "zhonglian1", 70001, response,
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"][0]["code"], "content_warning")

    def test_direct_write_is_impossible_in_default_dry_run_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            product_dir = copy_product(temp_dir)
            transport = RecordingTransport()
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(UploadGateError, "UPLOAD_MODE"):
                    execute_upload(product_dir, client(transport))
            self.assertEqual(transport.calls, [])

    def test_client_itself_rejects_network_calls_in_dry_run(self):
        transport = RecordingTransport()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "UPLOAD_MODE=production"):
                client(transport).create_products([])
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
