"""Market-intelligence routes and helpers extracted from app.py (2026-08-14).

Imported from the BOTTOM of app.py after every shared name is defined; the
routes register on the existing FastAPI instance via `from app import *`.
"""

from __future__ import annotations

import asyncio
import copy as copy_module
import json
import math
import mimetypes
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from market_intelligence import (
    MarketEnricher,
    MarketStore,
    OzonAnalyticsApiError,
    OzonAnalyticsPermissionError,
    OzonAnalyticsReadOnlyClient,
    build_search_visibility_plan,
    build_traffic_performance_plan,
    collect_seller_search_visibility,
    normalize_seerfar_keyword_rows,
    normalize_yandex_wordstat_rows,
    parse_ozon_product_query_text,
    parse_yandex_wordstat_text,
)
from ozon_adapter.config import OzonConfig
from ozon_uploader.client import OzonUploadApiError, OzonWriteClient
from russian_seo_rules import canonical_hashtag
from workbench_stores import load_registry, read_secret

@app.get("/api/workbench/market-intelligence/status")
def workbench_market_intelligence_status() -> Dict[str, Any]:
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    categories = load_optional_json(MARKET_CATEGORIES_PATH, {"schema_version": "1.0.0", "categories": []})
    counts = store.counts()
    ranking_available = counts["products"] > 0
    sources = store.list_source_status()
    latest_checked_at = max((item.get("checked_at") or "" for item in sources), default="") or "unknown"
    trend = load_optional_json(MARKET_TREND_REPORT_PATH, {
        "state": "collecting", "days_collected": counts["snapshots"] and 1 or 0,
        "notice": "等待每日快照积累后生成趋势对比",
    })
    return {
        "schema_version": "1.0.0",
        "module_state": "data_ready" if ranking_available else "data_source_setup",
        "categories": categories.get("categories") or [],
        "sources": sources,
        "counts": counts,
        "ranking_available": ranking_available,
        "last_updated_at": latest_checked_at,
        "trend": trend,
        "notice": "Ozon 官方市场商品数据已就绪" if ranking_available else "真实市场商品数据接入后才会显示热销榜和飙升榜",
    }


def market_category(category_key: str) -> Dict[str, Any]:
    config = load_optional_json(MARKET_CATEGORIES_PATH, {"categories": []})
    for category in config.get("categories") or []:
        if category.get("key") == category_key and category.get("enabled", True):
            return dict(category)
    raise HTTPException(status_code=404, detail="未找到该选品类目")


@app.get("/api/workbench/market-intelligence/products")
def workbench_market_intelligence_products(
    ranking: str = "hot",
    category: str = "home",
    period: int = 30,
    page: int = 1,
    page_size: int = 24,
    q: str = "",
) -> Dict[str, Any]:
    if period not in {7, 30}:
        raise HTTPException(status_code=400, detail="榜单周期只支持7天或30天")
    category_rule = market_category(category)
    if period == 7:
        return {
            "schema_version": "1.0.0",
            "items": [], "total": 0, "page": max(1, page), "page_size": page_size,
            "ranking": ranking, "category_key": category, "period_days": 7,
            "available": False,
            "notice": "当前官方公开商品榜单只提供近30天数据；7天榜将在积累每日快照后开放",
        }
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    try:
        result = store.list_ranked_products(
            category_rule=category_rule,
            ranking=ranking,
            page=page,
            page_size=page_size,
            query=q,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "schema_version": "1.0.0",
        **result,
        "period_days": 30,
        "available": True,
        "notice": "数据来自 Ozon 官方免费市场分析报告；排序与FBS适配度由本地规则计算",
    }


@app.get("/api/workbench/market-intelligence/products/{source_product_id}")
def workbench_market_intelligence_product(source_product_id: str) -> Dict[str, Any]:
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    product = store.get_product(source_product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="未找到该 Ozon 商品")
    product = MarketEnricher(store, MARKET_IMAGE_CACHE_DIR).enrich_product(source_product_id)
    enrichment = dict(product.pop("enrichment", {}) or {})
    merged_keywords = []
    seen_keywords = set()
    for keyword in product.get("keywords") or []:
        normalized = " ".join(str(keyword.get("keyword_ru") or "").lower().split())
        if not normalized or normalized in seen_keywords:
            continue
        seen_keywords.add(normalized)
        merged_keywords.append(keyword)
    product["keywords"] = merged_keywords
    return {
        "schema_version": "1.0.0",
        **product,
        "image_state": enrichment.get("image_state") or ("ready" if product.get("image_url") != "unknown" else "syncing"),
        "keyword_state": enrichment.get("keyword_state") or ("ready" if product["keywords"] else "source_pending"),
        "keyword_notice": "关键词根据公开数据和商品信息整理",
    }


@app.get("/api/workbench/market-intelligence/images/{source_product_id}")
def workbench_market_intelligence_image(source_product_id: str) -> FileResponse:
    if not re.fullmatch(r"[0-9A-Za-z_-]+", source_product_id):
        raise HTTPException(status_code=404, detail="主图不存在")
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    try:
        enrichment = store.get_product_enrichment(source_product_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="主图不存在") from error
    raw_path = enrichment.get("image_local_path")
    if not raw_path or raw_path == "unknown":
        raise HTTPException(status_code=404, detail="主图仍在同步")
    image_path = Path(str(raw_path)).resolve()
    cache_root = MARKET_IMAGE_CACHE_DIR.resolve()
    if not image_path.is_file() or cache_root not in image_path.parents:
        raise HTTPException(status_code=404, detail="主图仍在同步")
    return FileResponse(
        image_path,
        media_type=mimetypes.guess_type(image_path.name)[0] or "application/octet-stream",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/workbench/market-intelligence/keywords")
def workbench_market_intelligence_keywords(category: str = "", limit: int = 12) -> Dict[str, Any]:
    if category:
        market_category(category)
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    items = [
        item for item in store.list_keywords(category_key=category, limit=max(limit * 4, 50))
        if (item.get("evidence") or {}).get("source") == "ozon_official_search_queries"
    ][:max(1, min(200, int(limit)))]
    return {
        "schema_version": "1.0.0",
        "items": items,
        "total": len(items),
        "category_key": category or "all",
        "period_days": 7,
        "available": bool(items),
        "notice": "数据来自 Ozon 官方近7天搜索查询；中文为本地翻译，不改变俄文原词",
    }


def _seller_search_window(period_days: int) -> Tuple[str, str]:
    days = max(1, min(30, int(period_days)))
    date_to = datetime.now(timezone.utc) - timedelta(days=3)
    date_from = date_to - timedelta(days=days)
    return (
        date_from.isoformat(timespec="seconds").replace("+00:00", "Z"),
        date_to.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _seller_order_window(period_days: int) -> Tuple[str, str]:
    days = max(1, min(90, int(period_days)))
    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=days)
    return (
        date_from.isoformat(timespec="seconds").replace("+00:00", "Z"),
        date_to.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _default_search_shop(store_id: str = "") -> Tuple[Dict[str, Any], Dict[str, str]]:
    registry = load_registry(ROOT)
    target_store_id = str(store_id or registry.get("default_read_shop") or "").strip()
    shop = next((item for item in registry.get("shops") or [] if str(item.get("id")) == target_store_id), None)
    if not shop:
        raise HTTPException(status_code=422, detail="没有配置默认 Ozon 店铺")
    secrets = read_secret(ROOT, shop)
    client_id = secrets.get(str(shop["client_id_env"]), "")
    api_key = secrets.get(str(shop["api_key_env"]), "")
    if not client_id or not api_key:
        raise HTTPException(status_code=422, detail="当前 Ozon 店铺没有配置只读凭证")
    return shop, {"client_id": client_id, "api_key": api_key}


def _unavailable_search_visibility_plan(
    *,
    shop_id: str,
    period_days: int,
    date_from: str,
    date_to: str,
    state: str,
    notice: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "mode": "read_only",
        "source": "ozon_seller_search_visibility",
        "shop_id": shop_id,
        "period_days": period_days,
        "recommended_schedule_days": 7,
        "generated_at": now_iso(),
        "available": False,
        "notice": notice,
        "summary": {
            "products": 0,
            "stable_tag_only": 0,
            "title_optimization_candidates": 0,
            "tag_only_candidates": 0,
            "insufficient_data": 0,
        },
        "batches": [],
        "actions": [],
        "source_status": {"state": state, "details": details or {}},
        "safety": {
            "dry_run_only": True,
            "read_only": True,
            "write_api_calls": 0,
            "inventory_api_calls": 0,
            "requires_explicit_write_scope_before_ozon_update": True,
        },
        "write_api_calls": 0,
        "inventory_api_calls": 0,
        "date_from": date_from,
        "date_to": date_to,
    }


def _search_visibility_plan_cache_path(shop_id: str) -> Path:
    safe_shop_id = re.sub(r"[^0-9A-Za-z_-]+", "-", str(shop_id or "")).strip("-")
    return MARKET_SEARCH_VISIBILITY_PLAN_CACHE_DIR / f"{safe_shop_id or 'unknown'}.json"


def _load_search_visibility_plan(shop_id: str = "") -> Dict[str, Any]:
    requested_shop_id = str(shop_id or "").strip()
    if requested_shop_id:
        cache_path = _search_visibility_plan_cache_path(requested_shop_id)
        if cache_path.is_file():
            return load_optional_json(cache_path, {})
        if MARKET_SEARCH_VISIBILITY_PLAN_PATH.is_file():
            legacy_plan = load_optional_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH, {})
            if str(legacy_plan.get("shop_id") or "").strip() == requested_shop_id:
                atomic_write_json(cache_path, legacy_plan)
                return legacy_plan
        return {}
    if MARKET_SEARCH_VISIBILITY_PLAN_PATH.is_file():
        return load_optional_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH, {})
    return {}


def _write_search_visibility_plan(plan: Dict[str, Any]) -> None:
    atomic_write_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
    shop_id = str(plan.get("shop_id") or "").strip()
    if shop_id:
        atomic_write_json(_search_visibility_plan_cache_path(shop_id), plan)


def _search_visibility_preserve_action_state(plan: Dict[str, Any], previous_plan: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(previous_plan, dict):
        return plan
    preserved_keys = (
        "last_upload",
        "last_upload_status_check",
        "last_yandex_wordstat_import",
        "last_ozon_product_query_import",
        "last_seerfar_keyword_import",
    )
    state_by_product: Dict[str, Dict[str, Any]] = {}
    for action in previous_plan.get("actions") or []:
        if not isinstance(action, dict):
            continue
        product_id = str(action.get("product_id") or "").strip()
        if not product_id:
            continue
        state = {
            key: copy_module.deepcopy(action.get(key))
            for key in preserved_keys
            if isinstance(action.get(key), dict)
        }
        if state:
            state_by_product[product_id] = state
    for action in plan.get("actions") or []:
        if not isinstance(action, dict):
            continue
        product_id = str(action.get("product_id") or "").strip()
        if product_id in state_by_product:
            action.update(copy_module.deepcopy(state_by_product[product_id]))
    for key in ("last_upload", "last_yandex_wordstat_import", "last_ozon_product_query_import", "last_seerfar_keyword_import"):
        if isinstance(previous_plan.get(key), dict):
            plan[key] = copy_module.deepcopy(previous_plan[key])
    return plan


@app.post("/api/workbench/market-intelligence/search-visibility/sync")
async def workbench_search_visibility_sync(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="搜索词读取参数必须是JSON对象")
    period_days = max(1, min(30, int(payload.get("period_days") or 15)))
    order_period_days = max(1, min(90, int(payload.get("order_period_days") or 90)))
    requested_product_limit = payload.get("product_limit")
    product_limit = 0 if requested_product_limit in {None, "", 0, "0", "all", "ALL"} else max(1, int(requested_product_limit))
    date_from, date_to = _seller_search_window(period_days)
    order_date_from, order_date_to = _seller_order_window(order_period_days)
    shop, secrets = _default_search_shop(str(payload.get("store_id") or ""))
    shop_id = str(shop["id"])
    previous_plan = _load_search_visibility_plan(shop_id)
    client = OzonAnalyticsReadOnlyClient(secrets["client_id"], secrets["api_key"], timeout_seconds=10)
    try:
        source = await asyncio.to_thread(collect_seller_search_visibility,
            client,
            shop_id=shop_id,
            date_from=date_from,
            date_to=date_to,
            order_date_from=order_date_from,
            order_date_to=order_date_to,
            order_period_days=order_period_days,
            page_size=1000,
            max_products=product_limit,
            period_days=period_days,
        )
        plan = _search_visibility_preserve_action_state(build_search_visibility_plan(source), previous_plan)
        query_count = sum(
            int(((item.get("evidence") or {}).get("totals") or {}).get("query_count") or 0)
            for item in plan.get("actions") or []
        )
        order_count = sum(
            float(item.get("order_count") or 0)
            for item in plan.get("actions") or []
        )
        product_count = len(source.get("items") or [])
        query_error_count = len(source.get("query_errors") or [])
        order_error_count = len(source.get("order_errors") or [])
        if query_count > 0:
            source_state = "connected"
            notice = f"已从 Ozon 下载 {product_count} 个商品资料、{query_count} 条搜索词和近{order_period_days}天 {int(order_count)} 个出单量；没有提交Ozon更新，没有调用库存接口。"
        elif query_error_count > 0 and product_count > 0:
            source_state = "product_info_only"
            notice = f"已从 Ozon 下载 {product_count} 个商品资料和近{order_period_days}天 {int(order_count)} 个出单量；搜索词暂时未读取到，商品信息已可查看；没有提交Ozon更新，没有调用库存接口。"
        else:
            source_state = "empty" if product_count > 0 else "connected_empty"
            notice = f"已连接 Ozon 并下载 {product_count} 个商品资料和近{order_period_days}天 {int(order_count)} 个出单量，但本窗口没有搜索词；没有提交Ozon更新，没有调用库存接口。"
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
                    "product_limit": "all" if product_limit == 0 else product_limit,
                    "query_error_count": query_error_count,
                    "order_count": order_count,
                    "order_period_days": order_period_days,
                    "order_error_count": order_error_count,
                    "order_read_api_calls": source.get("order_read_api_calls") or 0,
                },
            },
            "notice": notice,
            "write_api_calls": 0,
            "inventory_api_calls": 0,
        })
        plan.setdefault("safety", {}).update({"read_only": True, "write_api_calls": 0, "inventory_api_calls": 0})
    except OzonAnalyticsPermissionError as exc:
        premium_required = "premium" in str(exc).lower()
        plan = _unavailable_search_visibility_plan(
            shop_id=shop_id,
            period_days=period_days,
            date_from=date_from,
            date_to=date_to,
            state="premium_required" if premium_required else "permission_denied",
            notice="Ozon 搜索词需要 Premium 或当前店铺没有搜索词权限；没有提交Ozon更新，没有调用库存接口。",
            details={"http_status": exc.status_code or 403},
        )
    except (OzonAnalyticsApiError, ValueError) as exc:
        plan = _unavailable_search_visibility_plan(
            shop_id=shop_id,
            period_days=period_days,
            date_from=date_from,
            date_to=date_to,
            state="error",
            notice="Ozon 搜索词读取失败；没有提交Ozon更新，没有调用库存接口。",
            details={"error_type": type(exc).__name__},
        )
    _write_search_visibility_plan(plan)
    store = MarketStore(MARKET_DB_PATH)
    store.initialize()
    source_status = plan.get("source_status") or {}
    status_state = str(source_status.get("state") or ("connected" if plan.get("available") else "empty"))
    store.upsert_source_status({
        "source_id": "ozon_product_query_details",
        "state": status_state,
        "access_level": (
            "official_read_only" if status_state in {"connected", "empty", "product_info_only", "connected_empty"}
            else "subscription_required" if status_state == "premium_required"
            else "unavailable"
        ),
        "message_zh": str(plan.get("notice") or "Ozon 搜索词状态已更新"),
        "checked_at": str(plan.get("generated_at") or now_iso()),
        "details": dict(source_status.get("details") or {}),
    })
    return plan


@app.post("/api/workbench/market-intelligence/search-visibility/dry-run")
async def workbench_search_visibility_dry_run(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="搜索可见性 dry-run 输入必须是JSON对象")
    plan = build_search_visibility_plan(payload)
    plan["available"] = True
    atomic_write_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
    return {
        **plan,
        "notice": "已生成本地搜索可见性优化方案；没有提交Ozon更新，没有调用库存接口。",
    }


@app.get("/api/workbench/market-intelligence/search-visibility/latest")
def workbench_search_visibility_latest(store_id: str = "") -> Dict[str, Any]:
    plan = _load_search_visibility_plan(store_id)
    if not plan:
        return {
            "schema_version": "1.0.0",
            "available": False,
            "shop_id": str(store_id or "").strip(),
            "notice": "这个店铺还没有下载 Ozon 商品信息和搜索词。",
            "write_api_calls": 0,
            "inventory_api_calls": 0,
        }
    if store_id:
        atomic_write_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH, plan)
    return {
        **plan,
        "available": bool(plan.get("available", True)),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def _search_visibility_query_rows_for_source(action: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence = action.get("evidence") if isinstance(action.get("evidence"), dict) else {}
    rows: List[Dict[str, Any]] = []
    for row in evidence.get("top_queries") or []:
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        rows.append({
            **row,
            "query": row.get("query"),
            "impressions": row.get("impressions", metrics.get("impressions")),
            "clicks": row.get("clicks", metrics.get("clicks")),
            "orders": row.get("orders", metrics.get("orders")),
            "revenue_rub": row.get("revenue_rub", metrics.get("revenue_rub")),
        })
    return rows


def _search_visibility_action_to_source_item(action: Dict[str, Any]) -> Dict[str, Any]:
    evidence = action.get("evidence") if isinstance(action.get("evidence"), dict) else {}
    source_item: Dict[str, Any] = {
        "product_id": action.get("product_id") or "",
        "offer_ids": action.get("offer_ids") or [],
        "title": action.get("current_title") or "",
        "sku": action.get("sku") or "",
        "image_url": action.get("image_url") or "",
        "images": action.get("images") or [],
        "price": action.get("price") or "",
        "currency": action.get("currency") or "",
        "stock": action.get("stock") or "",
        "created_at": action.get("created_at") or "",
        "updated_at": action.get("updated_at") or "",
        "order_count": action.get("order_count") or 0,
        "category_name": action.get("category_name") or "",
        "brand": action.get("brand") or "",
        "source_url": action.get("source_url") or "",
        "measurements": action.get("measurements") if isinstance(action.get("measurements"), dict) else {},
        "product_attributes": action.get("product_attributes") if isinstance(action.get("product_attributes"), list) else [],
        "current_intro": action.get("current_intro") or "",
        "queries": _search_visibility_query_rows_for_source(action),
        "seerfar_keyword_mining": {"items": evidence.get("top_seerfar_keyword_mining") or []},
        "seerfar_keyword_reverse": {"items": evidence.get("top_seerfar_keyword_reverse") or []},
        "yandex_wordstat": {"items": evidence.get("top_yandex_wordstat") or []},
        "trial_reference_terms": evidence.get("top_trial_terms") or [],
    }
    if isinstance(action.get("existing_subject_tags"), list) and action.get("existing_subject_tags"):
        source_item["existing_subject_tags"] = action.get("existing_subject_tags")
    return source_item


def _search_visibility_yandex_import_report_path(product_id: str, shop_id: str) -> Path:
    safe_product_id = re.sub(r"[^0-9A-Za-z_-]+", "-", product_id).strip("-") or "unknown"
    safe_shop_id = re.sub(r"[^0-9A-Za-z_-]+", "-", shop_id).strip("-") or "shop"
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    return MARKET_YANDEX_WORDSTAT_IMPORT_DIR / f"{stamp}-{safe_shop_id}-{safe_product_id}.json"


def _search_visibility_ozon_product_query_import_report_path(product_id: str, shop_id: str) -> Path:
    safe_product_id = re.sub(r"[^0-9A-Za-z_-]+", "-", product_id).strip("-") or "unknown"
    safe_shop_id = re.sub(r"[^0-9A-Za-z_-]+", "-", shop_id).strip("-") or "shop"
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    return MARKET_OZON_PRODUCT_QUERY_IMPORT_DIR / f"{stamp}-{safe_shop_id}-{safe_product_id}.json"


def _search_visibility_seerfar_import_report_path(product_id: str, shop_id: str) -> Path:
    safe_product_id = re.sub(r"[^0-9A-Za-z_-]+", "-", product_id).strip("-") or "unknown"
    safe_shop_id = re.sub(r"[^0-9A-Za-z_-]+", "-", shop_id).strip("-") or "shop"
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    return MARKET_SEERFAR_KEYWORD_IMPORT_DIR / f"{stamp}-{safe_shop_id}-{safe_product_id}.json"


def _seerfar_keyword_jobs() -> Dict[str, Any]:
    value = load_optional_json(MARKET_SEERFAR_KEYWORD_JOBS_PATH, {"schema_version": "1.0.0", "jobs": []})
    if not isinstance(value, dict):
        return {"schema_version": "1.0.0", "jobs": []}
    jobs = value.get("jobs")
    return {"schema_version": "1.0.0", "jobs": [job for job in jobs if isinstance(job, dict)] if isinstance(jobs, list) else []}


def _save_seerfar_keyword_jobs(value: Dict[str, Any]) -> None:
    atomic_write_json(MARKET_SEERFAR_KEYWORD_JOBS_PATH, value)


def _seerfar_worker_status(queue: Dict[str, Any], stall_seconds: int = 90) -> Dict[str, Any]:
    """Detect a stopped browser worker while keyword jobs are still pending."""
    jobs = queue.get("jobs") or []
    queued = [job for job in jobs if str(job.get("status") or "") == "queued"]
    running = [job for job in jobs if str(job.get("status") or "") == "running"]
    login_required = [job for job in jobs if str(job.get("status") or "") == "login_required"]
    timestamps: List[datetime] = []
    for job in jobs:
        for key in ("claimed_at", "completed_at", "failed_at", "resumed_after_login_at", "created_at"):
            value = str(job.get(key) or "").strip()
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(value)
                timestamps.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
            except ValueError:
                continue
    latest = max(timestamps) if timestamps else None
    now = datetime.now(timezone.utc)
    latest_age = (now - latest.astimezone(timezone.utc)).total_seconds() if latest else float("inf")
    running_ages = []
    for job in running:
        try:
            claimed = datetime.fromisoformat(str(job.get("claimed_at") or ""))
            if claimed.tzinfo is None:
                claimed = claimed.replace(tzinfo=timezone.utc)
            running_ages.append((now - claimed.astimezone(timezone.utc)).total_seconds())
        except ValueError:
            running_ages.append(float("inf"))
    pending_count = len(queued) + len(running) + len(login_required)
    stalled = bool(
        pending_count
        and not login_required
        and (
            (running and max(running_ages or [float("inf")]) > stall_seconds)
            or (not running and latest_age > stall_seconds)
        )
    )
    return {
        "stalled": stalled,
        "queued_count": len(queued),
        "running_count": len(running),
        "pending_count": pending_count,
        "last_activity_at": latest.isoformat() if latest else "unknown",
        "stall_seconds": stall_seconds,
        "message": "Seerfar 关键词队列已停止，请重新加载插件并刷新已登录的 Seerfar 页面。" if stalled else "",
    }


def _seerfar_batch_reverse_is_unavailable(queue: Dict[str, Any]) -> bool:
    """Detect when this Seerfar account has no usable reverse results for a batch."""
    sampled = [
        job for job in queue.get("jobs") or []
        if isinstance(job, dict)
        and str(job.get("kind") or "").startswith("deduplicated_existing_product")
        and str(job.get("mode") or "") == "keyword_reverse"
        and str(job.get("status") or "") in {"completed", "completed_without_reverse_result"}
    ]
    if len(sampled) < 20:
        return False
    successful = [job for job in sampled if str(job.get("status") or "") == "completed" and int(job.get("imported_count") or 0) > 0]
    return not successful


def _skip_unavailable_batch_reverse_jobs(queue: Dict[str, Any]) -> int:
    """Use title mining directly after repeated empty reverse lookups in one batch."""
    if not _seerfar_batch_reverse_is_unavailable(queue):
        return 0
    updated = 0
    for job in queue.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        if (
            str(job.get("status") or "") == "queued"
            and str(job.get("mode") or "") == "keyword_reverse"
            and str(job.get("kind") or "").startswith("deduplicated_existing_product")
            and str(job.get("title_seed") or "").strip()
        ):
            job["mode"] = "keyword_miner"
            job["seed_keyword"] = str(job["title_seed"])[:120]
            job["reverse_skipped_reason"] = "batch_reverse_returned_zero_results"
            updated += 1
    return updated


def _seerfar_seed_keyword(action: Dict[str, Any]) -> str:
    """Use the live Ozon title as the miner seed; never invent a keyword."""
    title = re.sub(r"\s+", " ", str(action.get("current_title") or "")).strip()
    if not title:
        raise HTTPException(status_code=422, detail="该商品没有可用于 Seerfar 挖掘的线上标题")
    return title[:120]


def _seerfar_safe_keyword_rows(
    rows: List[Dict[str, Any]],
    seed_keyword: str,
    known_facts: str = "",
) -> List[Dict[str, Any]]:
    """Keep Seerfar demand terms, but never let a different SKU fact enter a card."""
    seed = str(seed_keyword or "").casefold()
    volume_pattern = re.compile(r"(?<!\d)(\d+(?:[.,]\s*\d+)?)\s*(?:л\b|литр\w*|ml\b|мл\b)", re.IGNORECASE)
    quantity_pattern = re.compile(r"(?<!\d)(\d+)\s*(?:шт\b|штук\w*|предмет\w*|pcs?\b)", re.IGNORECASE)
    dimensional_mode_pattern = re.compile(r"(?<![a-zа-яё0-9])([0-9]+)\s*[dд](?![a-zа-яё])", re.IGNORECASE)
    ignored_terms = {
        "для", "без", "или", "под", "над", "при", "and", "the", "with",
        "беспроводной", "цифровой", "черный", "белый", "серый", "новый",
    }
    factual_property_terms = {
        "плас", "стек", "мета", "дере", "керам", "сили", "кож", "ткан", "бамб",
        "крас", "син", "зеле", "желт", "бел", "черн", "сер", "роз", "золот", "сереб", "прозр",
    }

    def core_terms(value: str) -> List[str]:
        return [
            token[:4]
            for token in re.findall(r"[a-zа-яё]{3,}", value.casefold())
            if token not in ignored_terms
        ]

    def normalized_values(value: str, pattern: re.Pattern[str], *, volume: bool = False) -> set[str]:
        values: set[str] = set()
        for match in pattern.finditer(value):
            raw = match.group(1).replace(" ", "").replace(",", ".")
            try:
                number = float(raw)
            except ValueError:
                continue
            unit = match.group(0).casefold()
            if volume and ("л" in unit and "мл" not in unit and "ml" not in unit):
                number *= 1000
            values.add(str(int(round(number))))
        return values

    facts = f"{seed} {str(known_facts or '').casefold()}"
    seed_volumes = normalized_values(facts, volume_pattern, volume=True)
    seed_quantities = normalized_values(facts, quantity_pattern)
    seed_modes = set(dimensional_mode_pattern.findall(facts))
    seed_core_terms = core_terms(seed)
    seed_core_set = set(seed_core_terms)
    known_fact_terms = set(core_terms(facts))
    primary_term = seed_core_terms[0] if seed_core_terms else ""
    safe_rows: List[Dict[str, Any]] = []
    for row in rows:
        query = str(row.get("query") or "").strip()
        if not query or re.search(r"[\u3400-\u9fff\uf900-\ufaff]", query):
            continue
        query_normalized = query.casefold()
        query_volumes = normalized_values(query_normalized, volume_pattern, volume=True)
        query_quantities = normalized_values(query_normalized, quantity_pattern)
        query_modes = set(dimensional_mode_pattern.findall(query_normalized))
        query_core_set = set(core_terms(query_normalized))
        if query_volumes and (not seed_volumes or not query_volumes.issubset(seed_volumes)):
            continue
        if query_quantities and (not seed_quantities or not query_quantities.issubset(seed_quantities)):
            continue
        if query_modes and (not seed_modes or not query_modes.issubset(seed_modes)):
            continue
        if (query_core_set & factual_property_terms) - known_fact_terms:
            continue
        overlap_count = len(seed_core_set & query_core_set)
        if seed_core_set and query_core_set and overlap_count < 2 and primary_term not in query_core_set:
            continue
        safe_rows.append(row)
    return safe_rows


def _seerfar_ozon_sku(action: Dict[str, Any]) -> str:
    """Only use an already downloaded numeric Ozon identifier for reverse lookup."""
    # Seerfar's reverse page expects Ozon SKU, not the seller's product_id.
    candidates = [action.get("sku"), action.get("product_id"), *(action.get("offer_ids") or [])]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if re.fullmatch(r"\d{6,}", text):
            return text
    return ""


def _rebuild_search_visibility_with_seerfar(
    plan: Dict[str, Any],
    *,
    product_id: str,
    rows: List[Dict[str, Any]],
    mode: str,
    imported_at: str,
    report_path: str,
) -> Dict[str, Any]:
    source_items: List[Dict[str, Any]] = []
    for action in plan.get("actions") or []:
        if not isinstance(action, dict):
            continue
        source_item = _search_visibility_action_to_source_item(action)
        if str(action.get("product_id") or "") == product_id:
            source_item["seerfar_keyword_reverse" if mode == "keyword_reverse" else "seerfar_keyword_mining"] = {"items": rows}
        source_items.append(source_item)
    rebuilt = _search_visibility_preserve_action_state(build_search_visibility_plan({
        "shop_id": str(plan.get("shop_id") or "unknown"),
        "period_days": int(plan.get("period_days") or 15),
        "items": source_items,
    }), plan)
    for action in rebuilt.get("actions") or []:
        if str(action.get("product_id") or "") == product_id:
            action["last_seerfar_keyword_import"] = {
                "status": "imported",
                "imported_at": imported_at,
                "imported_count": len(rows),
                "period_days": 30,
                "report_path": report_path,
                "source_label": "Seerfar竞品反查" if mode == "keyword_reverse" else "Seerfar关键词挖掘",
                "mode": mode,
            }
    for key in (
        "available", "mode", "date_from", "date_to", "order_period_days",
        "order_date_from", "order_date_to", "source_status",
    ):
        if key in plan:
            rebuilt[key] = copy_module.deepcopy(plan[key])
    details = dict(((rebuilt.get("source_status") or {}).get("details") or {}))
    if mode == "keyword_reverse":
        details.update({
            "seerfar_keyword_reverse_query_count": len(rows),
            "last_seerfar_keyword_reverse_imported_at": imported_at,
        })
    else:
        details.update({
            "seerfar_keyword_mining_query_count": len(rows),
            "last_seerfar_keyword_mining_imported_at": imported_at,
        })
    rebuilt.setdefault("source_status", {}).update({"details": details})
    rebuilt["available"] = True
    source_label = "Seerfar竞品反查词" if mode == "keyword_reverse" else "Seerfar月搜热度词"
    rebuilt["notice"] = f"已导入 {len(rows)} 个 {source_label}；只作为标签和简介依据，没有提交Ozon更新，没有调用库存接口。"
    rebuilt["write_api_calls"] = 0
    rebuilt["inventory_api_calls"] = 0
    rebuilt.setdefault("safety", {}).update({"write_api_calls": 0, "inventory_api_calls": 0})
    rebuilt["last_seerfar_keyword_import"] = {
        "product_id": product_id,
        "imported_count": len(rows),
        "period_days": 30,
        "imported_at": imported_at,
        "report_path": report_path,
    }
    return rebuilt


def _safe_project_relative(path: Path) -> str:
    try:
        return project_relative(path)
    except ValueError:
        return str(path)


@app.post("/api/workbench/market-intelligence/search-visibility/yandex-wordstat/import")
async def workbench_search_visibility_yandex_import(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Yandex 词表导入参数必须是JSON对象")
    if not MARKET_SEARCH_VISIBILITY_PLAN_PATH.is_file():
        raise HTTPException(status_code=409, detail="请先更新 Ozon 商品信息，再导入 Yandex 词")
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=422, detail="缺少 Ozon 商品ID")
    period_days = max(1, min(365, int(payload.get("period_days") or 30)))
    rows = parse_yandex_wordstat_text(str(payload.get("text") or ""), period_days=period_days)
    structured_rows = (
        payload.get("rows")
        or payload.get("items")
        or payload.get("wordstat")
        or payload.get("yandex_wordstat")
    )
    if structured_rows:
        rows = normalize_yandex_wordstat_rows(rows, structured_rows)
    if not rows:
        raise HTTPException(status_code=422, detail="没有识别到 Yandex 搜索词和搜索量")

    plan = load_optional_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH)
    plan_shop_id = str(plan.get("shop_id") or "").strip()
    request_shop_id = str(payload.get("store_id") or payload.get("shop_id") or plan_shop_id).strip()
    if plan_shop_id and request_shop_id and request_shop_id != plan_shop_id:
        raise HTTPException(status_code=409, detail="当前 Yandex 词表不属于所选店铺，请先切回对应店铺")
    original_action = _search_visibility_action(plan, product_id)
    import_path = _search_visibility_yandex_import_report_path(product_id, plan_shop_id or request_shop_id or "shop")
    imported_at = now_iso()
    import_record = {
        "schema_version": "1.0.0",
        "source": "yandex_wordstat_manual_import",
        "shop_id": plan_shop_id or request_shop_id,
        "product_id": product_id,
        "period_days": period_days,
        "imported_at": imported_at,
        "raw_text": str(payload.get("text") or ""),
        "rows": rows,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }
    atomic_write_json(import_path, import_record)
    import_path_text = _safe_project_relative(import_path)

    source_items = []
    last_uploads: Dict[str, Any] = {}
    for action in plan.get("actions") or []:
        if not isinstance(action, dict):
            continue
        action_product_id = str(action.get("product_id") or "")
        if action.get("last_upload"):
            last_uploads[action_product_id] = copy_module.deepcopy(action.get("last_upload"))
        source_item = _search_visibility_action_to_source_item(action)
        if action_product_id == product_id:
            source_item["yandex_wordstat"] = {"items": rows}
        source_items.append(source_item)

    rebuilt = _search_visibility_preserve_action_state(build_search_visibility_plan({
        "shop_id": plan_shop_id or request_shop_id or "unknown",
        "period_days": int(plan.get("period_days") or 15),
        "items": source_items,
    }), plan)
    for action in rebuilt.get("actions") or []:
        action_product_id = str(action.get("product_id") or "")
        if action_product_id in last_uploads:
            action["last_upload"] = last_uploads[action_product_id]
        if action_product_id == product_id:
            action["last_yandex_wordstat_import"] = {
                "status": "imported",
                "imported_at": imported_at,
                "imported_count": len(rows),
                "period_days": period_days,
                "report_path": import_path_text,
            }

    preserved_keys = (
        "available", "mode", "date_from", "date_to", "order_period_days",
        "order_date_from", "order_date_to", "source_status",
    )
    for key in preserved_keys:
        if key in plan:
            rebuilt[key] = plan[key]
    details = dict(((rebuilt.get("source_status") or {}).get("details") or {}))
    details.update({
        "yandex_wordstat_imported_query_count": len(rows),
        "last_yandex_wordstat_imported_at": imported_at,
    })
    rebuilt.setdefault("source_status", {}).update({"details": details})
    rebuilt["available"] = True
    rebuilt["notice"] = (
        f"已给「{original_action.get('current_title') or product_id}」导入 {len(rows)} 个 Yandex 参考词；"
        "只作为主题标签依据，没有提交Ozon更新，没有调用库存接口。"
    )
    rebuilt["write_api_calls"] = 0
    rebuilt["inventory_api_calls"] = 0
    rebuilt.setdefault("safety", {}).update({"write_api_calls": 0, "inventory_api_calls": 0})
    rebuilt["last_yandex_wordstat_import"] = {
        "product_id": product_id,
        "imported_count": len(rows),
        "period_days": period_days,
        "imported_at": imported_at,
        "report_path": import_path_text,
    }
    _write_search_visibility_plan(rebuilt)
    return {
        **rebuilt,
        "imported_count": len(rows),
        "import_path": import_path_text,
    }


@app.post("/api/workbench/market-intelligence/search-visibility/seerfar/queue")
async def workbench_search_visibility_seerfar_queue(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Seerfar 任务参数必须是JSON对象")
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=422, detail="缺少 Ozon 商品ID")
    request_shop_id = str(payload.get("store_id") or payload.get("shop_id") or "").strip()
    plan = _load_search_visibility_plan(request_shop_id)
    if not plan:
        raise HTTPException(status_code=409, detail="请先更新这个店铺的 Ozon 商品信息，再读取 Seerfar 关键词")
    plan_shop_id = str(plan.get("shop_id") or "").strip()
    request_shop_id = request_shop_id or plan_shop_id
    if plan_shop_id and request_shop_id and request_shop_id != plan_shop_id:
        raise HTTPException(status_code=409, detail="当前 Seerfar 任务不属于所选店铺，请先切回对应店铺")
    action = _search_visibility_action(plan, product_id)
    # Seerfar reverse lookup proved unreliable for our own Ozon SKUs.  Use the
    # current listing title as the single, deterministic seed for every card.
    mode = "keyword_miner"
    seed_keyword = str(payload.get("seed_keyword") or "").strip() or _seerfar_seed_keyword(action)
    if len(seed_keyword) > 120:
        seed_keyword = seed_keyword[:120]
    with SEERFAR_KEYWORD_JOB_LOCK:
        queue = _seerfar_keyword_jobs()
        for job in queue["jobs"]:
            if str(job.get("product_id") or "") == product_id and str(job.get("status") or "") in {"queued", "running"}:
                return {
                    "status": "queued",
                    "job": job,
                    "notice": "该商品的 Seerfar 关键词任务已在队列中；保持已登录的 Seerfar 页面打开即可自动读取。",
                    "write_api_calls": 0,
                    "inventory_api_calls": 0,
                }
        job = {
            "job_id": f"seerfar-{int(time.time() * 1000)}-{threading.get_ident()}",
            "product_id": product_id,
            "shop_id": plan_shop_id or request_shop_id or "unknown",
            "seed_keyword": seed_keyword,
            "mode": mode,
            "status": "queued",
            "created_at": now_iso(),
        }
        queue["jobs"].append(job)
        _save_seerfar_keyword_jobs(queue)
    return {
        "status": "queued",
        "job": job,
        "notice": "已按商品线上标题加入 Seerfar 关键词挖掘队列；保持已登录的 Seerfar 页面打开即可自动读取。",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.get("/api/workbench/market-intelligence/search-visibility/seerfar/next")
def workbench_search_visibility_seerfar_next(session_state: str = "") -> Dict[str, Any]:
    """Return one queued browser job.  The extension never receives login data."""
    with SEERFAR_KEYWORD_JOB_LOCK:
        queue = _seerfar_keyword_jobs()
        now = datetime.now(timezone.utc)
        active: Optional[Dict[str, Any]] = None
        changed = False
        if _skip_unavailable_batch_reverse_jobs(queue):
            changed = True
        if session_state == "logged_in":
            for job in queue["jobs"]:
                if str(job.get("status") or "") == "login_required":
                    job["status"] = "queued"
                    job["resumed_after_login_at"] = now_iso()
                    changed = True
        for job in queue["jobs"]:
            if str(job.get("status") or "") != "running":
                continue
            claimed_at = str(job.get("claimed_at") or "")
            try:
                age_seconds = (now - datetime.fromisoformat(claimed_at)).total_seconds() if claimed_at else 10**9
            except ValueError:
                age_seconds = 10**9
            if age_seconds > 20 * 60:
                job["status"] = "queued"
                job.pop("claimed_at", None)
                changed = True
            else:
                active = job
                break
        if active is None:
            # A title-miner fallback belongs to the SKU query that just
            # returned empty. Run it before unrelated SKU jobs so each title
            # group reaches a usable Seerfar result in one contiguous pass.
            active = next((
                job for job in queue["jobs"]
                if str(job.get("status") or "") == "queued" and job.get("priority") == "revalidate"
            ), None)
            if active is None:
                active = next((
                    job for job in queue["jobs"]
                    if str(job.get("status") or "") == "queued" and job.get("fallback_from_job_id")
                ), None)
            if active is None:
                active = next((job for job in queue["jobs"] if str(job.get("status") or "") == "queued"), None)
            if active is not None:
                active["status"] = "running"
                active["claimed_at"] = now_iso()
                if session_state == "logged_in" and active.get("login_required_at"):
                    active["resumed_after_login_at"] = now_iso()
                changed = True
        if changed:
            _save_seerfar_keyword_jobs(queue)
    return {
        "job": active,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.get("/api/workbench/market-intelligence/search-visibility/seerfar/pending")
def workbench_search_visibility_seerfar_pending() -> Dict[str, Any]:
    """Let the extension wake one logged-in Seerfar page without claiming a job."""
    with SEERFAR_KEYWORD_JOB_LOCK:
        queue = _seerfar_keyword_jobs()
        pending = [
            job for job in queue["jobs"]
            if str(job.get("status") or "") in {"queued", "login_required"}
        ]
    return {
        "pending": bool(pending),
        "queued_count": sum(1 for job in pending if str(job.get("status") or "") == "queued"),
        "login_required_count": sum(1 for job in pending if str(job.get("status") or "") == "login_required"),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.post("/api/workbench/market-intelligence/search-visibility/seerfar/import")
async def workbench_search_visibility_seerfar_import(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Seerfar 词表导入参数必须是JSON对象")
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=422, detail="缺少 Ozon 商品ID")
    mode = "keyword_reverse" if str(payload.get("mode") or "") == "keyword_reverse" else "keyword_miner"
    raw_rows = payload.get("rows") or payload.get("items") or payload.get("keywords") or []
    if isinstance(raw_rows, list):
        raw_rows = [{**row, "source_mode": mode} for row in raw_rows if isinstance(row, dict)]
    job_id = str(payload.get("job_id") or "").strip()
    queued_job: Dict[str, Any] = {}
    if job_id:
        with SEERFAR_KEYWORD_JOB_LOCK:
            queue = _seerfar_keyword_jobs()
            queued_job = next((job for job in queue["jobs"] if str(job.get("job_id") or "") == job_id), {})
        if not queued_job or str(queued_job.get("status") or "") != "running":
            raise HTTPException(status_code=409, detail="Seerfar 任务已暂停或不再可导入")
    request_shop_id = str(payload.get("store_id") or payload.get("shop_id") or queued_job.get("shop_id") or "").strip()
    plan = _load_search_visibility_plan(request_shop_id)
    if not plan:
        raise HTTPException(status_code=409, detail="请先更新这个店铺的 Ozon 商品信息，再导入 Seerfar 词")
    plan_shop_id = str(plan.get("shop_id") or "").strip()
    request_shop_id = request_shop_id or plan_shop_id
    if plan_shop_id and request_shop_id and request_shop_id != plan_shop_id:
        raise HTTPException(status_code=409, detail="当前 Seerfar 词表不属于所选店铺，请先切回对应店铺")
    original_action = _search_visibility_action(plan, product_id)
    rows = _seerfar_safe_keyword_rows(
        normalize_seerfar_keyword_rows(raw_rows),
        str(payload.get("seed_keyword") or _seerfar_seed_keyword(original_action)),
        json.dumps(original_action.get("product_attributes") or [], ensure_ascii=False),
    )
    if not rows:
        raise HTTPException(status_code=422, detail="Seerfar 返回词与当前商品规格不一致，未导入")
    imported_at = now_iso()
    import_path = _search_visibility_seerfar_import_report_path(product_id, plan_shop_id or request_shop_id or "shop")
    import_record = {
        "schema_version": "1.0.0",
        "source": f"seerfar_browser_{mode}",
        "shop_id": plan_shop_id or request_shop_id,
        "product_id": product_id,
        "seed_keyword": str(payload.get("seed_keyword") or "").strip(),
        "mode": mode,
        "imported_at": imported_at,
        "rows": rows,
        "source_notice": "读取用户已登录浏览器中可见的 Seerfar 结果；未读取账号Cookie，未调用 Seerfar Open API。",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }
    atomic_write_json(import_path, import_record)
    import_path_text = _safe_project_relative(import_path)
    rebuilt = _rebuild_search_visibility_with_seerfar(
        plan,
        product_id=product_id,
        rows=rows,
        mode=mode,
        imported_at=imported_at,
        report_path=import_path_text,
    )
    _write_search_visibility_plan(rebuilt)
    replicated_count = 0
    replicas = queued_job.get("replicas") if isinstance(queued_job.get("replicas"), list) else []
    for replica in replicas:
        if not isinstance(replica, dict):
            continue
        replica_shop_id = str(replica.get("shop_id") or "").strip()
        replica_product_id = str(replica.get("product_id") or "").strip()
        if not replica_shop_id or not replica_product_id:
            continue
        if replica_shop_id == (plan_shop_id or request_shop_id) and replica_product_id == product_id:
            continue
        replica_plan = _load_search_visibility_plan(replica_shop_id)
        if not replica_plan:
            continue
        try:
            _search_visibility_action(replica_plan, replica_product_id)
        except HTTPException:
            continue
        replica_rebuilt = _rebuild_search_visibility_with_seerfar(
            replica_plan,
            product_id=replica_product_id,
            rows=rows,
            mode=mode,
            imported_at=imported_at,
            report_path=import_path_text,
        )
        _write_search_visibility_plan(replica_rebuilt)
        replicated_count += 1
    if job_id:
        with SEERFAR_KEYWORD_JOB_LOCK:
            queue = _seerfar_keyword_jobs()
            for job in queue["jobs"]:
                if str(job.get("job_id") or "") == job_id:
                    job.update({
                        "status": "completed",
                        "completed_at": imported_at,
                        "imported_count": len(rows),
                        "replicated_count": replicated_count,
                        "report_path": import_path_text,
                    })
                    _save_seerfar_keyword_jobs(queue)
                    break
    return {
        **rebuilt,
        "imported_count": len(rows),
        "import_path": import_path_text,
        "replicated_count": replicated_count,
        "notice": f"已给「{original_action.get('current_title') or product_id}」导入 {len(rows)} 个 Seerfar {'竞品反查词' if mode == 'keyword_reverse' else '月搜热度词'}；没有提交Ozon更新。",
    }


@app.post("/api/workbench/market-intelligence/search-visibility/seerfar/pipeline/import")
async def workbench_search_visibility_seerfar_pipeline_import(request: Request) -> Dict[str, Any]:
    """Save visible Seerfar miner rows for one new-product copy task.

    This intentionally does not touch the existing Ozon-product optimization
    plan.  New 1688 products consume the saved evidence during ecommerce
    design, before a listing exists in any store.
    """
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Seerfar 新品词表参数必须是JSON对象")
    product_id = str(payload.get("product_id") or "").strip()
    if not re.fullmatch(r"P\d{6}", product_id):
        raise HTTPException(status_code=422, detail="新品 Seerfar 任务缺少有效商品ID")
    product_dir = PRODUCTS_DIR / product_id
    if not (product_dir / "input" / "source.json").is_file():
        raise HTTPException(status_code=404, detail="新品商品资料不存在")
    raw_rows = payload.get("rows") or payload.get("items") or payload.get("keywords") or []
    if isinstance(raw_rows, list):
        raw_rows = [{**row, "source_mode": "keyword_miner"} for row in raw_rows if isinstance(row, dict)]
    rows = _seerfar_safe_keyword_rows(
        normalize_seerfar_keyword_rows(raw_rows),
        str(payload.get("seed_keyword") or ""),
        json.dumps(load_optional_json(product_dir / "input" / "source.json", {}), ensure_ascii=False),
    )
    if not rows:
        raise HTTPException(status_code=422, detail="没有识别到 Seerfar 关键词挖掘的月搜热度")
    imported_at = now_iso()
    artifact = {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "status": "completed",
        "job_id": str(payload.get("job_id") or "").strip() or None,
        "seed_keyword": str(payload.get("seed_keyword") or "").strip(),
        "mode": "keyword_miner",
        "rows": rows[:50],
        "source": "seerfar_browser_keyword_miner",
        "source_notice": "读取用户已登录浏览器中可见的 Seerfar 关键词挖掘结果；未读取账号Cookie，未调用 Seerfar Open API。月搜热度不是 Ozon 搜索人数。",
        "imported_at": imported_at,
    }
    artifact_path = product_dir / "output" / "seerfar-keyword-research.json"
    atomic_write_json(artifact_path, artifact)
    job_id = str(payload.get("job_id") or "").strip()
    if job_id:
        with SEERFAR_KEYWORD_JOB_LOCK:
            queue = _seerfar_keyword_jobs()
            for job in queue["jobs"]:
                if str(job.get("job_id") or "") == job_id:
                    job.update({
                        "status": "completed",
                        "completed_at": imported_at,
                        "imported_count": len(rows),
                        "artifact_path": _safe_project_relative(artifact_path),
                    })
                    _save_seerfar_keyword_jobs(queue)
                    break
    return {
        "product_id": product_id,
        "status": "completed",
        "imported_count": len(rows),
        "artifact_path": _safe_project_relative(artifact_path),
        "notice": "已保存新品 Seerfar 关键词依据；将用于本商品标题、主题标签和简介设计。",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.post("/api/workbench/market-intelligence/search-visibility/seerfar/fail")
async def workbench_search_visibility_seerfar_fail(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    job_id = str((payload or {}).get("job_id") or "").strip() if isinstance(payload, dict) else ""
    if not job_id:
        raise HTTPException(status_code=422, detail="缺少 Seerfar 任务ID")
    with SEERFAR_KEYWORD_JOB_LOCK:
        queue = _seerfar_keyword_jobs()
        job = next((item for item in queue["jobs"] if str(item.get("job_id") or "") == job_id), None)
        if job is None:
            raise HTTPException(status_code=404, detail="没有找到 Seerfar 任务")
        error = str(payload.get("error") or "Seerfar 页面未返回关键词结果")[:300]
        is_login_required = error.startswith("SEERFAR_LOGIN_REQUIRED")
        is_reverse_empty = (
            str(job.get("mode") or "") == "keyword_reverse"
            and error.startswith("SEERFAR_REVERSE_EMPTY")
        )
        fallback_job: Optional[Dict[str, Any]] = None
        if is_reverse_empty:
            plan = _load_search_visibility_plan(str(job.get("shop_id") or ""))
            try:
                action = _search_visibility_action(plan, str(job.get("product_id") or ""))
                fallback_seed = _seerfar_seed_keyword(action)
            except HTTPException:
                fallback_seed = ""
            if fallback_seed:
                fallback_job = {
                    "job_id": f"seerfar-{int(time.time() * 1000)}-{threading.get_ident()}",
                    "product_id": str(job.get("product_id") or ""),
                    "shop_id": str(job.get("shop_id") or "unknown"),
                    "seed_keyword": fallback_seed,
                    "mode": "keyword_miner",
                    "status": "queued",
                    "created_at": now_iso(),
                    "fallback_from_job_id": job_id,
                    "fallback_reason": "reverse_sku_not_found",
                    "kind": job.get("kind") or "existing_product_keyword_miner_fallback",
                    "dedupe_key": job.get("dedupe_key") or "",
                    "replicas": copy_module.deepcopy(job.get("replicas") or []),
                }
                queue["jobs"].append(fallback_job)
                job.update({
                    "status": "completed_without_reverse_result",
                    "completed_at": now_iso(),
                    "error": error,
                    "fallback_job_id": fallback_job["job_id"],
                })
        if is_login_required:
            job.update({
                "status": "login_required",
                "login_required_at": now_iso(),
                "error": error,
                "notice": "Seerfar 登录已失效，请在 Chrome 的 Seerfar 页面重新登录；登录后自动继续。",
            })
            product_id = str(job.get("product_id") or "")
            if re.fullmatch(r"P\d{6}", product_id):
                product_dir = PRODUCTS_DIR / product_id
                if (product_dir / "input" / "source.json").is_file():
                    atomic_write_json(product_dir / "output" / "seerfar-keyword-research.json", {
                        "schema_version": "1.0.0",
                        "product_id": product_id,
                        "status": "login_required",
                        "job_id": job_id,
                        "seed_keyword": str(job.get("seed_keyword") or ""),
                        "mode": str(job.get("mode") or "keyword_miner"),
                        "rows": [],
                        "reason": "Seerfar 登录已失效，请在 Chrome 的 Seerfar 页面重新登录；登录后自动继续关键词查询。",
                        "updated_at": now_iso(),
                    })
        elif fallback_job is None:
            job.update({
                "status": "failed",
                "failed_at": now_iso(),
                "error": error,
            })
        _save_seerfar_keyword_jobs(queue)
    return {
        "status": "fallback_queued" if fallback_job else "login_required" if is_login_required else "failed",
        "fallback_job": fallback_job,
        "notice": (
            "该 SKU 在 Seerfar 没有反查结果，已自动改用商品线上标题做关键词挖掘。"
            if fallback_job else "Seerfar 登录已失效，请在 Chrome 重新登录；登录后自动继续关键词查询。"
            if is_login_required else "Seerfar 读取失败，未提交Ozon更新。"
        ),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.post("/api/workbench/market-intelligence/search-visibility/ozon-product-query/import")
async def workbench_search_visibility_ozon_product_query_import(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Ozon 商品查询导入参数必须是JSON对象")
    if not MARKET_SEARCH_VISIBILITY_PLAN_PATH.is_file():
        raise HTTPException(status_code=409, detail="请先更新 Ozon 商品信息，再导入竞品查询词")
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=422, detail="缺少 Ozon 商品ID")

    plan = load_optional_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH)
    period_days = max(1, min(365, int(payload.get("period_days") or plan.get("period_days") or 15)))
    rows = parse_ozon_product_query_text(
        str(payload.get("text") or ""),
        period_days=period_days,
        source_kind="competitor_product_query",
        source_label="竞品商品查询",
    )
    if not rows:
        raise HTTPException(status_code=422, detail="没有识别到 Ozon 商品查询词")

    plan_shop_id = str(plan.get("shop_id") or "").strip()
    request_shop_id = str(payload.get("store_id") or payload.get("shop_id") or plan_shop_id).strip()
    if plan_shop_id and request_shop_id and request_shop_id != plan_shop_id:
        raise HTTPException(status_code=409, detail="当前竞品查询词不属于所选店铺，请先切回对应店铺")
    original_action = _search_visibility_action(plan, product_id)
    import_path = _search_visibility_ozon_product_query_import_report_path(product_id, plan_shop_id or request_shop_id or "shop")
    imported_at = now_iso()
    import_record = {
        "schema_version": "1.0.0",
        "source": "ozon_competitor_product_query_manual_import",
        "shop_id": plan_shop_id or request_shop_id,
        "product_id": product_id,
        "period_days": period_days,
        "imported_at": imported_at,
        "raw_text": str(payload.get("text") or ""),
        "rows": rows,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }
    atomic_write_json(import_path, import_record)
    import_path_text = _safe_project_relative(import_path)

    source_items = []
    last_uploads: Dict[str, Any] = {}
    last_yandex_imports: Dict[str, Any] = {}
    for action in plan.get("actions") or []:
        if not isinstance(action, dict):
            continue
        action_product_id = str(action.get("product_id") or "")
        if action.get("last_upload"):
            last_uploads[action_product_id] = copy_module.deepcopy(action.get("last_upload"))
        if action.get("last_yandex_wordstat_import"):
            last_yandex_imports[action_product_id] = copy_module.deepcopy(action.get("last_yandex_wordstat_import"))
        source_item = _search_visibility_action_to_source_item(action)
        if action_product_id == product_id:
            existing_trial_terms = (
                ((action.get("evidence") or {}).get("top_trial_terms") if isinstance(action.get("evidence"), dict) else [])
                or []
            )
            source_item["trial_reference_terms"] = [*existing_trial_terms, *rows]
        source_items.append(source_item)

    rebuilt = _search_visibility_preserve_action_state(build_search_visibility_plan({
        "shop_id": plan_shop_id or request_shop_id or "unknown",
        "period_days": int(plan.get("period_days") or 15),
        "items": source_items,
    }), plan)
    for action in rebuilt.get("actions") or []:
        action_product_id = str(action.get("product_id") or "")
        if action_product_id in last_uploads:
            action["last_upload"] = last_uploads[action_product_id]
        if action_product_id in last_yandex_imports:
            action["last_yandex_wordstat_import"] = last_yandex_imports[action_product_id]
        if action_product_id == product_id:
            action["last_ozon_product_query_import"] = {
                "status": "imported",
                "imported_at": imported_at,
                "imported_count": len(rows),
                "period_days": period_days,
                "report_path": import_path_text,
            }

    preserved_keys = (
        "available", "mode", "date_from", "date_to", "order_period_days",
        "order_date_from", "order_date_to", "source_status",
    )
    for key in preserved_keys:
        if key in plan:
            rebuilt[key] = plan[key]
    details = dict(((rebuilt.get("source_status") or {}).get("details") or {}))
    details.update({
        "ozon_product_query_imported_query_count": len(rows),
        "last_ozon_product_query_imported_at": imported_at,
    })
    rebuilt.setdefault("source_status", {}).update({"details": details})
    rebuilt["available"] = True
    rebuilt["notice"] = (
        f"已给「{original_action.get('current_title') or product_id}」导入 {len(rows)} 个 Ozon 竞品查询词；"
        "带人数的词会显示搜索人数；没有提交Ozon更新，没有调用库存接口。"
    )
    rebuilt["write_api_calls"] = 0
    rebuilt["inventory_api_calls"] = 0
    rebuilt.setdefault("safety", {}).update({"write_api_calls": 0, "inventory_api_calls": 0})
    rebuilt["last_ozon_product_query_import"] = {
        "product_id": product_id,
        "imported_count": len(rows),
        "period_days": period_days,
        "imported_at": imported_at,
        "report_path": import_path_text,
    }
    _write_search_visibility_plan(rebuilt)
    return {
        **rebuilt,
        "imported_count": len(rows),
        "import_path": import_path_text,
    }


def _search_visibility_action(plan: Dict[str, Any], product_id: str) -> Dict[str, Any]:
    for item in plan.get("actions") or []:
        if isinstance(item, dict) and str(item.get("product_id") or "") == product_id:
            return item
    raise HTTPException(status_code=404, detail="没有找到这个 Ozon 商品的搜索词建议")


def _search_visibility_compact_policy_text(value: Any) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", str(value or "").casefold())


def _search_visibility_has_blocked_fragment(value: Any, fragments: Iterable[str]) -> bool:
    compact = _search_visibility_compact_policy_text(value)
    spaced = f" {re.sub(r'\\s+', ' ', str(value or '').casefold()).strip()} "
    for fragment in fragments:
        text = str(fragment or "").casefold().strip()
        compact_fragment = _search_visibility_compact_policy_text(text)
        if compact_fragment and compact_fragment in compact:
            return True
        if " " in text and f" {text} " in spaced:
            return True
    return False


def _search_visibility_tag_policy_ok(value: Any) -> bool:
    text = str(value or "").strip().lstrip("#")
    if not 0 < len(text) <= OZON_SUBJECT_TAG_MAX_BODY_LENGTH:
        return False
    return not _search_visibility_has_blocked_fragment(text, SEARCH_VISIBILITY_BLOCKED_TAG_FRAGMENTS)


def _search_visibility_intro_policy_ok(value: Any) -> bool:
    text = str(value or "")
    if not text or not re.search(r"[А-Яа-яЁё]", text):
        return False
    if re.search(r"</?[a-z][^>]*>", text, flags=re.IGNORECASE):
        return False
    if re.search(r"[*_]{2,}", text):
        return False
    return not (
        _search_visibility_has_blocked_fragment(text, SEARCH_VISIBILITY_INTRO_RISK_FRAGMENTS)
        or _search_visibility_has_blocked_fragment(text, SEARCH_VISIBILITY_BLOCKED_TAG_FRAGMENTS)
    )


def _search_visibility_tag_key(value: Any) -> str:
    text = str(value or "").strip().lstrip("#")
    if not text:
        return ""
    return re.sub(r"[\s_#-]+", "", text).casefold()


def _search_visibility_split_tag_text(value: Any) -> List[str]:
    return [part.strip() for part in re.split(r"[\s,;，；]+", str(value or "")) if part.strip()]


def _search_visibility_actual_subject_tags(action: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    existing = action.get("existing_subject_tags") if isinstance(action.get("existing_subject_tags"), list) else []
    for value in existing:
        tags.extend(_search_visibility_split_tag_text(value))
    check = action.get("last_upload_status_check") if isinstance(action.get("last_upload_status_check"), dict) else {}
    checked_values = check.get("subject_tag_values") if isinstance(check.get("subject_tag_values"), list) else []
    if checked_values:
        for value in checked_values:
            tags.extend(_search_visibility_split_tag_text(value))
    elif check.get("has_subject_tags"):
        tags.extend(_search_visibility_split_tag_text(check.get("subject_tag_sample")))
    return tags


def _search_visibility_action_has_suggested_subject_tags(action: Dict[str, Any]) -> bool:
    actual_keys = {_search_visibility_tag_key(tag) for tag in _search_visibility_actual_subject_tags(action)}
    actual_keys.discard("")
    proposed = action.get("subject_tags") if isinstance(action.get("subject_tags"), list) else []
    proposed_keys = [_search_visibility_tag_key(tag) for tag in proposed if _search_visibility_tag_key(tag)]
    return bool(proposed_keys and all(key in actual_keys for key in proposed_keys))


def _merged_subject_tags(action: Dict[str, Any], limit: int = 30, *, include_last_upload: bool = False) -> Tuple[List[str], int]:
    def tag_length_ok(value: Any) -> bool:
        return 0 < len(str(value or "").strip().lstrip("#")) <= OZON_SUBJECT_TAG_MAX_BODY_LENGTH

    def tag_key(value: Any) -> str:
        canonical = canonical_hashtag(value)
        if canonical:
            return canonical.casefold()
        return str(value or "").strip().casefold()

    def upload_tag(value: Any, *, preserve_existing: bool = False) -> Optional[str]:
        canonical = canonical_hashtag(value)
        if canonical:
            return canonical
        text = str(value or "").strip()
        if preserve_existing and re.fullmatch(r"#?[А-Яа-яЁё]+", text):
            return "#" + text.lstrip("#").casefold()
        return None

    existing = _search_visibility_actual_subject_tags(action)
    proposed = action.get("subject_tags") if isinstance(action.get("subject_tags"), list) else []
    last_upload = action.get("last_upload") if isinstance(action.get("last_upload"), dict) else {}
    uploaded = last_upload.get("applied_subject_tags") if isinstance(last_upload.get("applied_subject_tags"), list) else []
    remove_values = action.get("subject_tags_to_remove") if isinstance(action.get("subject_tags_to_remove"), list) else []
    remove_keys = {tag_key(value) for value in remove_values if tag_key(value)}
    strategy = str(action.get("subject_tag_strategy") or "")
    tags: List[str] = []
    seen: set[str] = set()
    new_count = 0
    existing_keys = {tag_key(tag) for tag in existing if tag_key(tag)}
    if strategy == "replace_low_search":
        values = [
            *proposed,
            *(uploaded if include_last_upload else []),
            *[
                tag
                for tag in existing
                if tag_key(tag) not in remove_keys
            ],
        ]
    else:
        values = [*existing, *proposed, *uploaded] if include_last_upload else [*existing, *proposed]
    for value in values:
        tag = upload_tag(value, preserve_existing=value in existing)
        if not tag or not tag_length_ok(tag) or not _search_visibility_tag_policy_ok(tag):
            continue
        key = tag_key(tag)
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if key not in existing_keys:
            new_count += 1
        if len(tags) >= limit:
            break
    return tags, new_count


def _search_visibility_upload_report_path(product_id: str, shop_id: str) -> Path:
    safe_product_id = re.sub(r"[^0-9A-Za-z_-]+", "-", product_id).strip("-") or "unknown"
    safe_shop_id = re.sub(r"[^0-9A-Za-z_-]+", "-", shop_id).strip("-") or "shop"
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    return MARKET_SEARCH_VISIBILITY_UPLOAD_DIR / f"{stamp}-{safe_shop_id}-{safe_product_id}.json"


def _search_visibility_batch_upload_report_path(shop_id: str) -> Path:
    safe_shop_id = re.sub(r"[^0-9A-Za-z_-]+", "-", shop_id).strip("-") or "shop"
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    return MARKET_SEARCH_VISIBILITY_UPLOAD_DIR / f"{stamp}-{safe_shop_id}-batch.json"


def _search_visibility_chunks(values: Sequence[Any], size: int) -> Iterable[List[Any]]:
    for index in range(0, len(values), size):
        yield list(values[index:index + size])


def _search_visibility_intro_value(action: Dict[str, Any]) -> str:
    if "intro" not in (action.get("allowed_changes") or []):
        return ""
    if action.get("intro_update_available") is False:
        return ""
    raw_text = str(action.get("recommended_intro") or "").strip()
    text = re.sub(r"[ \t\r\f\v]+", " ", raw_text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text or not re.search(r"[А-Яа-яЁё]", text):
        return ""
    if not _search_visibility_intro_policy_ok(text):
        return ""
    current_intro = re.sub(r"\s+", " ", str(action.get("current_intro") or "").strip())
    if current_intro and re.sub(r"\s+", " ", text) == current_intro:
        return ""
    if len(text) <= 1000:
        return text
    return text[:1000].rsplit(" ", 1)[0].rstrip(" ,.;")


def _search_visibility_upload_item(
    action: Dict[str, Any],
    *,
    include_intro: bool = True,
    force_reupload: bool = False,
) -> Tuple[Dict[str, Any], int, int, bool]:
    product_id = str(action.get("product_id") or "").strip()
    if not product_id.isdigit():
        raise ValueError("Ozon 商品ID必须是数字")
    offer_ids = [str(value).strip() for value in action.get("offer_ids") or [] if str(value or "").strip()]
    offer_id = offer_ids[0] if offer_ids else str(action.get("offer_id") or "").strip()
    if not offer_id:
        raise ValueError("缺少 Ozon offer_id，不能上传")
    allowed_changes = action.get("allowed_changes") or []
    if "subject_tags" not in allowed_changes and "intro" not in allowed_changes:
        raise ValueError("没有可上传的主题标签或简介")
    tags: List[str] = []
    new_count = 0
    if "subject_tags" in allowed_changes:
        tags, new_count = _merged_subject_tags(action, include_last_upload=force_reupload)
    intro_value = _search_visibility_intro_value(action) if include_intro else ""
    attributes = []
    if tags and (new_count > 0 or force_reupload):
        attributes.append({
            "id": OZON_HASHTAG_ATTRIBUTE_ID,
            "values": [{"value": " ".join(tags)}],
        })
    if intro_value:
        attributes.append({
            "id": OZON_ANNOTATION_ATTRIBUTE_ID,
            "values": [{"value": intro_value}],
        })
    if not attributes:
        raise ValueError("没有新的主题标签或简介需要上传")
    return {
        "product_id": int(product_id),
        "offer_id": offer_id,
        "attributes": attributes,
    }, len(tags), new_count, bool(intro_value)


def _search_visibility_subject_tags_from_upload_item(item: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    for attribute in item.get("attributes") or []:
        if not isinstance(attribute, dict):
            continue
        attribute_id = str(attribute.get("id") or attribute.get("attribute_id") or "")
        if attribute_id != str(OZON_HASHTAG_ATTRIBUTE_ID):
            continue
        for value in attribute.get("values") or []:
            text = value.get("value") if isinstance(value, dict) else value
            for token in str(text or "").split():
                cleaned = token.strip()
                if cleaned.startswith("#") and cleaned not in tags:
                    tags.append(cleaned)
    return tags[:30]


def _ozon_task_id_from_response(response: Any) -> Optional[int]:
    if isinstance(response, dict):
        value = response.get("task_id")
        if value in (None, "", "unknown"):
            result = response.get("result") if isinstance(response.get("result"), dict) else {}
            value = result.get("task_id")
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None
    return None


def _search_visibility_last_upload_receipt(action: Dict[str, Any]) -> Dict[str, Any]:
    last_upload = action.get("last_upload") if isinstance(action.get("last_upload"), dict) else {}
    report_path = str(last_upload.get("report_path") or "").strip()
    if not report_path:
        return {}
    path = ROOT / report_path
    if not path.is_file():
        return {}
    receipt = load_optional_json(path)
    return receipt if isinstance(receipt, dict) else {}


def _search_visibility_action_offer_id(action: Dict[str, Any], receipt: Optional[Dict[str, Any]] = None) -> str:
    for item in (receipt or {}).get("request") or []:
        if isinstance(item, dict) and str(item.get("offer_id") or "").strip():
            return str(item.get("offer_id")).strip()
    offer_ids = [str(value).strip() for value in action.get("offer_ids") or [] if str(value or "").strip()]
    if offer_ids:
        return offer_ids[0]
    return str(action.get("offer_id") or "").strip()


def _search_visibility_task_id(action: Dict[str, Any], receipt: Optional[Dict[str, Any]] = None) -> Optional[int]:
    last_upload = action.get("last_upload") if isinstance(action.get("last_upload"), dict) else {}
    for source in (last_upload, receipt or {}):
        value = source.get("task_id") if isinstance(source, dict) else None
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = 0
        if number > 0:
            return number
    if isinstance(receipt, dict):
        task_id = _ozon_task_id_from_response(receipt.get("response"))
        if task_id:
            return task_id
        responses = receipt.get("response")
        if isinstance(responses, list):
            for response in responses:
                task_id = _ozon_task_id_from_response(response)
                if task_id:
                    return task_id
    return None


def _search_visibility_remote_attribute_values(item: Dict[str, Any], attribute_id: int) -> List[str]:
    values: List[str] = []
    for attribute in item.get("attributes") or []:
        if not isinstance(attribute, dict):
            continue
        current_id = str(attribute.get("id") or attribute.get("attribute_id") or "")
        if current_id != str(attribute_id):
            continue
        for value in attribute.get("values") or []:
            if isinstance(value, dict):
                text = value.get("value") or value.get("name") or value.get("text")
            else:
                text = value
            if str(text or "").strip():
                values.append(str(text).strip())
    return values


def _search_visibility_import_items(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = info.get("result") if isinstance(info.get("result"), dict) else {}
    return [dict(item) for item in result.get("items") or [] if isinstance(item, dict)]


def _ensure_search_visibility_upload_enabled() -> None:
    disabled = str(os.environ.get("JLC_DISABLE_SEARCH_VISIBILITY_UPLOADS") or "").strip().casefold()
    if disabled in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=423,
            detail="搜索词上传已暂停，当前只保留商品、搜索词和出单数据读取。",
        )


@app.post("/api/workbench/market-intelligence/search-visibility/apply")
async def workbench_search_visibility_apply(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="搜索词上传参数必须是JSON对象")
    _ensure_search_visibility_upload_enabled()
    if not MARKET_SEARCH_VISIBILITY_PLAN_PATH.is_file():
        raise HTTPException(status_code=409, detail="请先更新搜索词，再上传优化")
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=422, detail="缺少 Ozon 商品ID")
    if not product_id.isdigit():
        raise HTTPException(status_code=422, detail="Ozon 商品ID必须是数字")
    plan = load_optional_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH)
    plan_shop_id = str(plan.get("shop_id") or "").strip()
    request_shop_id = str(payload.get("store_id") or payload.get("shop_id") or plan_shop_id).strip()
    shop, secrets = _default_search_shop(request_shop_id)
    shop_id = str(shop["id"])
    if plan_shop_id and shop_id != plan_shop_id:
        raise HTTPException(status_code=409, detail="当前搜索词结果不属于所选店铺，请先更新搜索词")
    action = _search_visibility_action(plan, product_id)
    try:
        upload_item, tag_count, new_count, intro_applied = _search_visibility_upload_item(action)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    request_items = [upload_item]
    applied_subject_tags = _search_visibility_subject_tags_from_upload_item(upload_item)
    client = OzonWriteClient(
        OzonConfig(
            client_id=secrets["client_id"],
            api_key=secrets["api_key"],
            shop_name=shop_id,
        ),
        allow_production_write=True,
    )
    try:
        response = client.update_product_attributes(request_items)
    except (OzonUploadApiError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Ozon 主题标签上传失败：{exc}") from exc

    applied_at = now_iso()
    task_id = _ozon_task_id_from_response(response)
    receipt = {
        "schema_version": "1.0.0",
        "status": "submitted",
        "shop_id": shop_id,
        "product_id": product_id,
        "endpoint": OzonWriteClient.PRODUCT_ATTRIBUTES_UPDATE_ENDPOINT,
        "task_id": task_id,
        "uploaded_changes": ["subject_tags", "intro"] if intro_applied else ["subject_tags"],
        "applied_subject_tags_count": tag_count,
        "applied_subject_tags": applied_subject_tags,
        "new_subject_tags_count": new_count,
        "intro_update_status": "applied" if intro_applied else "not_applied",
        "title_update_status": "not_applied",
        "title_update_reason": "title rewrite requires full product import and is kept as advice",
        "applied_at": applied_at,
        "request": request_items,
        "response": response,
        "write_api_calls": 1,
        "inventory_api_calls": 0,
    }
    report_path = _search_visibility_upload_report_path(product_id, shop_id)
    atomic_write_json(report_path, receipt)
    action["last_upload"] = {
        "status": "submitted",
        "task_id": task_id,
        "uploaded_changes": ["subject_tags", "intro"] if intro_applied else ["subject_tags"],
        "applied_subject_tags_count": tag_count,
        "applied_subject_tags": applied_subject_tags,
        "new_subject_tags_count": new_count,
        "intro_update_status": "applied" if intro_applied else "not_applied",
        "applied_at": applied_at,
        "report_path": project_relative(report_path),
    }
    plan["last_upload"] = action["last_upload"]
    _write_search_visibility_plan(plan)
    return {
        **receipt,
        "report_path": project_relative(report_path),
        "notice": f"已上传 {new_count} 个新主题标签和简介到 {shop_id}；没有改标题，没有改价格，没有调用库存接口。" if intro_applied else f"已上传 {new_count} 个新主题标签到 {shop_id}；没有改标题，没有改价格，没有调用库存接口。",
    }


@app.post("/api/workbench/market-intelligence/search-visibility/apply-batch")
async def workbench_search_visibility_apply_batch(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="批量搜索词上传参数必须是JSON对象")
    _ensure_search_visibility_upload_enabled()
    if payload.get("confirm_upload") is not True:
        raise HTTPException(status_code=422, detail="批量上传需要明确确认")
    if not MARKET_SEARCH_VISIBILITY_PLAN_PATH.is_file():
        raise HTTPException(status_code=409, detail="请先更新搜索词，再批量上传")
    plan = load_optional_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH)
    plan_shop_id = str(plan.get("shop_id") or "").strip()
    request_shop_id = str(payload.get("store_id") or payload.get("shop_id") or plan_shop_id).strip()
    shop, secrets = _default_search_shop(request_shop_id)
    shop_id = str(shop["id"])
    if plan_shop_id and shop_id != plan_shop_id:
        raise HTTPException(status_code=409, detail="当前搜索词结果不属于所选店铺，请先更新搜索词")

    requested_ids = {
        str(value).strip()
        for value in payload.get("product_ids") or []
        if str(value or "").strip()
    }
    include_uploaded = bool(payload.get("include_uploaded"))
    include_intro = payload.get("include_intro") is not False
    max_products = max(1, min(1000, int(payload.get("max_products") or 1000)))
    upload_products: List[Dict[str, Any]] = []
    request_items: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    action_by_product_id: Dict[str, Dict[str, Any]] = {}

    for action in plan.get("actions") or []:
        if not isinstance(action, dict):
            continue
        product_id = str(action.get("product_id") or "").strip()
        if requested_ids and product_id not in requested_ids:
            continue
        if (
            not include_uploaded
            and (action.get("last_upload") or {}).get("status") == "submitted"
            and _search_visibility_action_has_suggested_subject_tags(action)
        ):
            skipped.append({"product_id": product_id, "reason": "商品卡已包含建议标签"})
            continue
        if len(request_items) >= max_products:
            skipped.append({"product_id": product_id, "reason": "超过本次上限"})
            continue
        try:
            upload_item, tag_count, new_count, intro_applied = _search_visibility_upload_item(action, include_intro=include_intro)
        except ValueError as exc:
            skipped.append({"product_id": product_id, "reason": str(exc)})
            continue
        request_items.append(upload_item)
        applied_subject_tags = _search_visibility_subject_tags_from_upload_item(upload_item)
        upload_products.append({
            "product_id": product_id,
            "applied_subject_tags_count": tag_count,
            "applied_subject_tags": applied_subject_tags,
            "new_subject_tags_count": new_count,
            "intro_update_status": "applied" if intro_applied else "not_applied",
        })
        action_by_product_id[product_id] = action

    if not request_items:
        raise HTTPException(status_code=409, detail="当前店铺没有可批量上传的新主题标签")

    client = OzonWriteClient(
        OzonConfig(
            client_id=secrets["client_id"],
            api_key=secrets["api_key"],
            shop_name=shop_id,
        ),
        allow_production_write=True,
    )
    responses: List[Dict[str, Any]] = []
    write_api_calls = 0
    try:
        for group in _search_visibility_chunks(request_items, 100):
            responses.append(client.update_product_attributes(group))
            write_api_calls += 1
    except (OzonUploadApiError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Ozon 主题标签批量上传失败：{exc}") from exc

    applied_at = now_iso()
    total_new_tags = sum(int(item["new_subject_tags_count"]) for item in upload_products)
    total_tags = sum(int(item["applied_subject_tags_count"]) for item in upload_products)
    intro_update_count = sum(1 for item in upload_products if item.get("intro_update_status") == "applied")
    report_path = _search_visibility_batch_upload_report_path(shop_id)
    receipt = {
        "schema_version": "1.0.0",
        "status": "submitted",
        "shop_id": shop_id,
        "endpoint": OzonWriteClient.PRODUCT_ATTRIBUTES_UPDATE_ENDPOINT,
        "uploaded_changes": ["subject_tags", "intro"] if intro_update_count else ["subject_tags"],
        "uploaded_product_count": len(upload_products),
        "applied_subject_tags_count": total_tags,
        "new_subject_tags_count": total_new_tags,
        "intro_update_count": intro_update_count,
        "uploaded_products": upload_products,
        "skipped": skipped[:200],
        "title_update_status": "not_applied",
        "title_update_reason": "batch entry uploads subject tags and intro only",
        "applied_at": applied_at,
        "request": request_items,
        "response": responses,
        "write_api_calls": write_api_calls,
        "inventory_api_calls": 0,
    }
    atomic_write_json(report_path, receipt)
    relative_report_path = project_relative(report_path)
    for item in upload_products:
        product_id = str(item["product_id"])
        action = action_by_product_id.get(product_id)
        if not action:
            continue
        action["last_upload"] = {
            "status": "submitted",
            "uploaded_changes": ["subject_tags", "intro"] if item.get("intro_update_status") == "applied" else ["subject_tags"],
            "applied_subject_tags_count": item["applied_subject_tags_count"],
            "applied_subject_tags": item.get("applied_subject_tags") or [],
            "new_subject_tags_count": item["new_subject_tags_count"],
            "intro_update_status": item.get("intro_update_status"),
            "applied_at": applied_at,
            "report_path": relative_report_path,
        }
    plan["last_upload"] = {
        "status": "submitted",
        "uploaded_changes": ["subject_tags", "intro"] if intro_update_count else ["subject_tags"],
        "uploaded_product_count": len(upload_products),
        "applied_subject_tags_count": total_tags,
        "new_subject_tags_count": total_new_tags,
        "intro_update_count": intro_update_count,
        "applied_at": applied_at,
        "report_path": relative_report_path,
    }
    _write_search_visibility_plan(plan)
    return {
        **receipt,
        "report_path": relative_report_path,
        "notice": f"已给 {len(upload_products)} 个商品上传 {total_new_tags} 个新主题标签，并更新 {intro_update_count} 个简介；没有改标题，没有改价格，没有调用库存接口。",
    }


@app.post("/api/workbench/market-intelligence/search-visibility/upload-status")
async def workbench_search_visibility_upload_status(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="上传结果查询参数必须是JSON对象")
    if not MARKET_SEARCH_VISIBILITY_PLAN_PATH.is_file():
        raise HTTPException(status_code=409, detail="请先更新搜索词")
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=422, detail="缺少 Ozon 商品ID")
    plan = load_optional_json(MARKET_SEARCH_VISIBILITY_PLAN_PATH)
    plan_shop_id = str(plan.get("shop_id") or "").strip()
    request_shop_id = str(payload.get("store_id") or payload.get("shop_id") or plan_shop_id).strip()
    shop, secrets = _default_search_shop(request_shop_id)
    shop_id = str(shop["id"])
    if plan_shop_id and shop_id != plan_shop_id:
        raise HTTPException(status_code=409, detail="当前上传记录不属于所选店铺，请先切回对应店铺")
    action = _search_visibility_action(plan, product_id)
    receipt = _search_visibility_last_upload_receipt(action)
    task_id = _search_visibility_task_id(action, receipt)
    offer_id = _search_visibility_action_offer_id(action, receipt)
    if not task_id:
        raise HTTPException(status_code=409, detail="没有找到 Ozon task_id，无法查询处理结果")
    if not offer_id:
        raise HTTPException(status_code=409, detail="没有找到 Ozon offer_id，无法读取商品属性")

    client = OzonWriteClient(
        OzonConfig(
            client_id=secrets["client_id"],
            api_key=secrets["api_key"],
            shop_name=shop_id,
        ),
        allow_production_write=True,
    )
    try:
        import_info = client.get_import_info(task_id)
        attributes_response = client.get_product_attributes([offer_id])
    except (OzonUploadApiError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Ozon 结果查询失败：{exc}") from exc

    import_items = _search_visibility_import_items(import_info)
    matched_import = next((
        item for item in import_items
        if str(item.get("offer_id") or "") == offer_id or str(item.get("product_id") or "") == product_id
    ), import_items[0] if import_items else {})
    warnings = [
        error for error in matched_import.get("errors") or []
        if isinstance(error, dict) and str(error.get("level") or "").casefold() == "warning"
    ]
    errors = [
        error for error in matched_import.get("errors") or []
        if isinstance(error, dict) and str(error.get("level") or "").casefold() != "warning"
    ]

    result = attributes_response.get("result")
    attribute_items = result.get("items") if isinstance(result, dict) else result
    remote_item = next((
        dict(item) for item in attribute_items or []
        if isinstance(item, dict) and (
            str(item.get("offer_id") or "") == offer_id
            or str(item.get("id") or item.get("product_id") or "") == product_id
        )
    ), {})
    subject_values = _search_visibility_remote_attribute_values(remote_item, OZON_HASHTAG_ATTRIBUTE_ID)
    intro_values = _search_visibility_remote_attribute_values(remote_item, OZON_ANNOTATION_ATTRIBUTE_ID)
    import_status = str(matched_import.get("status") or "").strip()
    verified = import_status == "imported" and bool(subject_values or intro_values) and not errors
    checked_at = now_iso()
    status_record = {
        "status": "verified" if verified else "needs_review",
        "checked_at": checked_at,
        "task_id": task_id,
        "import_status": import_status or "unknown",
        "warnings": warnings,
        "errors": errors,
        "has_subject_tags": bool(subject_values),
        "subject_tag_value_count": len(subject_values),
        "subject_tag_sample": subject_values[0][:500] if subject_values else "",
        "subject_tag_values": subject_values,
        "has_intro": bool(intro_values),
        "intro_sample": intro_values[0][:500] if intro_values else "",
        "read_api_calls": 2,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }
    action["last_upload_status_check"] = status_record
    if isinstance(action.get("last_upload"), dict):
        action["last_upload"]["task_id"] = task_id
        action["last_upload"]["remote_status"] = status_record["status"]
        action["last_upload"]["import_status"] = status_record["import_status"]
        action["last_upload"]["last_checked_at"] = checked_at
    _write_search_visibility_plan(plan)
    return {
        **status_record,
        "shop_id": shop_id,
        "product_id": product_id,
        "offer_id": offer_id,
        "notice": (
            f"Ozon 已处理完成：task {task_id}，已读到主题标签和简介。"
            if verified and subject_values and intro_values else
            f"Ozon 已返回 {import_status or 'unknown'}，但需要查看警告或属性读取结果。"
        ),
    }


@app.post("/api/workbench/market-intelligence/traffic-performance/dry-run")
async def workbench_traffic_performance_dry_run(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="流量表现 dry-run 输入必须是JSON对象")
    plan = build_traffic_performance_plan(payload)
    atomic_write_json(MARKET_TRAFFIC_PERFORMANCE_PLAN_PATH, plan)
    return {
        **plan,
        "notice": "已生成本地流量表现分析方案；没有提交Ozon更新，没有调用库存接口，没有调整广告预算。",
    }


@app.get("/api/workbench/market-intelligence/traffic-performance/latest")
def workbench_traffic_performance_latest() -> Dict[str, Any]:
    if not MARKET_TRAFFIC_PERFORMANCE_PLAN_PATH.is_file():
        return {
            "schema_version": "1.0.0",
            "available": False,
            "notice": "还没有流量表现 dry-run 方案。",
            "write_api_calls": 0,
            "inventory_api_calls": 0,
            "ad_budget_api_calls": 0,
        }
    return {
        **load_optional_json(MARKET_TRAFFIC_PERFORMANCE_PLAN_PATH),
        "available": True,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
        "ad_budget_api_calls": 0,
    }
