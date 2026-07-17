"""Standalone image tunnel worker that survives application restarts."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .images import CloudflareImageTunnel


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ttl_metadata(started_at: str, ttl_seconds: int) -> dict:
    started = datetime.fromisoformat(started_at)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return {
        "ttl_seconds": int(ttl_seconds),
        "expires_at": (started + timedelta(seconds=int(ttl_seconds))).isoformat(timespec="seconds"),
        "close_policy": "fixed_ttl",
    }


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--max-seconds", type=int, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    started_at = now()
    state = {
        "status": "starting", "worker_pid": os.getpid(), "public_url": "unknown",
        "started_at": started_at, "stopped_at": None, "reason": None,
    }
    state.update(ttl_metadata(started_at, args.max_seconds))
    write_json_atomic(args.state, state)
    try:
        with CloudflareImageTunnel(args.directory) as tunnel:
            state.update({"status": "running", "public_url": tunnel.public_url})
            write_json_atomic(args.state, state)
            while not args.stop_file.exists() and time.monotonic() - started < args.max_seconds:
                time.sleep(2)
            stop_reason = "ozon_cdn_confirmed"
            if args.stop_file.exists():
                try:
                    stop_reason = args.stop_file.read_text(encoding="utf-8").strip() or stop_reason
                except OSError:
                    pass
            state.update({
                "status": "confirmed_closed" if args.stop_file.exists() else "expired",
                "stopped_at": now(),
                "reason": stop_reason if args.stop_file.exists() else "max_lifetime_exceeded",
            })
    except Exception as exc:
        state.update({"status": "failed", "stopped_at": now(), "reason": str(exc)})
        write_json_atomic(args.state, state)
        return 1
    write_json_atomic(args.state, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
