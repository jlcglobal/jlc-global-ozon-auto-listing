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

from ozon_uploader.image_channels import start_image_channel  # noqa: E402


class ImageChannelIsolatedWorkspaceTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
