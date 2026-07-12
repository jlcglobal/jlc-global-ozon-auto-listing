#!/usr/bin/env python3
"""Read-only Ozon import-result recovery for already submitted product tasks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))
sys.path.insert(0, str(ROOT / "scripts"))

from ozon_adapter import OzonConfig  # noqa: E402
from ozon_uploader import OzonWriteClient, recover_remote_import  # noqa: E402
from run_batch import load_shop_environment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover an already submitted Ozon import task")
    parser.add_argument("product_id", nargs="?")
    parser.add_argument("--product-dir", help="Explicit isolated product directory for one local store publication")
    parser.add_argument("--shop", default="zhonglian1")
    parser.add_argument("--timeout", type=int, default=1)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if not args.product_id and not args.product_dir:
        parser.error("product_id or --product-dir is required")
    if args.timeout < 1 or args.interval < 1:
        parser.error("timeout and interval must be positive")
    settings = json.loads((ROOT / "config/pipeline-settings.json").read_text(encoding="utf-8"))
    settings["shop_name"] = args.shop
    load_shop_environment(settings)
    os.environ["UPLOAD_MODE"] = "production"
    product_dir = Path(args.product_dir).resolve() if args.product_dir else ROOT / "products" / args.product_id
    result = recover_remote_import(
        product_dir,
        OzonWriteClient(OzonConfig.from_shop(args.shop, ROOT / "ozon-adapter/shops.json")),
        timeout_seconds=args.timeout,
        poll_interval_seconds=args.interval,
    )
    print(json.dumps({
        "product_id": product_dir.name,
        "task_id": result["task_id"],
        "status": result["status"],
        "query_count": (result.get("recovery") or {}).get("query_count"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
