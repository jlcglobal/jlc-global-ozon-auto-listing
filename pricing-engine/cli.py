#!/usr/bin/env python3
"""Generate cost intelligence and pricing outputs for one product."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pricing-engine"))

from pricing_engine import (  # noqa: E402
    apply_pricing_to_existing_draft,
    build_pricing_package,
    write_pricing_package,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build RETS-based product pricing")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--write", action="store_true", help="Write output JSON files")
    parser.add_argument("--update-draft", action="store_true", help="Apply calculated prices to the existing Ozon draft")
    args = parser.parse_args()
    product_dir = Path(args.product_dir)
    package = write_pricing_package(product_dir) if args.write else build_pricing_package(product_dir)
    if args.update_draft:
        if not args.write:
            parser.error("--update-draft requires --write")
        apply_pricing_to_existing_draft(product_dir, package)
    result = package["pricing-result.json"]
    print(json.dumps({
        "product_id": result["product_id"],
        "recommendation": result["recommendation"],
        "sku_prices": [
            {
                "sku_id": item["sku_id"],
                "selling_price_cny": item["selling_price_cny"],
                "selling_price_rub": item["selling_price_rub"],
                "route": item["shipping"]["route_name"] if item["shipping"] else None,
            }
            for item in result["sku_pricing"]
        ],
    }, ensure_ascii=False))
    return 0 if result["recommendation"] != "REJECT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
