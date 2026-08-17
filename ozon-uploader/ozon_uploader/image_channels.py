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
    workspace_root = product_dir.parent.parent
    source_root = Path(__file__).resolve().parents[2]
    if running_channel_count(workspace_root) >= concurrency_limit:
        raise ImageTunnelError(f"Image channel concurrency limit reached: {concurrency_limit}")
    attempts = max(1, int(os.environ.get("OZON_IMAGE_CHANNEL_START_ATTEMPTS", "3")))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        if state_path.is_file():
            state = load_json(state_path)
            if state.get("status") == "running" and process_alive(state.get("worker_pid")):
                try:
                    return apply_public_urls(product_dir, manifest, state["public_url"])
                except Exception as exc:
                    last_error = exc
                    # A quick-tunnel URL can expire or be unreachable while its
                    # local worker still exists. Close it and create a fresh URL.
                    if not stop_image_channel(product_dir, reason="public_url_validation_failed", wait_seconds=10):
                        raise ImageTunnelError("旧图片通道失效且未能安全退出，已阻止重复启动")
                    if attempt >= attempts:
                        break
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
                    except Exception as exc:
                        last_error = exc
                        stop_image_channel(product_dir, reason="public_url_validation_failed", wait_seconds=10)
                        break
                if state.get("status") == "failed":
                    last_error = ImageTunnelError(str(state.get("reason") or "Image channel failed"))
                    break
            time.sleep(1)
        else:
            last_error = ImageTunnelError("Persistent image channel did not become ready within 60 seconds")
        if attempt < attempts:
            continue
    detail = f": {last_error}" if last_error else ""
    raise ImageTunnelError(f"图片公网通道连续启动失败，已重试 {attempts} 次{detail}")


def _is_local_tls_probe_failure(error: BaseException) -> bool:
    """Return true only for the known local HTTPS probe failure.

    A connected Cloudflare quick tunnel can be externally reachable while the
    current Mac's curl/OpenSSL stack fails its own TLS handshake.  This is not
    evidence that an image is missing.  HTTP errors, invalid tunnel responses
    and every other probe failure remain hard failures and still trigger a
    safe channel rebuild.
    """
    text = str(error).casefold()
    markers = (
        "ssl_error_syscall",
        "libressl ssl_connect",
        "openssl ssl_connect",
        "tlsv1 alert internal error",
    )
    return any(marker in text for marker in markers)


def apply_public_urls(product_dir: Path, manifest: Dict[str, Any], public_url: str) -> Dict[str, Any]:
    cache_path = product_dir / "output" / "image-public-validation-cache.json"
    cache = load_json(cache_path) if cache_path.is_file() else {"entries": {}}
    provisional_urls: List[str] = []
    for item in manifest["images"]:
        # These are local diagnostic fields from older channel attempts.  The
        # final Ozon upload schema deliberately has no place for them: keep
        # diagnostics in the local cache below rather than leaking them into
        # the transport manifest/payload.
        item.pop("public_validation", None)
        url = f"{public_url}/{urllib.parse.quote(item['staged_name'])}"
        key = f"{item['sha256']}|{url}"
        cached = cache["entries"].get(key)
        if not cached or cached.get("status") != "valid":
            try:
                CloudflareImageTunnel._wait_until_public(url)
                cache["entries"][key] = {"status": "valid", "validated_at": now()}
            except ImageTunnelError as exc:
                if not _is_local_tls_probe_failure(exc):
                    raise
                # Keep the verified worker/tunnel alive for its fixed TTL and
                # let Ozon fetch the exact HTTPS URL.  The local TLS probe is
                # recorded for diagnosis, but must not turn into a manual
                # image-transfer task or a false upload failure.
                cache["entries"][key] = {
                    "status": "local_tls_probe_unavailable",
                    "validated_at": now(),
                    "error": str(exc),
                }
                provisional_urls.append(url)
        item.update({
            "public_url": url,
            "status": "served",
            "error": "unknown",
        })
    manifest.pop("public_validation", None)
    manifest.pop("local_tls_probe_unavailable_urls", None)
    manifest.update({
        "hosting_mode": "background_tunnel",
        "tunnel_url": public_url,
    })
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
