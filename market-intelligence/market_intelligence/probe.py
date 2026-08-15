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


def _not_tested(source_id: str, message_zh: str, checked_at: str) -> Dict[str, Any]:
    return {
        "source_id": source_id,
        "state": "not_tested",
        "access_level": "unknown",
        "message_zh": message_zh,
        "checked_at": checked_at,
        "details": {},
    }


def _result_count(response: Dict[str, Any]) -> int:
    for key in ("queries", "items"):
        rows = response[key] if key in response else None
        if isinstance(rows, list):
            return len(rows)
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    for key in ("queries", "items"):
        rows = result[key] if key in result else None
        if isinstance(rows, list):
            return len(rows)
    value = response.get("total")
    if value is None and isinstance(response.get("result"), dict):
        value = response["result"].get("total")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        pass
    return 0


def _probe_search_endpoint(
    *,
    source_id: str,
    request,
    connected_message: str,
    empty_message: str,
    premium_message: str,
    denied_message: str,
    error_message: str,
    checked_at: str,
    count_key: str,
) -> Dict[str, Any]:
    try:
        response = request()
        count = _result_count(response)
        return {
            "source_id": source_id,
            "state": "connected" if count > 0 else "empty",
            "access_level": "official_read_only",
            "message_zh": connected_message if count > 0 else empty_message,
            "checked_at": checked_at,
            "details": {count_key: count},
        }
    except OzonAnalyticsPermissionError as exc:
        premium_required = "premium" in str(exc).lower()
        return {
            "source_id": source_id,
            "state": "premium_required" if premium_required else "permission_denied",
            "access_level": "subscription_required" if premium_required else "unavailable",
            "message_zh": premium_message if premium_required else denied_message,
            "checked_at": checked_at,
            "details": {"http_status": exc.status_code or 403},
        }
    except (OzonAnalyticsApiError, TypeError, ValueError, IndexError) as exc:
        return {
            "source_id": source_id,
            "state": "error",
            "access_level": "unavailable",
            "message_zh": error_message,
            "checked_at": checked_at,
            "details": {"error_type": type(exc).__name__},
        }


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

    query_record = _not_tested("ozon_product_queries", "没有可用于权限检测的店铺商品", stamp)
    detail_record = _not_tested("ozon_product_query_details", "没有可用于权限检测的店铺商品", stamp)
    if items:
        product_id = items[0].get("product_id")
        try:
            info = client.get_product_info([int(product_id)])
            info_items = info.get("items") or (info.get("result") or {}).get("items") or []
            sku = info_items[0].get("sku") if info_items else None
            if sku:
                date_to = _iso(now - timedelta(days=3))
                date_from = _iso(now - timedelta(days=10))
                query_record = _probe_search_endpoint(
                    source_id="ozon_product_queries",
                    request=lambda: client.get_product_queries(
                        [int(sku)],
                        date_from=date_from,
                        date_to=date_to,
                        page_size=1,
                    ),
                    connected_message="Ozon 官方商品搜索词汇总接口可用",
                    empty_message="Ozon 商品搜索词汇总接口可用，但当前SKU本窗口没有数据",
                    premium_message="Ozon 商品搜索词汇总需要 Premium 订阅",
                    denied_message="当前店铺无商品搜索词汇总权限",
                    error_message="Ozon 商品搜索词汇总权限检测失败",
                    checked_at=stamp,
                    count_key="result_count",
                )
                detail_record = _probe_search_endpoint(
                    source_id="ozon_product_query_details",
                    request=lambda: client.get_product_query_details(
                        [int(sku)],
                        date_from=date_from,
                        date_to=date_to,
                        limit_by_sku=1,
                        page_size=1,
                    ),
                    connected_message="Ozon 官方商品搜索词明细接口可用",
                    empty_message="Ozon 商品搜索词明细接口可用，但当前SKU本窗口没有搜索词",
                    premium_message="Ozon 商品搜索词明细需要 Premium 订阅",
                    denied_message="当前店铺无商品搜索词明细权限",
                    error_message="Ozon 商品搜索词明细权限检测失败",
                    checked_at=stamp,
                    count_key="query_count",
                )
        except (OzonAnalyticsApiError, TypeError, ValueError, IndexError) as exc:
            query_record = {
                "source_id": "ozon_product_queries",
                "state": "error",
                "access_level": "unavailable",
                "message_zh": "Ozon 商品搜索词汇总权限检测失败",
                "checked_at": stamp,
                "details": {"error_type": type(exc).__name__},
            }
            detail_record = {
                "source_id": "ozon_product_query_details",
                "state": "error",
                "access_level": "unavailable",
                "message_zh": "Ozon 商品搜索词明细权限检测失败",
                "checked_at": stamp,
                "details": {"error_type": type(exc).__name__},
            }
    records["ozon_product_queries"] = query_record
    records["ozon_product_query_details"] = detail_record
    records["ozon_free_market_analytics"] = {
        "source_id": "ozon_free_market_analytics",
        "state": "login_required",
        "access_level": "official_free_login",
        "message_zh": "Ozon 免费选品分析需要首次 Ozon ID 登录",
        "checked_at": stamp,
        "details": {"login_method": "ozon_id"},
    }
    return records
