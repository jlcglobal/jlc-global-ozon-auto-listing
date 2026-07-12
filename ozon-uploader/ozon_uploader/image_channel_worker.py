"""Standalone image tunnel worker that survives application restarts."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .images import CloudflareImageTunnel


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    state = {
        "status": "starting", "worker_pid": os.getpid(), "public_url": "unknown",
        "started_at": now(), "stopped_at": None, "reason": None,
    }
    write_json_atomic(args.state, state)
    try:
        with CloudflareImageTunnel(args.directory) as tunnel:
            state.update({"status": "running", "public_url": tunnel.public_url})
            write_json_atomic(args.state, state)
            while not args.stop_file.exists() and time.monotonic() - started < args.max_seconds:
                time.sleep(2)
            state.update({
                "status": "confirmed_closed" if args.stop_file.exists() else "expired",
                "stopped_at": now(),
                "reason": "ozon_cdn_confirmed" if args.stop_file.exists() else "max_lifetime_exceeded",
            })
    except Exception as exc:
        state.update({"status": "failed", "stopped_at": now(), "reason": str(exc)})
        write_json_atomic(args.state, state)
        return 1
    write_json_atomic(args.state, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
