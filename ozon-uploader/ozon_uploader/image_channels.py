"""Persistent local image channels and validation cache management."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from .images import CloudflareImageTunnel, ImageTunnelError

DEFAULT_IMAGE_CHANNEL_TTL_SECONDS = 24 * 60 * 60


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def image_channel_ttl(started_at: str | None = None, ttl_seconds: int = DEFAULT_IMAGE_CHANNEL_TTL_SECONDS) -> Dict[str, Any]:
    """Fixed safety lifetime; it never depends on a remote Ozon query."""
    started = datetime.fromisoformat(started_at) if started_at else datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    expires = started + timedelta(seconds=int(ttl_seconds))
    return {
        "ttl_seconds": int(ttl_seconds),
        "started_at": started.isoformat(timespec="seconds"),
        "expires_at": expires.isoformat(timespec="seconds"),
        "close_policy": "fixed_ttl",
    }


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def process_alive(pid: Any) -> bool:
    try:
        process_id = int(pid)
        os.kill(process_id, 0)
    except (OSError, TypeError, ValueError):
        return False
    ps = shutil.which("ps")
    if ps:
        try:
            state = subprocess.run(
                [ps, "-o", "stat=", "-p", str(process_id)],
                capture_output=True, text=True, timeout=2, check=False,
            ).stdout.strip()
            if not state or state.upper().startswith("Z"):
                return False
        except (OSError, subprocess.SubprocessError):
            pass
    return True


def running_channel_count(project_root: Path) -> int:
    count = 0
    for state_path in project_root.glob("products/P*/output/image-channel-state.json"):
        state = load_json(state_path)
        if state.get("status") == "running" and process_alive(state.get("worker_pid")):
            count += 1
    return count


def start_image_channel(
    product_dir: Path,
    manifest: Dict[str, Any],
    max_hours: int = 24,
    concurrency_limit: int = 4,
) -> Dict[str, Any]:
    output = product_dir / "output"
    state_path = output / "image-channel-state.json"
    stop_path = output / "image-channel.stop"
    if state_path.is_file():
        state = load_json(state_path)
        if state.get("status") == "running" and process_alive(state.get("worker_pid")):
            try:
                return apply_public_urls(product_dir, manifest, state["public_url"])
            except Exception:
                # A quick-tunnel URL can expire while its local worker still
                # exists. Close it before a retry creates a replacement.
                if not stop_image_channel(product_dir, reason="public_url_validation_failed", wait_seconds=10):
                    raise ImageTunnelError("旧图片通道失效且未能安全退出，已阻止重复启动")
    workspace_root = product_dir.parent.parent
    source_root = Path(__file__).resolve().parents[2]
    if running_channel_count(workspace_root) >= concurrency_limit:
        raise ImageTunnelError(f"Image channel concurrency limit reached: {concurrency_limit}")
    stop_path.unlink(missing_ok=True)
    log_path = output / "image-channel.log"
    command = [
        sys.executable, "-m", "ozon_uploader.image_channel_worker",
        "--directory", str(output / "ozon-image-staging"),
        "--state", str(state_path), "--stop-file", str(stop_path),
        "--max-seconds", str(max_hours * 3600),
    ]
    caffeinate = shutil.which("caffeinate")
    if caffeinate:
        command = [caffeinate, "-ims", *command]
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[1])
    # Store uploads run from an isolated copy under runtime/.  The uploader
    # package itself still belongs to the real source tree, so its sibling
    # ozon-adapter must come from that tree rather than from the isolated
    # workspace (which intentionally contains product data only).
    adapter_root = str(source_root / "ozon-adapter")
    python_paths = [package_root, adapter_root]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.Popen(
            command, cwd=source_root, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if state_path.is_file():
            state = load_json(state_path)
            if state.get("status") == "running":
                try:
                    return apply_public_urls(product_dir, manifest, state["public_url"])
                except Exception:
                    stop_image_channel(product_dir, reason="public_url_validation_failed", wait_seconds=10)
                    raise
            if state.get("status") == "failed":
                raise ImageTunnelError(str(state.get("reason") or "Image channel failed"))
        time.sleep(1)
    raise ImageTunnelError("Persistent image channel did not become ready within 60 seconds")


def apply_public_urls(product_dir: Path, manifest: Dict[str, Any], public_url: str) -> Dict[str, Any]:
    cache_path = product_dir / "output" / "image-public-validation-cache.json"
    cache = load_json(cache_path) if cache_path.is_file() else {"entries": {}}
    for item in manifest["images"]:
        url = f"{public_url}/{urllib.parse.quote(item['staged_name'])}"
        key = f"{item['sha256']}|{url}"
        cached = cache["entries"].get(key)
        if not cached or cached.get("status") != "valid":
            CloudflareImageTunnel._wait_until_public(url)
            cache["entries"][key] = {"status": "valid", "validated_at": now()}
        item.update({"public_url": url, "status": "served", "error": "unknown"})
    manifest.update({"hosting_mode": "background_tunnel", "tunnel_url": public_url})
    write_json_atomic(cache_path, cache)
    return manifest


def stop_image_channel(
    product_dir: Path,
    reason: str = "ozon_cdn_confirmed",
    wait_seconds: float = 0,
) -> bool:
    output = product_dir / "output"
    state_path = output / "image-channel-state.json"
    if not state_path.is_file():
        return True
    state = load_json(state_path)
    if state.get("status") != "running":
        return not process_alive(state.get("worker_pid"))
    (output / "image-channel.stop").write_text(reason + "\n", encoding="utf-8")
    if wait_seconds <= 0:
        return True
    deadline = time.monotonic() + wait_seconds
    while process_alive(state.get("worker_pid")) and time.monotonic() < deadline:
        time.sleep(0.2)
    return not process_alive(state.get("worker_pid"))


def channel_state(product_dir: Path) -> Dict[str, Any]:
    path = product_dir / "output" / "image-channel-state.json"
    return load_json(path) if path.is_file() else {"status": "missing"}


class PersistentImageTunnel:
    """Context-compatible facade whose exit intentionally leaves the worker running."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.product_dir = self.directory.parent.parent

    def __enter__(self) -> "PersistentImageTunnel":
        return self

    def public_image_urls(self, manifest: Dict[str, Any]) -> List[str]:
        start_image_channel(
            self.product_dir,
            manifest,
            max_hours=int(os.environ.get("OZON_IMAGE_CHANNEL_MAX_HOURS", "24")),
            concurrency_limit=int(os.environ.get("OZON_IMAGE_CHANNEL_CONCURRENCY", "4")),
        )
        return [item["public_url"] for item in manifest["images"]]

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None
