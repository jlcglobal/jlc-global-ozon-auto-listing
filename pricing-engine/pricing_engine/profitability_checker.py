"""Profitability recommendation and rejection rules."""

from __future__ import annotations

from typing import Any, Dict


def analyze_profit(
    purchase_cost_cny: float,
    shipping_cost_cny: float,
    pricing: Dict[str, Any],
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    selling = pricing["selling_price_cny"]
    total_cost = selling - pricing["estimated_profit_cny"]
    profit = pricing["estimated_profit_cny"]
    margin = profit / selling if selling else 0
    fixed_cost = purchase_cost_cny + shipping_cost_cny + pricing["packing_fee_cny"] + pricing["other_fixed_cost_cny"]
    logistics_ratio = shipping_cost_cny / fixed_cost if fixed_cost else 0
    issues = []
    if profit < rules["minimum_profit_cny"]:
        issues.append("profit_below_minimum")
    if margin < rules["minimum_profit_margin"]:
        issues.append("profit_margin_below_minimum")
    if issues:
        recommendation = "REJECT"
    elif logistics_ratio > rules["warning_logistics_ratio"]:
        recommendation = "WARNING"
        issues.append("logistics_ratio_too_high")
    else:
        recommendation = "UPLOAD"
    return {
        "total_cost_cny": round(total_cost, 2),
        "selling_price_cny": round(selling, 2),
        "profit_cny": round(profit, 2),
        "profit_margin": round(margin, 4),
        "logistics_ratio": round(logistics_ratio, 4),
        "recommendation": recommendation,
        "issues": issues,
    }
