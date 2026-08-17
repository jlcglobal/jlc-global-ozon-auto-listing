#!/usr/bin/env python3
"""Read the complete Ozon catalog for each connected JLC shop in the background.

This is read-only: it never submits product updates, stock, warehouse, or
activation requests. Each shop writes to its own workbench cache.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collector" / "local-ingest"))
sys.path.insert(0, str(ROOT / "market-intelligence"))

import app  # noqa: E402
from market_intelligence import OzonAnalyticsReadOnlyClient  # noqa: E402
from market_intelligence.search_visibility_optimizer import (  # noqa: E402
    build_search_visibility_plan,
    collect_seller_search_visibility,
)


DEFAULT_SHOPS = ("zhonglian1", "zhonglian2", "volttech", "zhonglian3", "zhonglian4", "zhonglian5")


def emit(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def sync_shop(shop_id: str, *, period_days: int, order_period_days: int) -> Dict[str, Any]:
    date_from, date_to = app._seller_search_window(period_days)
    order_date_from, order_date_to = app._seller_order_window(order_period_days)
    _shop, secrets = app._default_search_shop(shop_id)
    client = OzonAnalyticsReadOnlyClient(secrets["client_id"], secrets["api_key"])
    source = collect_seller_search_visibility(
        client,
        shop_id=shop_id,
        date_from=date_from,
        date_to=date_to,
        order_date_from=order_date_from,
        order_date_to=order_date_to,
        order_period_days=order_period_days,
        page_size=1000,
        max_products=0,
        period_days=period_days,
    )
    previous_plan = app._load_search_visibility_plan(shop_id)
    plan = app._search_visibility_preserve_action_state(build_search_visibility_plan(source), previous_plan)
    product_count = len(source.get("items") or [])
    query_count = sum(
        int(((item.get("evidence") or {}).get("totals") or {}).get("query_count") or 0)
        for item in plan.get("actions") or []
    )
    order_count = sum(float(item.get("order_count") or 0) for item in plan.get("actions") or [])
    query_error_count = len(source.get("query_errors") or [])
    order_error_count = len(source.get("order_errors") or [])
    source_state = (
        "connected" if query_count else
        "product_info_only" if query_error_count and product_count else
        "empty" if product_count else "connected_empty"
    )
    plan.update({
        "mode": "read_only",
        "available": product_count > 0,
        "date_from": date_from,
        "date_to": date_to,
        "order_period_days": order_period_days,
        "order_date_from": order_date_from,
        "order_date_to": order_date_to,
        "source_status": {
            "state": source_state,
            "details": {
                "query_count": query_count,
                "product_count": product_count,
                "product_limit": "all",
                "query_error_count": query_error_count,
                "order_count": order_count,
                "order_period_days": order_period_days,
                "order_error_count": order_error_count,
                "order_read_api_calls": source.get("order_read_api_calls") or 0,
            },
        },
        "notice": (
            f"已从 Ozon 下载 {product_count} 个商品资料、{query_count} 条搜索词和近{order_period_days}天 "
            f"{int(order_count)} 个出单量；没有提交Ozon更新，没有调用库存接口。"
        ),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    })
    plan.setdefault("safety", {}).update({"read_only": True, "write_api_calls": 0, "inventory_api_calls": 0})
    app._write_search_visibility_plan(plan)
    return {
        "shop_id": shop_id,
        "products": product_count,
        "queries": query_count,
        "orders": order_count,
        "query_errors": query_error_count,
        "order_errors": order_error_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shops", nargs="*", default=list(DEFAULT_SHOPS))
    parser.add_argument("--period-days", type=int, default=15)
    parser.add_argument("--order-period-days", type=int, default=90)
    args = parser.parse_args()
    shops = [str(shop).strip() for shop in args.shops if str(shop).strip()]
    emit("started", shops=shops, mode="read_only_all_products")
    failures = 0
    for shop_id in shops:
        emit("shop_started", shop_id=shop_id)
        try:
            emit("shop_completed", **sync_shop(
                shop_id,
                period_days=max(1, min(30, args.period_days)),
                order_period_days=max(1, min(90, args.order_period_days)),
            ))
        except Exception as exc:  # Continue with the next shop.
            failures += 1
            emit("shop_failed", shop_id=shop_id, error_type=type(exc).__name__, error=str(exc))
            traceback.print_exc()
    emit("finished", failures=failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
