#!/usr/bin/env python3
"""CLI for stage 4.1 read-only Ozon metadata operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ozon_adapter import OzonApiError, OzonConfig, OzonConfigurationError, OzonReadOnlyClient
from ozon_adapter.service import (
    fetch_and_write_product_metadata,
    remap_cached_product_metadata,
    validate_live_metadata_package,
)


ROOT = Path(__file__).resolve().parents[1]
SHOP_REGISTRY = ROOT / "ozon-adapter" / "shops.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch read-only Ozon category metadata.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--shop", help="Configured Ozon shop name used for authentication")
    parser.add_argument("--fetch", action="store_true", help="Fetch and write live metadata")
    parser.add_argument("--remap-cached", action="store_true", help="Remap already fetched metadata without API calls")
    parser.add_argument("--verify", action="store_true", help="Verify existing live metadata files")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()

    if args.verify:
        errors = validate_live_metadata_package(product_dir)
        if errors:
            print("FAILED")
            for error in errors:
                print(f"- {error}")
            return 1
        print(f"PASS {product_dir}")
        print("upload_allowed=false")
        return 0

    if args.remap_cached:
        package = remap_cached_product_metadata(product_dir)
        summary = package["ozon-attributes.json"]["summary"]
        print(json.dumps({
            "product_id": product_dir.name,
            "required_attributes": summary["required_count"],
            "mapped_attributes": summary["mapped_count"],
            "missing_attributes": summary["missing_count"],
            "upload_allowed": False,
        }, ensure_ascii=False))
        return 0

    if not args.fetch:
        parser.error("Choose --fetch, --remap-cached or --verify")
    try:
        registry = json.loads(SHOP_REGISTRY.read_text(encoding="utf-8"))
        shop_name = args.shop or registry["default_read_shop"]
        config = OzonConfig.from_shop(shop_name, SHOP_REGISTRY)
        package = fetch_and_write_product_metadata(product_dir, OzonReadOnlyClient(config))
    except (OzonConfigurationError, OzonApiError, ValueError) as exc:
        print("FAILED")
        print(f"- {exc}")
        return 2
    summary = package["ozon-attributes.json"]["summary"]
    print(json.dumps({
        "product_id": product_dir.name,
        "credential_shop": config.shop_name,
        "category_id": package["ozon-category.json"]["category_id"],
        "category_name": package["ozon-category.json"]["category_name"],
        "required_attributes": summary["required_count"],
        "mapped_attributes": summary["mapped_count"],
        "missing_attributes": summary["missing_count"],
        "upload_allowed": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
