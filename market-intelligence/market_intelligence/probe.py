"""Read-only capability checks for Ozon market data sources."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .ozon_client import (
    OzonAnalyticsApiError,
    OzonAnalyticsPermissionError,
    OzonAnalyticsReadOnlyClient,
)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def probe_ozon_sources(
    client: OzonAnalyticsReadOnlyClient,
    *,
    checked_at: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    now = checked_at or datetime.now(timezone.utc)
    stamp = now.astimezone().isoformat(timespec="seconds")
    records: Dict[str, Dict[str, Any]] = {}

    try:
        product_page = client.list_products(limit=1)
        result = product_page.get("result") or {}
        items = result.get("items") or []
        total = int(result.get("total") or 0)
        records["ozon_seller_catalog"] = {
            "source_id": "ozon_seller_catalog",
            "state": "connected",
            "access_level": "official_read_only",
            "message_zh": "Ozon Seller API 商品目录只读访问正常",
            "checked_at": stamp,
            "details": {"product_count": total},
        }
    except OzonAnalyticsApiError as exc:
        records["ozon_seller_catalog"] = {
            "source_id": "ozon_seller_catalog",
            "state": "error",
            "access_level": "unavailable",
            "message_zh": "Ozon Seller API 商品目录读取失败",
            "checked_at": stamp,
            "details": {"http_status": exc.status_code or "unknown"},
        }
        items = []

    query_record = {
        "source_id": "ozon_product_queries",
        "state": "not_tested",
        "access_level": "unknown",
        "message_zh": "没有可用于权限检测的店铺商品",
        "checked_at": stamp,
        "details": {},
    }
    if items:
        product_id = items[0].get("product_id")
        try:
            info = client.get_product_info([int(product_id)])
            info_items = info.get("items") or (info.get("result") or {}).get("items") or []
            sku = info_items[0].get("sku") if info_items else None
            if sku:
                queries = client.get_product_queries(
                    [int(sku)],
                    date_from=_iso(now - timedelta(days=7)),
                    date_to=_iso(now),
                    page_size=1,
                )
                query_record = {
                    "source_id": "ozon_product_queries",
                    "state": "connected",
                    "access_level": "official_read_only",
                    "message_zh": "Ozon 官方商品搜索词接口可用",
                    "checked_at": stamp,
                    "details": {"result_count": int(queries.get("total") or 0)},
                }
        except OzonAnalyticsPermissionError as exc:
            premium_required = "premium" in str(exc).lower()
            query_record = {
                "source_id": "ozon_product_queries",
                "state": "premium_required" if premium_required else "permission_denied",
                "access_level": "subscription_required" if premium_required else "unavailable",
                "message_zh": "Ozon 商品搜索词需要 Premium 订阅" if premium_required else "当前店铺无商品搜索词权限",
                "checked_at": stamp,
                "details": {"http_status": exc.status_code or 403},
            }
        except (OzonAnalyticsApiError, TypeError, ValueError, IndexError) as exc:
            query_record = {
                "source_id": "ozon_product_queries",
                "state": "error",
                "access_level": "unavailable",
                "message_zh": "Ozon 商品搜索词权限检测失败",
                "checked_at": stamp,
                "details": {"error_type": type(exc).__name__},
            }
    records["ozon_product_queries"] = query_record
    records["ozon_free_market_analytics"] = {
        "source_id": "ozon_free_market_analytics",
        "state": "login_required",
        "access_level": "official_free_login",
        "message_zh": "Ozon 免费选品分析需要首次 Ozon ID 登录",
        "checked_at": stamp,
        "details": {"login_method": "ozon_id"},
    }
    return records
