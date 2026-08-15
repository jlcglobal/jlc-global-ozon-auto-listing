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

from multi_store_upload import HANDOFF_STATE, PENDING_STATES, has_task_without_product, refresh_pending_stores  # noqa: E402
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
    checked_products = 0
    checked_stores = 0
    failures = []
    products_dir = root / "products"
    for product_dir in sorted(products_dir.glob("P[0-9]*")):
        if not product_dir.is_dir():
            continue
        due_store_ids = due_pending_store_ids(root, product_dir.name)
        if not due_store_ids:
            # JSON-only pending rows may not have been projected into SQLite yet.
            publications = load_publications(product_dir)
            due_store_ids = [
                str(store_id)
                for store_id, record in (publications.get("stores") or {}).items()
                if str(record.get("status") or "") in PENDING_STATES
                or (str(record.get("status") or "") == HANDOFF_STATE and has_task_without_product(record))
            ]
        if not due_store_ids:
            continue
        try:
            result = refresh_pending_stores(root, product_dir, only_store_ids=due_store_ids)
            checked = result.get("checked") or []
            if checked:
                checked_products += 1
                checked_stores += len(checked)
                record_remote_check(root, product_dir.name, [item["store_id"] for item in checked])
        except Exception as exc:
            failures.append({"product_id": product_dir.name, "error": str(exc)})
    return {
        "checked_products": checked_products,
        "checked_stores": checked_stores,
        "write_api_calls": 0,
        "read_api_calls": checked_stores,
        "inventory_api_calls": 0,
        "failures": failures,
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
