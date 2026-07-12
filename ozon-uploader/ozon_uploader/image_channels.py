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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .images import CloudflareImageTunnel, ImageTunnelError


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


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
            return apply_public_urls(product_dir, manifest, state["public_url"])
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
        command = [caffeinate, "-dimsu", *command]
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
                return apply_public_urls(product_dir, manifest, state["public_url"])
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


def stop_image_channel(product_dir: Path, reason: str = "ozon_cdn_confirmed") -> None:
    output = product_dir / "output"
    state_path = output / "image-channel-state.json"
    if not state_path.is_file():
        return
    state = load_json(state_path)
    if state.get("status") != "running":
        return
    (output / "image-channel.stop").write_text(reason + "\n", encoding="utf-8")


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
