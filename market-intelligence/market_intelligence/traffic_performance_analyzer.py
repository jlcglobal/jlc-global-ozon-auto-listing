"""Local dry-run analyzer for Ozon search, recommendation, and ads traffic."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _number(value: Any) -> float:
    if isinstance(value, bool) or value in {None, "", "unknown"}:
        return 0.0
    text = str(value).replace("\u00a0", "").replace(" ", "").replace("₽", "").replace("%", "").replace(",", ".")
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def _metrics(value: Any) -> Dict[str, float]:
    data = value if isinstance(value, Mapping) else {}
    impressions = _number(data.get("impressions") or data.get("shows") or data.get("views"))
    clicks = _number(data.get("clicks"))
    orders = _number(data.get("orders") or data.get("ordered_units"))
    revenue = _number(data.get("revenue_rub") or data.get("ordered_amount_rub") or data.get("sales_rub"))
    spend = _number(data.get("ad_spend_rub") or data.get("spend_rub") or data.get("cost_rub"))
    ctr = round(clicks / impressions, 4) if impressions else 0.0
    conversion = round(orders / clicks, 4) if clicks else 0.0
    acos = round(spend / revenue, 4) if spend and revenue else 0.0
    return {
        "impressions": impressions,
        "clicks": clicks,
        "orders": orders,
        "revenue_rub": revenue,
        "spend_rub": spend,
        "ctr": ctr,
        "conversion": conversion,
        "acos": acos,
    }


def _product_id(item: Mapping[str, Any]) -> str:
    for key in ("product_id", "local_product_id", "source_product_id", "offer_id", "sku"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return "unknown"


def normalize_traffic_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    search = _metrics(item.get("search") or item.get("search_traffic"))
    recommendation = _metrics(item.get("recommendation") or item.get("recommendation_traffic") or item.get("recommended"))
    ads = _metrics(item.get("ads") or item.get("advertising") or item.get("ad_traffic"))
    total_orders = search["orders"] + recommendation["orders"] + ads["orders"]
    total_revenue = search["revenue_rub"] + recommendation["revenue_rub"] + ads["revenue_rub"]
    return {
        "product_id": _product_id(item),
        "offer_ids": [str(value).strip() for value in item.get("offer_ids") or [] if str(value).strip()],
        "title": str(item.get("title") or item.get("title_ru") or "").strip(),
        "search": search,
        "recommendation": recommendation,
        "ads": ads,
        "totals": {
            "orders": round(total_orders, 3),
            "revenue_rub": round(total_revenue, 3),
            "impressions": round(search["impressions"] + recommendation["impressions"] + ads["impressions"], 3),
            "clicks": round(search["clicks"] + recommendation["clicks"] + ads["clicks"], 3),
            "spend_rub": round(ads["spend_rub"], 3),
        },
    }


def _share(value: float, total: float) -> float:
    return round(value / total, 4) if total else 0.0


def classify_traffic_layer(item: Mapping[str, Any]) -> str:
    totals = item.get("totals") or {}
    search = item.get("search") or {}
    recommendation = item.get("recommendation") or {}
    ads = item.get("ads") or {}
    total_orders = _number(totals.get("orders"))
    total_clicks = _number(totals.get("clicks"))
    recommendation_share = _share(_number(recommendation.get("orders")), total_orders)
    search_share = _share(_number(search.get("orders")), total_orders)
    ad_spend = _number(ads.get("spend_rub"))
    ad_orders = _number(ads.get("orders"))
    ad_acos = _number(ads.get("acos"))

    if ad_spend > 0 and (ad_orders <= 0 or ad_acos >= 0.7):
        return "ad_spend_risk"
    if total_orders > 0 and recommendation_share >= 0.5:
        return "recommendation_led"
    if total_orders > 0 and search_share >= 0.5:
        return "search_led"
    if total_clicks > 0 and total_orders <= 0:
        return "click_no_order"
    if _number(totals.get("impressions")) > 0 and total_clicks <= 0:
        return "exposure_no_click"
    return "insufficient_data"


def build_traffic_action(item: Mapping[str, Any]) -> Dict[str, Any]:
    layer = classify_traffic_layer(item)
    search = item.get("search") or {}
    recommendation = item.get("recommendation") or {}
    ads = item.get("ads") or {}
    totals = item.get("totals") or {}
    total_orders = _number(totals.get("orders"))
    total_revenue = _number(totals.get("revenue_rub"))
    title_locked = layer in {"recommendation_led", "search_led"} and total_orders > 0
    focus: List[str] = []
    if layer == "recommendation_led":
        focus = ["lock_title", "maintain_card_quality", "protect_main_image", "watch_recommendation_traffic"]
    elif layer == "search_led":
        focus = ["search_keyword_plan", "fill_subject_tags_to_30", "avoid_title_rewrite_if_orders_exist"]
    elif layer == "ad_spend_risk":
        focus = ["review_ads", "check_bid_and_query_match", "do_not_auto_raise_budget"]
    elif layer == "click_no_order":
        focus = ["check_price", "check_main_image", "check_description_and_reviews"]
    elif layer == "exposure_no_click":
        focus = ["improve_main_image", "check_title_relevance", "check_price_position"]
    else:
        focus = ["collect_more_data"]
    return {
        "product_id": item["product_id"],
        "offer_ids": item.get("offer_ids") or [],
        "title": item.get("title") or "",
        "traffic_layer": layer,
        "title_locked": title_locked,
        "allowed_changes": [] if title_locked else ["content_review"],
        "blocked_changes": ["title"] if title_locked else [],
        "focus": focus,
        "evidence": {
            "search": search,
            "recommendation": recommendation,
            "ads": ads,
            "totals": totals,
            "shares": {
                "search_orders": _share(_number(search.get("orders")), total_orders),
                "recommendation_orders": _share(_number(recommendation.get("orders")), total_orders),
                "ads_orders": _share(_number(ads.get("orders")), total_orders),
                "search_revenue": _share(_number(search.get("revenue_rub")), total_revenue),
                "recommendation_revenue": _share(_number(recommendation.get("revenue_rub")), total_revenue),
                "ads_revenue": _share(_number(ads.get("revenue_rub")), total_revenue),
            },
        },
        "reason_cn": _reason_cn(layer, title_locked),
    }


def _reason_cn(layer: str, title_locked: bool) -> str:
    if layer == "recommendation_led":
        return "推荐流量贡献更高，先保护商品卡和主图表现；已有订单时不要自动改标题。"
    if layer == "search_led":
        return "搜索流量贡献更高，优先结合搜索词做标题/主题标签 dry-run；已有订单时标题仍需谨慎。"
    if layer == "ad_spend_risk":
        return "广告有花费但订单不足或 ACOS 偏高，只建议人工复查广告，不自动加预算。"
    if layer == "click_no_order":
        return "有点击但没有订单，问题更可能在价格、图片、描述或评价，不应只改关键词。"
    if layer == "exposure_no_click":
        return "有曝光但点击弱，优先检查主图、价格和标题相关性。"
    return "数据不足，本轮只记录，不建议自动改标题、标签或广告。"


def build_traffic_performance_plan(
    source: Mapping[str, Any],
    *,
    generated_at: Optional[str] = None,
    batch_size: int = 50,
) -> Dict[str, Any]:
    items = [
        normalize_traffic_item(item)
        for item in source.get("items") or source.get("products") or []
        if isinstance(item, Mapping)
    ]
    actions = [build_traffic_action(item) for item in items]
    layers = {
        "recommendation_led": [item for item in actions if item["traffic_layer"] == "recommendation_led"],
        "search_led": [item for item in actions if item["traffic_layer"] == "search_led"],
        "ad_spend_risk": [item for item in actions if item["traffic_layer"] == "ad_spend_risk"],
        "click_no_order": [item for item in actions if item["traffic_layer"] == "click_no_order"],
        "exposure_no_click": [item for item in actions if item["traffic_layer"] == "exposure_no_click"],
        "insufficient_data": [item for item in actions if item["traffic_layer"] == "insufficient_data"],
    }
    batches = []
    for layer, layer_items in layers.items():
        if not layer_items or layer == "insufficient_data":
            continue
        for index in range(0, len(layer_items), max(1, int(batch_size))):
            group = layer_items[index:index + max(1, int(batch_size))]
            batches.append({
                "batch_id": f"{layer}-{index // max(1, int(batch_size)) + 1}",
                "traffic_layer": layer,
                "product_count": len(group),
                "product_ids": [item["product_id"] for item in group],
                "allowed_changes": sorted({change for item in group for change in item.get("allowed_changes", [])}),
            })
    return {
        "schema_version": "1.0.0",
        "mode": "dry_run",
        "source": "ozon_seller_traffic_performance",
        "shop_id": str(source.get("shop_id") or source.get("store_id") or "unknown"),
        "period_days": int(source.get("period_days") or source.get("window_days") or 30),
        "recommended_schedule_days": 7,
        "generated_at": generated_at or _now(),
        "summary": {
            "products": len(actions),
            "recommendation_led": len(layers["recommendation_led"]),
            "search_led": len(layers["search_led"]),
            "ad_spend_risk": len(layers["ad_spend_risk"]),
            "click_no_order": len(layers["click_no_order"]),
            "exposure_no_click": len(layers["exposure_no_click"]),
            "insufficient_data": len(layers["insufficient_data"]),
        },
        "batches": batches,
        "actions": actions,
        "safety": {
            "dry_run_only": True,
            "write_api_calls": 0,
            "inventory_api_calls": 0,
            "ad_budget_api_calls": 0,
            "requires_explicit_write_scope_before_ozon_update": True,
        },
    }
