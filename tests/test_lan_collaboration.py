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
            pid_path = Path(directory) / "batch.pid"
            pid_path.write_text("12345", encoding="utf-8")
            with patch.object(workbench, "BATCH_PID_PATH", pid_path), patch.object(workbench.os, "kill"):
                self.assertEqual(workbench.running_batch_pid(), 12345)
            pid_path.write_text("12345", encoding="utf-8")
            with patch.object(workbench, "BATCH_PID_PATH", pid_path), patch.object(workbench.os, "kill", side_effect=ProcessLookupError):
                self.assertIsNone(workbench.running_batch_pid())
            self.assertFalse(pid_path.exists())

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

    def test_extension_and_workbench_include_lan_connection_controls(self):
        manifest = json.loads((ROOT / "collector/edge-extension/manifest.json").read_text(encoding="utf-8"))
        popup = (ROOT / "collector/edge-extension/popup.html").read_text(encoding="utf-8")
        popup_script = (ROOT / "collector/edge-extension/popup.js").read_text(encoding="utf-8")
        workbench_script = (ROOT / "collector/local-ingest/static/workbench.js").read_text(encoding="utf-8")
        workbench_html = (ROOT / "collector/local-ingest/static/workbench.html").read_text(encoding="utf-8")
        self.assertIn("storage", manifest["permissions"])
        self.assertIn("http://*/*", manifest["host_permissions"])
        self.assertIn("factory-url", popup)
        self.assertIn("X-Factory-Access-Code", popup_script)
        self.assertNotIn('id="run-task"', popup)
        self.assertNotIn("runCollectedTasks", popup_script)
        self.assertIn("cafAccessCode", workbench_script)
        self.assertIn("already_queued", workbench_script)
        self.assertIn("access-dialog", workbench_html)
        self.assertNotIn("window.prompt", workbench_script)

    async def test_remote_api_requires_shared_code_but_accepts_correct_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config/lan-access.json", {
                "enabled": True, "access_code": "studio-code",
                "allowed_cidrs": ["192.168.0.0/16"],
            })
            def request_with(headers=None):
                encoded = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
                return Request({
                    "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": "GET", "scheme": "http", "path": "/api/workbench/summary",
                    "raw_path": b"/api/workbench/summary", "query_string": b"", "headers": encoded,
                    "client": ("192.168.1.25", 50000), "server": ("factory.local", 8765),
                })

            async def accepted(_request):
                return JSONResponse({"ok": True})

            with patch.object(workbench, "ROOT", root), patch.object(workbench, "PRODUCTS_DIR", root / "products"):
                denied = await workbench.local_network_only(request_with(), accepted)
                allowed = await workbench.local_network_only(
                    request_with({"X-Factory-Access-Code": "studio-code"}), accepted
                )
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(allowed.status_code, 200)

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
            request_with({"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"}),
            accepted,
        )
        self.assertEqual(cross_site.status_code, 401)
        self.assertEqual(same_origin.status_code, 200)


if __name__ == "__main__":
    unittest.main()
