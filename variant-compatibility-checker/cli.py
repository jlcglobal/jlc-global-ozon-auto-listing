#!/usr/bin/env python3
"""Run the category-aware SKU variant compatibility check."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from variant_compatibility_checker import evaluate_product


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether product SKUs can be Ozon variants")
    parser.add_argument("product_dir", type=Path)
    parser.add_argument("--rule-db", type=Path, default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    package = evaluate_product(args.product_dir, args.rule_db, write=not args.no_write)
    print(json.dumps(package, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
