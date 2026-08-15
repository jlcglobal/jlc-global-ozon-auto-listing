#!/usr/bin/env python3
"""Import a downloaded official Ozon bestsellers workbook."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "market-intelligence"))

from market_intelligence import MarketStore, import_bestsellers_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    store = MarketStore(ROOT / "market-intelligence/market.sqlite")
    store.initialize()
    observed_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    result = import_bestsellers_report(args.report, store, observed_at)
    store.upsert_source_status({
        "source_id": "ozon_free_market_analytics",
        "state": "connected",
        "access_level": "official_read_only",
        "message_zh": "Ozon 官方免费热门商品报表已导入",
        "checked_at": observed_at,
        "details": {"product_count": result["imported_products"], "period_to": result["period_to"]},
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
