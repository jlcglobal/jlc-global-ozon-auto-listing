#!/usr/bin/env python3
"""Import today's official Ozon captures and build a local daily trend report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "market-intelligence"))

from market_intelligence import (  # noqa: E402
    MarketEnricher,
    MarketStore,
    build_trend_report,
    import_bestsellers_report,
    import_search_query_file,
    write_trend_report,
)


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def newest(paths: List[Path]) -> Optional[Path]:
    files = [path for path in paths if path.is_file()]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bestsellers-report", type=Path)
    parser.add_argument("--search-queries", type=Path)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--allow-stale", action="store_true", help="Only for manual recovery; normal daily runs reject stale captures")
    parser.add_argument("--keyword-enrichment-limit", type=int, default=500)
    parser.add_argument("--image-enrichment-limit", type=int, default=30)
    args = parser.parse_args()

    now = local_now()
    observed_at = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    store = MarketStore(ROOT / "market-intelligence/market.sqlite")
    store.initialize()
    result: dict[str, object] = {"schema_version": "1.0.0", "run_at": observed_at, "mode": "report_only" if args.report_only else "daily_refresh"}

    if not args.report_only:
        bestsellers = args.bestsellers_report or newest(list((Path.home() / "Downloads").glob("ozon_bestsellers*.xlsx")))
        if bestsellers is None:
            raise SystemExit("未找到今天下载的 Ozon 热门商品报表")
        if not args.allow_stale and datetime.fromtimestamp(bestsellers.stat().st_mtime).astimezone().date().isoformat() != today:
            raise SystemExit("最新 Ozon 热门商品报表不是今天下载的，已停止导入以避免重复旧数据")
        bestseller_result = import_bestsellers_report(bestsellers, store, observed_at)
        store.upsert_source_status({
            "source_id": "ozon_free_market_analytics", "state": "connected", "access_level": "official_read_only",
            "message_zh": "Ozon 官方免费热门商品报表已完成每日更新", "checked_at": observed_at,
            "details": {"product_count": bestseller_result["imported_products"], "period_to": bestseller_result["period_to"]},
        })
        result["bestsellers"] = bestseller_result

        query_path = args.search_queries
        if query_path:
            query_source = json.loads(query_path.read_text(encoding="utf-8"))
            if not args.allow_stale and str(query_source.get("observed_at", ""))[:10] != today:
                raise SystemExit("Ozon 搜索词采集文件不是今天生成的，已停止导入")
            result["search_queries"] = import_search_query_file(query_path, store)
        else:
            result["search_queries"] = {"state": "not_provided", "notice": "本次未提供搜索词采集文件"}

    enricher = MarketEnricher(store, ROOT / "runtime/market-intelligence/images")
    result["enrichment"] = enricher.enrich_batch(
        keyword_limit=max(0, args.keyword_enrichment_limit),
        image_limit=max(0, args.image_enrichment_limit),
    )

    trend = build_trend_report(store, observed_at)
    result["trend"] = trend
    result["reports"] = write_trend_report(trend, ROOT / "market-intelligence/reports")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
