"""Temporary local image server and Cloudflare quick tunnel."""

from __future__ import annotations

import hashlib
import http.server
import json
import os
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


REPO_ROOT = Path(__file__).resolve().parents[2]
WATERMARK_CONFIG_PATH = REPO_ROOT / "config" / "image-watermark.json"
CLOUDFLARED_FALLBACK_PATHS = (
    "/opt/homebrew/bin/cloudflared",
    "/usr/local/bin/cloudflared",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_watermark_config() -> Dict[str, Any]:
    if not WATERMARK_CONFIG_PATH.is_file():
        return {"enabled": False}
    with WATERMARK_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ImageTunnelError("Image watermark config must be a JSON object")
    return value


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def resolve_cloudflared_binary() -> str:
    configured = str(os.environ.get("CLOUDFLARED_BIN") or "").strip()
    candidates = [configured] if configured else []
    found = shutil.which("cloudflared")
    if found:
        candidates.append(found)
    candidates.extend(CLOUDFLARED_FALLBACK_PATHS)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ImageTunnelError(
        "cloudflared is not installed or not reachable; checked PATH, "
        "CLOUDFLARED_BIN, /opt/homebrew/bin/cloudflared and /usr/local/bin/cloudflared"
    )


def _watermark_enabled_for_role(config: Dict[str, Any], role: str) -> bool:
    if not config.get("enabled", False):
        return False
    roles = config.get("apply_to_roles") or []
    return not roles or role in {str(item) for item in roles}


def _apply_watermark(source: Path, target: Path, role: str, config: Dict[str, Any]) -> bool:
    if not _watermark_enabled_for_role(config, role):
        shutil.copy2(source, target)
        return False

    logo_path = _resolve_project_path(str(config.get("logo_path") or ""))
    if not logo_path.is_file():
        raise ImageTunnelError(f"Image watermark logo is missing: {logo_path}")

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - runtime requirements include Pillow
        raise ImageTunnelError(
            "Image watermark requires Pillow; install project requirements before upload"
        ) from exc

    try:
        base = Image.open(source)
        base_format = base.format or (source.suffix.lower().lstrip(".") or "PNG").upper()
        base_rgba = base.convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")
    except OSError as exc:
        raise ImageTunnelError(f"Image watermark failed to read image assets: {exc}") from exc

    bbox = logo.getchannel("A").getbbox()
    if not bbox:
        raise ImageTunnelError(f"Image watermark logo has no visible pixels: {logo_path}")
    logo = logo.crop(bbox)

    max_width_ratio = float(config.get("max_width_ratio", 0.18))
    margin_ratio = float(config.get("margin_ratio", 0.035))
    opacity = max(0.0, min(1.0, float(config.get("opacity", 0.24))))
    max_width = max(1, int(base_rgba.width * max_width_ratio))
    scale = max_width / logo.width
    logo = logo.resize((max_width, max(1, int(logo.height * scale))), Image.LANCZOS)

    alpha = logo.getchannel("A").point(lambda pixel: int(pixel * opacity))
    logo.putalpha(alpha)

    margin = max(8, int(min(base_rgba.width, base_rgba.height) * margin_ratio))
    position = str(config.get("position") or "bottom_right")
    if position == "bottom_left":
        paste_at = (margin, base_rgba.height - logo.height - margin)
    elif position == "top_right":
        paste_at = (base_rgba.width - logo.width - margin, margin)
    elif position == "top_left":
        paste_at = (margin, margin)
    else:
        paste_at = (base_rgba.width - logo.width - margin, base_rgba.height - logo.height - margin)

    base_rgba.alpha_composite(logo, paste_at)
    target.parent.mkdir(parents=True, exist_ok=True)
    if base_format in {"JPEG", "JPG"} or target.suffix.lower() in {".jpg", ".jpeg"}:
        base_rgba.convert("RGB").save(target, format="JPEG", quality=95, optimize=True)
    else:
        base_rgba.save(target, format=base_format if base_format != "MPO" else "JPEG")
    return True


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
    watermark_config = _load_watermark_config()
    images: List[Dict[str, Any]] = []
    variant_main_skus = {
        str(item.get("source_sku_id"))
        for item in draft["images"]
        if item.get("role") == "main"
        and item.get("variant_scope") == "sku"
        and item.get("source_sku_id") not in {None, "all", "unknown"}
    }
    variant_main_by_sku: Dict[str, Dict[str, Any]] = {}
    for index, item in enumerate(draft["images"], start=1):
        path = root / item["path"]
        if not path.is_file():
            raise ImageTunnelError(f"Generated image is missing: {path}")
        suffix = path.suffix.lower() or ".png"
        role = "variant_main" if item.get("role") == "main" and item.get("variant_scope") == "sku" else item["role"]
        staged_name = f"{index:02d}-{role}{suffix}"
        staged_path = staging / staged_name
        watermark_applied = _apply_watermark(path, staged_path, role, watermark_config)
        images.append({
            "slot": item["slot"],
            "role": role,
            "source_sku_id": str(item.get("source_sku_id") or "all"),
            "local_path": item["path"],
            "staged_name": staged_name,
            "public_url": "unknown",
            "sha256": sha256_file(staged_path),
            "watermark_applied": watermark_applied,
            "status": "pending",
            "ozon_image_id": "unknown",
            "ozon_url": "unknown",
            "error": "unknown",
        })
        if role == "variant_main":
            variant_main_by_sku[str(item.get("source_sku_id") or "all")] = images[-1]
    for index, item in enumerate((color_variants or {}).get("variants", []), start=1):
        if item.get("status") != "mapped" or item.get("image") == "missing":
            continue
        sku_id = str(item.get("sku_id"))
        if sku_id in variant_main_skus and sku_id in variant_main_by_sku:
            source = variant_main_by_sku[sku_id]
            images.append({
                **source,
                "slot": f"color-{sku_id}",
                "role": "color",
                "source_sku_id": sku_id,
            })
            continue
        path = root / item["image"]
        if not path.is_file():
            raise ImageTunnelError(f"Color variant image is missing: {path}")
        suffix = path.suffix.lower() or ".png"
        staged_name = f"color-{index:02d}-{item['sku_id']}{suffix}"
        staged_path = staging / staged_name
        watermark_applied = _apply_watermark(path, staged_path, "color", watermark_config)
        images.append({
            "slot": f"color-{item['sku_id']}",
            "role": "color",
            "source_sku_id": str(item["sku_id"]),
            "local_path": item["image"],
            "staged_name": staged_name,
            "public_url": "unknown",
            "sha256": sha256_file(staged_path),
            "watermark_applied": watermark_applied,
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
        cloudflared = resolve_cloudflared_binary()
        self.process = subprocess.Popen(
            [
                cloudflared, "tunnel", "--url", f"http://127.0.0.1:{port}",
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
