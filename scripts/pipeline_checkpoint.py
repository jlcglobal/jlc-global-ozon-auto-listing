#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline_runtime import complete_step, mark_hard_failure


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist one full-pipeline checkpoint")
    parser.add_argument("action", choices=["complete", "fail"])
    parser.add_argument("product_id")
    parser.add_argument("step")
    parser.add_argument("--reason", default="unknown")
    args = parser.parse_args()
    product_dir = ROOT / "products" / args.product_id
    if args.action == "complete":
        status = complete_step(product_dir, args.step)
    else:
        status = mark_hard_failure(product_dir, args.step, args.reason)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
