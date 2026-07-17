#!/usr/bin/env python3
"""Continuously recover already-submitted Ozon tasks without creating products.

This process is intentionally separate from the FastAPI request path.  It only
calls the existing read-only recovery command for publications that already
have a task id and never invokes ``multi_store_upload --execute``.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from multi_store_upload import PENDING_STATES, refresh_pending_stores  # noqa: E402
from pipeline_runtime import now  # noqa: E402
from store_publications import load_publications  # noqa: E402
from task_database import due_pending_store_ids, record_remote_check  # noqa: E402

PID_PATH = ROOT / "logs/remote-status-worker.pid"
LOG_PATH = ROOT / "logs/remote-status-worker.log"
STOP_PATH = ROOT / "logs/remote-status-worker.stop"
STOP = False


def _signal(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def _log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{now()} {message}\n")


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_pid() -> bool:
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PID_PATH.is_file():
        try:
            old_pid = int(PID_PATH.read_text(encoding="utf-8").strip())
            if old_pid != os.getpid() and _pid_is_alive(old_pid):
                return False
        except (OSError, ValueError):
            pass
        PID_PATH.unlink(missing_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_pid() -> None:
    try:
        if PID_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def run_once(root: Path = ROOT) -> Dict[str, Any]:
    return {
        "checked_products": 0,
        "checked_stores": 0,
        "write_api_calls": 0,
        "read_api_calls": 0,
        "inventory_api_calls": 0,
        "failures": [],
        "disabled": True,
        "message": "远端回查已永久停用",
        "checked_at": now(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Ozon task status worker")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("interval must be at least 5 seconds")
    _log("remote status worker disabled; no Ozon API call made")
    return 0
    if not acquire_pid():
        _log("already running; exiting")
        return 0
    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)
    try:
        STOP_PATH.unlink(missing_ok=True)
        while not STOP:
            if STOP_PATH.is_file():
                break
            result = run_once(args.root.resolve())
            _log(json.dumps(result, ensure_ascii=False))
            if args.once:
                break
            for _ in range(args.interval):
                if STOP or STOP_PATH.is_file():
                    break
                time.sleep(1)
    finally:
        release_pid()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
