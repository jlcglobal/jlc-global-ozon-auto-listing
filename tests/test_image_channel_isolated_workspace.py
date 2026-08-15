import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-uploader"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))

from ozon_uploader.image_channels import (  # noqa: E402
    ImageTunnelError,
    apply_public_urls,
    start_image_channel,
)
from ozon_uploader.images import resolve_cloudflared_binary  # noqa: E402


class ImageChannelIsolatedWorkspaceTest(unittest.TestCase):
    def test_cloudflared_resolver_uses_homebrew_fallback_when_path_is_minimal(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory) / "cloudflared"
            fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False), \
                 patch("ozon_uploader.images.shutil.which", return_value=None), \
                 patch("ozon_uploader.images.CLOUDFLARED_FALLBACK_PATHS", (str(fake_bin),)):
                self.assertEqual(resolve_cloudflared_binary(), str(fake_bin))

    def test_known_local_tls_probe_error_keeps_the_running_channel_available(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P000001"
            (product / "output").mkdir(parents=True)
            manifest = {"images": [{"staged_name": "main.png", "sha256": "abc"}]}

            with patch(
                "ozon_uploader.image_channels.CloudflareImageTunnel._wait_until_public",
                side_effect=ImageTunnelError("curl: (35) LibreSSL SSL_connect: SSL_ERROR_SYSCALL"),
            ):
                result = apply_public_urls(product, manifest, "https://temporary.example.test")

            image = result["images"][0]
            self.assertEqual(image["status"], "served")
            self.assertEqual(image["public_url"], "https://temporary.example.test/main.png")
            self.assertNotIn("public_validation", image)
            self.assertNotIn("public_validation", result)
            self.assertNotIn("local_tls_probe_unavailable_urls", result)
            cache = json.loads((product / "output/image-public-validation-cache.json").read_text(encoding="utf-8"))
            self.assertEqual(
                cache["entries"]["abc|https://temporary.example.test/main.png"]["status"],
                "local_tls_probe_unavailable",
            )

    def test_non_tls_public_probe_error_remains_a_hard_channel_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P000001"
            (product / "output").mkdir(parents=True)
            manifest = {"images": [{"staged_name": "main.png", "sha256": "abc"}]}

            with patch(
                "ozon_uploader.image_channels.CloudflareImageTunnel._wait_until_public",
                side_effect=ImageTunnelError("Public image returned HTTP 404"),
            ):
                with self.assertRaises(ImageTunnelError):
                    apply_public_urls(product, manifest, "https://temporary.example.test")

    def test_worker_uses_source_tree_adapter_from_isolated_product(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            product = workspace / "runtime/store/products/P000001"
            output = product / "output"
            output.mkdir(parents=True)
            manifest = {
                "images": [{"staged_name": "main.png", "sha256": "abc"}],
            }
            captured = {}

            def fake_popen(command, **kwargs):
                captured.update({"command": command, **kwargs})
                state_path = Path(command[command.index("--state") + 1])
                state_path.write_text(json.dumps({
                    "status": "running", "worker_pid": os.getpid(),
                    "public_url": "https://images.example.test",
                }), encoding="utf-8")
                return object()

            with patch("ozon_uploader.image_channels.subprocess.Popen", side_effect=fake_popen), \
                 patch("ozon_uploader.image_channels.CloudflareImageTunnel._wait_until_public"):
                result = start_image_channel(product, manifest)

            python_paths = captured["env"]["PYTHONPATH"].split(os.pathsep)
            self.assertIn(str(ROOT / "ozon-adapter"), python_paths)
            self.assertEqual(Path(captured["cwd"]), ROOT)
            self.assertEqual(
                result["images"][0]["public_url"],
                "https://images.example.test/main.png",
            )

    def test_invalid_existing_url_is_closed_before_one_replacement_is_started(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            product = workspace / "products/P000001"
            output = product / "output"
            output.mkdir(parents=True)
            (output / "image-channel-state.json").write_text(json.dumps({
                "status": "running", "worker_pid": os.getpid(),
                "public_url": "https://expired.example.test",
            }), encoding="utf-8")
            manifest = {"images": [{"staged_name": "main.png", "sha256": "abc"}]}
            launches = []

            def fake_popen(command, **_kwargs):
                launches.append(command)
                state_path = Path(command[command.index("--state") + 1])
                state_path.write_text(json.dumps({
                    "status": "running", "worker_pid": 987654,
                    "public_url": "https://fresh.example.test",
                }), encoding="utf-8")
                return object()

            fresh = {"images": [{"public_url": "https://fresh.example.test/main.png"}]}
            with patch("ozon_uploader.image_channels.apply_public_urls", side_effect=[ImageTunnelError("expired"), fresh]), \
                 patch("ozon_uploader.image_channels.stop_image_channel", return_value=True) as stop, \
                 patch("ozon_uploader.image_channels.process_alive", return_value=True), \
                 patch("ozon_uploader.image_channels.subprocess.Popen", side_effect=fake_popen):
                result = start_image_channel(product, manifest)

            stop.assert_called_once()
            self.assertEqual(len(launches), 1)
            self.assertEqual(result, fresh)


if __name__ == "__main__":
    unittest.main()
