"""Temporary local image server and Cloudflare quick tunnel."""

from __future__ import annotations

import hashlib
import http.server
import re
import selectors
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


class ImageTunnelError(RuntimeError):
    pass


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_images(
    product_dir: Path,
    draft: Dict[str, Any],
    created_at: str,
    color_variants: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    root = product_dir.parents[1]
    staging = product_dir / "output" / "ozon-image-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    images: List[Dict[str, Any]] = []
    variant_main_skus = {
        str(item.get("source_sku_id"))
        for item in draft["images"]
        if item.get("role") == "main"
        and item.get("variant_scope") == "sku"
        and item.get("source_sku_id") not in {None, "all", "unknown"}
    }
    for index, item in enumerate(draft["images"], start=1):
        path = root / item["path"]
        if not path.is_file():
            raise ImageTunnelError(f"Generated image is missing: {path}")
        suffix = path.suffix.lower() or ".png"
        role = "variant_main" if item.get("role") == "main" and item.get("variant_scope") == "sku" else item["role"]
        staged_name = f"{index:02d}-{role}{suffix}"
        shutil.copy2(path, staging / staged_name)
        images.append({
            "slot": item["slot"],
            "role": role,
            "source_sku_id": str(item.get("source_sku_id") or "all"),
            "local_path": item["path"],
            "staged_name": staged_name,
            "public_url": "unknown",
            "sha256": sha256_file(path),
            "status": "pending",
            "ozon_image_id": "unknown",
            "ozon_url": "unknown",
            "error": "unknown",
        })
    for index, item in enumerate((color_variants or {}).get("variants", []), start=1):
        if item.get("status") != "mapped" or item.get("image") == "missing":
            continue
        if str(item.get("sku_id")) in variant_main_skus:
            continue
        path = root / item["image"]
        if not path.is_file():
            raise ImageTunnelError(f"Color variant image is missing: {path}")
        suffix = path.suffix.lower() or ".png"
        staged_name = f"color-{index:02d}-{item['sku_id']}{suffix}"
        shutil.copy2(path, staging / staged_name)
        images.append({
            "slot": f"color-{item['sku_id']}",
            "role": "color",
            "source_sku_id": str(item["sku_id"]),
            "local_path": item["image"],
            "staged_name": staged_name,
            "public_url": "unknown",
            "sha256": sha256_file(path),
            "status": "pending",
            "ozon_image_id": "unknown",
            "ozon_url": "unknown",
            "error": "unknown",
        })
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "hosting_mode": "pending_tunnel",
        "tunnel_url": "unknown",
        "images": images,
        "created_at": created_at,
    }


class CloudflareImageTunnel:
    URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.server: Optional[http.server.ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.process: Optional[subprocess.Popen[str]] = None
        self.public_url: Optional[str] = None

    def __enter__(self) -> "CloudflareImageTunnel":
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        handler = lambda *args, **kwargs: _QuietHandler(  # noqa: E731
            *args, directory=str(self.directory), **kwargs
        )
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.process = subprocess.Popen(
            [
                "cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}",
                "--no-autoupdate", "--protocol", "http2",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        selector = selectors.DefaultSelector()
        assert self.process.stdout is not None
        selector.register(self.process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 45
        tunnel_connected = False
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ImageTunnelError("cloudflared stopped before creating a tunnel")
            for key, _ in selector.select(timeout=1):
                line = key.fileobj.readline()
                match = self.URL_PATTERN.search(line)
                if match:
                    self.public_url = match.group(0)
                if "Registered tunnel connection" in line:
                    tunnel_connected = True
                if self.public_url and tunnel_connected:
                    return self
        raise ImageTunnelError("Timed out waiting for a connected Cloudflare quick tunnel")

    def public_image_urls(self, manifest: Dict[str, Any]) -> List[str]:
        if not self.public_url:
            raise ImageTunnelError("Tunnel is not ready")
        urls = []
        local_verification_available = True
        for item in manifest["images"]:
            url = f"{self.public_url}/{urllib.parse.quote(item['staged_name'])}"
            try:
                if local_verification_available:
                    self._wait_until_public(url)
            except ImageTunnelError as exc:
                local_verification_available = False
                item["error"] = (
                    "Local TLS verification was unavailable; Ozon ingestion verification "
                    f"is required: {exc}"
                )
            if not local_verification_available and item["error"] == "unknown":
                item["error"] = (
                    "Local TLS verification skipped after the tunnel domain was unreachable "
                    "from this machine; Ozon ingestion verification is required."
                )
            item["public_url"] = url
            item["status"] = "served"
            urls.append(url)
        manifest["hosting_mode"] = "cloudflare_quick_tunnel"
        manifest["tunnel_url"] = self.public_url
        return urls

    @staticmethod
    def _wait_until_public(url: str) -> None:
        last_error: Optional[BaseException] = None
        for attempt in range(1, 3):
            try:
                with urllib.request.urlopen(url, timeout=20) as response:
                    if response.status == 200:
                        return
                    last_error = ImageTunnelError(
                        f"Public image returned HTTP {response.status}: {url}"
                    )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(min(attempt * 2, 8))

        curl = shutil.which("curl")
        if curl:
            check = subprocess.run(
                [
                    curl,
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--retry",
                    "2",
                    "--retry-all-errors",
                    "--connect-timeout",
                    "10",
                    "--max-time",
                    "15",
                    "--output",
                    "/dev/null",
                    url,
                ],
                capture_output=True,
                text=True,
            )
            if check.returncode == 0:
                return
            last_error = ImageTunnelError(check.stderr.strip() or "curl check failed")

        raise ImageTunnelError(f"Public image check failed: {url}: {last_error}")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
