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
    parser.add_argument(
        "--pre-image", action="store_true",
        help="Materialize text, tags and attributes before image generation; image QC may still be pending.",
    )
    args = parser.parse_args()
    value = Path(args.product)
    product_dir = value if value.is_dir() else ROOT / "products" / args.product
    if args.pre_image:
        for name in ("rich-content.json",):
            (product_dir / "output" / name).unlink(missing_ok=True)
    package = build_package(product_dir, pre_image=args.pre_image)
    summary = {
        "product_id": product_dir.name,
        "phase": "pre_image" if args.pre_image else "final",
        "tags": package["ozon-tags.json"]["count"],
        "attributes": len(package["ozon-attributes-final.json"]["attributes"]),
        "required_missing": package["ozon-attributes-final.json"]["required_summary"]["missing"],
        "rich_content": package.get("rich-content.json", {}).get("status", "deferred_until_images"),
        "color_variants": package["color-variants.json"]["summary"],
        "color_policy": package["color-variant-policy.json"]["status"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
