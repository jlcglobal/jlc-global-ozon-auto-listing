import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Request
from fastapi.responses import JSONResponse


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("lan_workbench_app", APP_PATH)
workbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workbench)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class FakeRequest:
    def __init__(self, payload: dict):
        self.payload = payload

    async def json(self):
        return self.payload

    async def body(self):
        return json.dumps(self.payload).encode("utf-8")


class LanCollaborationTest(unittest.IsolatedAsyncioTestCase):
    def test_lan_filter_accepts_private_networks_and_rejects_public_ip(self):
        config = {"allowed_cidrs": list(workbench.DEFAULT_LAN_CIDRS)}
        self.assertTrue(workbench.client_ip_allowed("192.168.1.25", config))
        self.assertTrue(workbench.client_ip_allowed("10.10.0.8", config))
        self.assertFalse(workbench.client_ip_allowed("8.8.8.8", config))

    def test_running_batch_pid_returns_live_pid_and_removes_stale_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "batch.pid"
            current_path = root / "current-batch.json"
            log_path = root / "batch-runner.log"
            pid_path.write_text("12345", encoding="utf-8")
            write_json(current_path, {"batch_id": "B-LIVE", "pid": 12345})
            with (
                patch.object(workbench, "BATCH_PID_PATH", pid_path),
                patch.object(workbench, "CURRENT_BATCH_PATH", current_path),
                patch.object(workbench, "BATCH_LOG_PATH", log_path),
                patch.object(workbench, "_pid_is_alive", return_value=True),
            ):
                self.assertEqual(workbench.running_batch_pid(), 12345)
            self.assertTrue(current_path.exists())
            pid_path.write_text("12345", encoding="utf-8")
            write_json(current_path, {"batch_id": "B-STALE", "pid": 12345})
            with (
                patch.object(workbench, "BATCH_PID_PATH", pid_path),
                patch.object(workbench, "CURRENT_BATCH_PATH", current_path),
                patch.object(workbench, "BATCH_LOG_PATH", log_path),
                patch.object(workbench, "_pid_is_alive", return_value=False),
            ):
                self.assertIsNone(workbench.running_batch_pid())
            self.assertFalse(pid_path.exists())
            self.assertFalse(current_path.exists())
            self.assertIn("cleared stale batch runner", log_path.read_text(encoding="utf-8"))

    def test_running_batch_pid_clears_orphan_current_batch_without_pid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "batch.pid"
            current_path = root / "current-batch.json"
            log_path = root / "batch-runner.log"
            write_json(current_path, {"batch_id": "B-ORPHAN", "pid": 54321})
            with (
                patch.object(workbench, "BATCH_PID_PATH", pid_path),
                patch.object(workbench, "CURRENT_BATCH_PATH", current_path),
                patch.object(workbench, "BATCH_LOG_PATH", log_path),
            ):
                self.assertIsNone(workbench.running_batch_pid())
            self.assertFalse(current_path.exists())
            self.assertIn("cleared orphan current-batch", log_path.read_text(encoding="utf-8"))

    def test_busy_runner_persists_new_batch_in_central_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "logs/workbench-run-queue.json"
            batch = {"batch_id": "B-QUEUED", "product_count": 1, "products": [{"product_id": "P000001"}]}
            with (
                patch.object(workbench, "ROOT", root),
                patch.object(workbench, "WORKBENCH_RUN_QUEUE_PATH", queue_path),
                patch.object(workbench, "running_batch_pid", return_value=123),
                patch.object(workbench, "ensure_batch_dispatcher"),
            ):
                result = workbench.launch_or_enqueue_batch(batch, "single_product")
                saved = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["queue_position"], 1)
            self.assertEqual(saved["items"][0]["batch_id"], "B-QUEUED")

    def test_confirmed_upload_moves_to_front_without_stopping_active_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products_dir = root / "products"
            queue_path = root / "logs/workbench-run-queue.json"
            current_path = root / "logs/current-batch.json"
            safe_stop_path = root / "logs/safe-stop-request.json"
            write_json(products_dir / "P000001/status.json", {
                "status": "WAITING_MANUAL_REVIEW", "next_action": "manual_ozon_upload",
                "api_write_count": 0, "completed_steps": [
                    "collect_source", "validate_source", "product_analysis", "category_match",
                    "variant_rules", "measurements", "offer_exists_check", "upload_feasibility",
                    "product_positioning", "ecommerce_design", "russian_copy", "field_completion",
                    "image_plan", "image_generation", "image_qc",
                ],
                "pending_steps": ["ozon_upload"], "ozon": {"upload_status": "not_started"},
            })
            write_json(products_dir / "P000002/status.json", {
                "status": "PROCESSING", "current_step": "product_analysis",
                "next_action": "product_analysis", "api_write_count": 0,
                "ozon": {"upload_status": "not_started"},
            })
            upload_batch = {
                "batch_id": "B-UPLOAD", "status": "QUEUED", "product_count": 1,
                "auto_upload": True, "products": [{"product_id": "P000001"}],
            }
            running_batch = {
                "batch_id": "B-RUNNING", "status": "RUNNING", "product_count": 1,
                "auto_upload": False, "products": [{"product_id": "P000002"}],
            }
            write_json(root / "batches/B-RUNNING/batch.json", running_batch)
            write_json(current_path, {"batch_id": "B-RUNNING", "pid": 321})
            with (
                patch.object(workbench, "ROOT", root),
                patch.object(workbench, "PRODUCTS_DIR", products_dir),
                patch.object(workbench, "WORKBENCH_RUN_QUEUE_PATH", queue_path),
                patch.object(workbench, "CURRENT_BATCH_PATH", current_path),
                patch.object(workbench, "SAFE_STOP_REQUEST_PATH", safe_stop_path),
                patch.object(workbench, "running_batch_pid", return_value=321),
                patch.object(workbench, "ensure_batch_dispatcher"),
            ):
                result = workbench.launch_or_enqueue_batch(upload_batch, "manual_upload")

            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            upload_status = json.loads((products_dir / "P000001/status.json").read_text(encoding="utf-8"))
            saved_upload_batch = json.loads((root / "batches/B-UPLOAD/batch.json").read_text(encoding="utf-8"))
            self.assertEqual([item["batch_id"] for item in queue["items"]], ["B-UPLOAD"])
            self.assertEqual(queue["items"][0]["priority"], "manual_upload")
            self.assertFalse(safe_stop_path.exists())
            self.assertEqual(upload_status["next_action"], "ozon_upload")
            self.assertTrue(upload_status["task_authorized"])
            self.assertEqual(saved_upload_batch["execution_priority"], "manual_upload")
            self.assertTrue(result["priority_upload"])
            self.assertFalse(result["preemption_requested"])

    async def test_unversioned_stop_control_is_rejected_for_authorized_production_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_path = root / "logs/current-batch.json"
            safe_stop_path = root / "logs/safe-stop-request.json"
            write_json(root / "batches/B-RUNNING/owner.json", {
                "owner_id": "host-main",
                "device_id": "host-main",
            })
            write_json(current_path, {"batch_id": "B-RUNNING", "pid": 321})
            with (
                patch.object(workbench, "ROOT", root),
                patch.object(workbench, "CURRENT_BATCH_PATH", current_path),
                patch.object(workbench, "SAFE_STOP_REQUEST_PATH", safe_stop_path),
                patch.object(workbench, "running_batch_pid", return_value=321),
                patch.object(workbench, "current_operator", return_value={"operator_id": "host-main", "role": "owner"}),
            ):
                with self.assertRaises(workbench.HTTPException) as raised:
                    await workbench.control_batch(FakeRequest({"action": "stop"}))
            self.assertEqual(raised.exception.status_code, 409)
            self.assertFalse(safe_stop_path.exists())

    async def test_manual_toolbar_stop_writes_stop_request_for_authorized_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_path = root / "logs/current-batch.json"
            safe_stop_path = root / "logs/safe-stop-request.json"
            write_json(root / "batches/B-RUNNING/batch.json", {
                "batch_id": "B-RUNNING",
                "status": "PROCESSING",
                "products": [{"product_id": "P000001"}],
            })
            write_json(root / "batches/B-RUNNING/owner.json", {
                "owner_id": "host-main",
                "device_id": "host-main",
            })
            write_json(current_path, {"batch_id": "B-RUNNING", "pid": 321})
            with (
                patch.object(workbench, "ROOT", root),
                patch.object(workbench, "CURRENT_BATCH_PATH", current_path),
                patch.object(workbench, "SAFE_STOP_REQUEST_PATH", safe_stop_path),
                patch.object(workbench, "running_batch_pid", return_value=321),
                patch.object(workbench, "current_operator", return_value={
                    "id": "host-main",
                    "device_id": "host-main",
                    "client_device_id": "host-main",
                    "device_name": "主电脑",
                    "role": "owner",
                }),
            ):
                result = await workbench.control_batch(FakeRequest({
                    "action": "stop",
                    "source": "manual_toolbar_v2",
                }))
            saved = json.loads(safe_stop_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "stopping_safely")
            self.assertEqual(saved["batch_id"], "B-RUNNING")
            self.assertEqual(saved["mode"], "manual_operator_stop")
            self.assertEqual(saved["source"], "manual_toolbar_v2")
            self.assertEqual(saved["requested_by"], "host-main")

    def test_compatible_waiting_batches_are_coalesced_for_bounded_parallel_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "logs/workbench-run-queue.json"
            products_dir = root / "products"
            for number, batch_id in ((1, "B-111111111111"), (2, "B-222222222222")):
                product_id = f"P{number:06d}"
                write_json(products_dir / product_id / "status.json", {
                    "status": "COLLECTED", "batch_id": batch_id, "api_write_count": 0,
                })
                write_json(root / "batches" / batch_id / "batch.json", {
                    "schema_version": "1.0.0", "batch_id": batch_id, "status": "QUEUED",
                    "created_at": "2026-07-18T00:00:00+08:00", "started_at": "unknown",
                    "completed_at": "unknown", "product_count": 1, "sku_count": 1,
                    "processing_count": 0, "success_count": 0, "failed_count": 0, "progress": 0,
                    "target_store_ids": ["shop-a"], "auto_upload": False, "review_mode": "manual",
                    "manual_upload_required": True, "inventory_submission_enabled": False,
                    "products": [{
                        "product_id": product_id, "selected_sku_count": 1, "status": "QUEUED",
                        "current_step": "queue", "started_at": "unknown", "completed_at": "unknown",
                        "warnings": [], "errors": [], "target_store_ids": ["shop-a"],
                        "publication_count": 1, "collection_id": f"COL-{number}",
                        "source_manifest_sha256": "a" * 64,
                        "source_snapshot_binding": {"collection_id": f"COL-{number}"},
                        "auto_upload": False, "review_mode": "manual", "manual_confirmation_required": False,
                    }],
                })
            write_json(queue_path, {
                "schema_version": "1.0.0", "items": [
                    {"batch_id": "B-111111111111", "source": "workbench_batch", "product_count": 1},
                    {"batch_id": "B-222222222222", "source": "single_product", "product_count": 1},
                ],
            })
            with (
                patch.object(workbench, "ROOT", root),
                patch.object(workbench, "PRODUCTS_DIR", products_dir),
                patch.object(workbench, "WORKBENCH_RUN_QUEUE_PATH", queue_path),
            ):
                result = workbench.coalesce_compatible_queued_batches()

            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            merged = json.loads((root / "batches/B-111111111111/batch.json").read_text(encoding="utf-8"))
            archived = json.loads((root / "batches/B-222222222222/batch.json").read_text(encoding="utf-8"))
            second_status = json.loads((products_dir / "P000002/status.json").read_text(encoding="utf-8"))
            self.assertEqual(result["merged_batch_count"], 1)
            self.assertEqual(len(queue["items"]), 1)
            self.assertEqual(merged["product_count"], 2)
            self.assertEqual([item["product_id"] for item in merged["products"]], ["P000001", "P000002"])
            self.assertEqual(archived["local_lifecycle_status"], "ARCHIVED")
            self.assertEqual(second_status["batch_id"], "B-111111111111")

    async def test_same_product_click_returns_existing_batch_without_second_create(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P000001"
            write_json(product_dir / "status.json", {
                "status": "COLLECTED", "api_write_count": 0,
                "ozon": {"upload_status": "not_started"},
            })
            with (
                patch.object(workbench, "ROOT", root),
                patch.object(workbench, "PRODUCTS_DIR", root / "products"),
                patch.object(workbench, "connected_store_ids", return_value=["shop-1"]),
                patch.object(workbench, "reserved_product_batches", return_value={"P000001": "B-EXISTING"}),
                patch.object(workbench, "create_batch") as create_batch,
            ):
                result = await workbench.run_single_workbench_product(
                    "P000001", FakeRequest({"store_ids": ["shop-1"], "auto_upload": True})
                )
            self.assertEqual(result["status"], "already_queued")
            self.assertEqual(result["batch_id"], "B-EXISTING")
            create_batch.assert_not_called()

    def test_extension_and_workbench_use_automatic_device_identity(self):
        manifest = json.loads((ROOT / "collector/edge-extension/manifest.json").read_text(encoding="utf-8"))
        popup = (ROOT / "collector/edge-extension/popup.html").read_text(encoding="utf-8")
        popup_script = (ROOT / "collector/edge-extension/popup.js").read_text(encoding="utf-8")
        workbench_script = (ROOT / "collector/local-ingest/static/workbench.js").read_text(encoding="utf-8")
        workbench_html = (ROOT / "collector/local-ingest/static/workbench.html").read_text(encoding="utf-8")
        self.assertIn("storage", manifest["permissions"])
        self.assertIn("http://*/*", manifest["host_permissions"])
        self.assertIn("factory-url", popup)
        self.assertIn("X-Factory-Device-Id", popup_script)
        self.assertNotIn("factoryAccessCode", popup_script)
        self.assertNotIn('id="run-task"', popup)
        self.assertNotIn("runCollectedTasks", popup_script)
        self.assertIn("cafDeviceId", workbench_script)
        self.assertIn("X-Factory-Device-Id", workbench_script)
        self.assertNotIn("cafAccessCode", workbench_script)
        self.assertIn("already_queued", workbench_script)
        self.assertNotIn("access-dialog", workbench_html)
        self.assertIn("共享工作台", workbench_html)
        self.assertNotIn("window.prompt", workbench_script)

    async def test_remote_api_needs_no_code_and_is_identified_as_lan_device(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config/lan-access.json", {
                "enabled": True, "access_code": "",
                "allowed_cidrs": ["192.168.0.0/16"],
            })
            def request_with(headers=None):
                values = {"Host": "factory.local:8765", **(headers or {})}
                encoded = [(key.lower().encode(), value.encode()) for key, value in values.items()]
                return Request({
                    "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": "GET", "scheme": "http", "path": "/api/workbench/summary",
                    "raw_path": b"/api/workbench/summary", "query_string": b"", "headers": encoded,
                    "client": ("192.168.1.25", 50000), "server": ("factory.local", 8765),
                })

            async def accepted(request):
                return JSONResponse({"operator": request.state.operator})

            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                allowed = await workbench.local_network_only(
                    request_with({"Origin": "http://factory.local:8765", "X-Factory-Device-Id": "browser-123"}),
                    accepted,
                )
            self.assertEqual(allowed.status_code, 200)
            body = json.loads(allowed.body)
            self.assertEqual(body["operator"]["display_name"], "工作室电脑 25")
            self.assertEqual(body["operator"]["client_device_id"], "browser-123")
            self.assertFalse(body["operator"]["is_host_device"])

    def test_host_lan_address_keeps_main_computer_permissions(self):
        request = Request({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": "/api/workbench/settings",
            "raw_path": b"/api/workbench/settings", "query_string": b"", "headers": [],
            "client": ("192.168.3.13", 50000), "server": ("192.168.3.13", 8765),
        })
        self.assertTrue(workbench.request_from_host_machine(request, "192.168.3.13"))
        operator = workbench.automatic_device_operator(
            request,
            "192.168.3.13",
            loopback=workbench.request_from_host_machine(request, "192.168.3.13"),
        )
        self.assertTrue(operator["is_host_device"])
        self.assertEqual(operator["device_name"], "主电脑")

    def test_remote_lan_address_does_not_gain_main_computer_permissions(self):
        request = Request({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": "/api/workbench/settings",
            "raw_path": b"/api/workbench/settings", "query_string": b"", "headers": [],
            "client": ("192.168.3.25", 50000), "server": ("192.168.3.13", 8765),
        })
        self.assertFalse(workbench.request_from_host_machine(request, "192.168.3.25"))

    def test_products_and_batches_are_shared_across_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_dir = root / "products/P000001"
            write_json(product_dir / "input/owner.json", {"owner_id": "old-member"})
            write_json(product_dir / "status.json", {"status": "COLLECTED"})
            write_json(root / "batches/B-SHARED/batch.json", {"batch_id": "B-SHARED"})
            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                self.assertTrue(workbench.product_is_owned(product_dir, operator_id="different-device"))
                self.assertTrue(workbench.batch_is_owned("B-SHARED"))

    async def test_cross_site_page_cannot_inherit_loopback_owner(self):
        def request_with(headers=None):
            encoded = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
            return Request({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "POST", "scheme": "http", "path": "/api/workbench/settings",
                "raw_path": b"/api/workbench/settings", "query_string": b"", "headers": encoded,
                "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8765),
            })

        async def accepted(_request):
            return JSONResponse({"ok": True})

        cross_site = await workbench.local_network_only(
            request_with({"Origin": "https://untrusted.example", "Sec-Fetch-Site": "cross-site"}),
            accepted,
        )
        same_origin = await workbench.local_network_only(
            request_with({"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"}),
            accepted,
        )
        self.assertEqual(cross_site.status_code, 403)
        self.assertEqual(same_origin.status_code, 200)

    def test_only_host_device_can_manage_settings(self):
        token = workbench.CURRENT_OPERATOR.set({"id": "device-remote", "role": "member", "is_host_device": False})
        try:
            with self.assertRaisesRegex(Exception, "只有主电脑"):
                workbench.require_owner_role()
        finally:
            workbench.CURRENT_OPERATOR.reset(token)
        self.assertTrue(workbench.current_operator()["is_host_device"])

    async def test_main_computer_1688_collector_can_use_implicit_owner(self):
        async def accepted(_request):
            return JSONResponse({"ok": True})

        request = Request({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/collector/categories/rules",
            "raw_path": b"/api/collector/categories/rules", "query_string": b"",
            "headers": [
                (b"origin", b"https://detail.1688.com"),
                (b"sec-fetch-site", b"cross-site"),
            ],
            "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8765),
        })
        response = await workbench.local_network_only(request, accepted)
        self.assertEqual(response.status_code, 200)

    async def test_extension_popup_can_test_workbench_connection(self):
        async def accepted(_request):
            return JSONResponse({"ok": True})

        request = Request({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": "/api/workbench/summary",
            "raw_path": b"/api/workbench/summary", "query_string": b"",
            "headers": [(b"origin", b"chrome-extension://collector-id"), (b"sec-fetch-site", b"cross-site")],
            "client": ("192.168.1.25", 50000), "server": ("factory.local", 8765),
        })
        with patch.object(workbench, "load_lan_access_config", return_value={"enabled": True, "allowed_cidrs": ["192.168.0.0/16"]}):
            response = await workbench.local_network_only(request, accepted)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
