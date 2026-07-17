#!/usr/bin/env python3
"""Standalone finance scheduler; intentionally not started by the workbench."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from finance_center import FinanceCenter


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the isolated finance scheduler")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    center = FinanceCenter(args.root.resolve())
    center.recover_interrupted_syncs()
    center.repair_invalid_ad_matches(apply=True, created_by="finance-scheduler")
    while True:
        center.scheduler_tick()
        if args.once:
            return 0
        time.sleep(max(args.interval, 30))


if __name__ == "__main__":
    raise SystemExit(main())
