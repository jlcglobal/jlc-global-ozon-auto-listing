#!/usr/bin/env python3
"""Prewarm the shared Ozon category tree cache using read-only endpoints."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-adapter"))

from ozon_adapter import OzonConfig, OzonReadOnlyClient  # noqa: E402
try:
    from scripts.build_category_zh_cache import build_official_cache  # noqa: E402
except ModuleNotFoundError:  # Direct execution: scripts/ is already on sys.path.
    from build_category_zh_cache import build_official_cache  # type: ignore  # noqa: E402


OFFICIAL_RU_LANGUAGE = "RU"
OFFICIAL_ZH_LANGUAGE = "ZH_HANS"


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def prewarm_category_tree(
    settings: Dict[str, Any],
    root: Path = ROOT,
    client: Any | None = None,
) -> Dict[str, Any]:
    shop = str(settings.get("shop_name") or "zhonglian1")
    cache_hours = max(0.0, float(settings.get("ozon_metadata_cache_hours", 24)))
    cache_path = root / "ozon-adapter/metadata/live-category-cache" / "current" / "category-tree.zh-CN.json"
    if cache_path.is_file() and time.time() - cache_path.stat().st_mtime <= cache_hours * 3600:
        return {"status": "cache_fresh", "shop": shop, "cache_path": str(cache_path)}
    if client is None:
        config = OzonConfig.from_shop(shop, root / "ozon-adapter/shops.json")
        client = OzonReadOnlyClient(config)
    ru_response = client.get_category_tree(OFFICIAL_RU_LANGUAGE)
    zh_response = client.get_category_tree(OFFICIAL_ZH_LANGUAGE)
    if not isinstance(ru_response, dict) or not isinstance(zh_response, dict):
        raise RuntimeError("Ozon category tree responses must be objects")
    catalog_path = root / "ozon-adapter/metadata/ozon-rules-2026-07-10/categories.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else []
    cache = build_official_cache(
        catalog,
        ru_response,
        zh_response,
        shop_id=shop,
        strict_catalog=False,
    )
    write_json_atomic(cache_path, cache)
    return {
        "status": "prewarmed",
        "shop": shop,
        "cache_path": str(cache_path),
        "cache_version": cache["cache_version"],
        "item_count": cache["item_count"],
        "ozon_read_api_calls": 2,
        "ozon_write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def main() -> int:
    settings = json.loads((ROOT / "config/pipeline-settings.json").read_text(encoding="utf-8"))
    os.environ.setdefault("OZON_METADATA_CACHE_HOURS", str(settings.get("ozon_metadata_cache_hours", 24)))
    print(json.dumps(prewarm_category_tree(settings), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
