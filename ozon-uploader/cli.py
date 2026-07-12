#!/usr/bin/env python3
"""Stage 4.2 single-product uploader CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))

from ozon_adapter import OzonConfig  # noqa: E402
from ozon_uploader import (  # noqa: E402
    OzonWriteClient,
    UploadGateError,
    assert_production_allowed,
    build_upload_payload,
    execute_upload,
    prepare_upload,
    repair_uploaded_images,
    upload_mode,
)


SHOP_REGISTRY = ROOT / "ozon-adapter" / "shops.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Gated single-product Ozon uploader")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--prepare", action="store_true", help="Deprecated alias for the safe dry run")
    parser.add_argument("--execute", action="store_true", help="Run according to UPLOAD_MODE (dry-run by default)")
    parser.add_argument("--repair-images", action="store_true", help="Re-submit and verify images for an uploaded product")
    parser.add_argument("--force-image-resubmit", action="store_true", help="Force one controlled image UPDATE for end-to-end validation")
    parser.add_argument("--shop", default="zhonglian1", help="Configured Ozon shop name")
    parser.add_argument(
        "--require-action",
        choices=["create", "update"],
        default=None,
        help="Abort before the write request unless the live existence check selects this action",
    )
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()
    try:
        if args.prepare:
            preview = build_upload_payload(product_dir, mode="dry-run")
            print(json.dumps({
                "upload_mode": preview["upload_mode"],
                "product_name_cn": preview["product_name_cn"],
                "shop_name": preview["shop_name"],
                "api_writes_performed": preview["api_writes_performed"],
                "production_blockers": preview["production_blockers"],
            }, ensure_ascii=False))
            return 0
        if args.execute:
            mode = upload_mode()
            payload = build_upload_payload(product_dir, mode=mode)
            if mode == "dry-run":
                print(json.dumps({
                    "upload_mode": mode,
                    "product_name_cn": payload["product_name_cn"],
                    "api_writes_performed": False,
                    "payload": str(product_dir / "output/ozon-upload-payload.json"),
                    "production_blockers": payload["production_blockers"],
                }, ensure_ascii=False))
                return 0
            assert_production_allowed(product_dir, payload)
            config = OzonConfig.from_shop(args.shop, SHOP_REGISTRY)
            result = execute_upload(
                product_dir,
                OzonWriteClient(config),
                required_action=args.require_action,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] in {"submitted", "created", "updated", "skipped"} else 1
        if args.repair_images:
            config = OzonConfig.from_shop(args.shop, SHOP_REGISTRY)
            result = repair_uploaded_images(
                product_dir, OzonWriteClient(config), force_resubmit=args.force_image_resubmit
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] in {"submitted", "created", "updated"} else 1
        payload = build_upload_payload(product_dir, mode="dry-run")
        print(json.dumps({
            "upload_mode": "dry-run",
            "product_name_cn": payload["product_name_cn"],
            "api_writes_performed": False,
            "payload": str(product_dir / "output/ozon-upload-payload.json"),
            "production_blockers": payload["production_blockers"],
        }, ensure_ascii=False))
        return 0
    except (UploadGateError, ValueError, RuntimeError) as exc:
        print("FAILED")
        print(f"- {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
