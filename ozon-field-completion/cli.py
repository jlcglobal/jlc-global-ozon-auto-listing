#!/usr/bin/env python3
"""Generate the non-uploading Ozon field-completion package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-field-completion"))

from ozon_field_completion import build_package  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product", help="Product id such as P000004 or a product directory")
    args = parser.parse_args()
    value = Path(args.product)
    product_dir = value if value.is_dir() else ROOT / "products" / args.product
    package = build_package(product_dir)
    summary = {
        "product_id": product_dir.name,
        "tags": package["ozon-tags.json"]["count"],
        "attributes": len(package["ozon-attributes-final.json"]["attributes"]),
        "required_missing": package["ozon-attributes-final.json"]["required_summary"]["missing"],
        "rich_content": package["rich-content.json"]["status"],
        "color_variants": package["color-variants.json"]["summary"],
        "color_policy": package["color-variant-policy.json"]["status"],
        "upload_allowed": package["final-upload-check.json"]["upload_allowed"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
