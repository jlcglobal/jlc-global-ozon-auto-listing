#!/usr/bin/env python3
"""Background Ozon image CDN confirmation queue; performs read-only calls only."""

from __future__ import annotations

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


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    settings = load_json(ROOT / "config/pipeline-settings.json")
    load_shop_environment(settings)
    os.environ["UPLOAD_MODE"] = "production"
    shop = str(settings.get("shop_name") or "zhonglian1")
    client = OzonWriteClient(OzonConfig.from_shop(shop, ROOT / "ozon-adapter/shops.json"))
    queue_path = ROOT / "image-channel-queue.json"
    pid_path = ROOT / "logs/image-status-monitor.pid"
    try:
        items = list((load_json(queue_path).get("items") or [])) if queue_path.is_file() else []
        for item in items:
            product_dir = ROOT / "products" / item["product_id"]
            marker = ROOT / "logs/product-deletion-tombstones" / f"{item['product_id']}.deleted"
            if marker.is_file() or not product_dir.is_dir():
                continue
            try:
                recover_remote_import(product_dir, client, timeout_seconds=1)
            except Exception as exc:
                with (ROOT / "logs/image-status-monitor.log").open("a", encoding="utf-8") as log:
                    log.write(f"{item['product_id']}: {type(exc).__name__}: {exc}\n")
    finally:
        pid_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
