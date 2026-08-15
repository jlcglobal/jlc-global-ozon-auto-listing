"""Deterministic price formulas with explicit percentage fees."""

from __future__ import annotations

import math
from typing import Any, Dict


def calculate_base_price(purchase_cost_cny: float, shipping_cost_cny: float, other_cost_cny: float, profit_rate: float) -> float:
    return round((purchase_cost_cny + shipping_cost_cny + other_cost_cny) * (1 + profit_rate), 2)


def calculate_ozon_price(
    purchase_cost_cny: float,
    shipping_cost_cny: float,
    rules: Dict[str, Any],
    commission_rate: float,
    rub_per_cny: float,
) -> Dict[str, Any]:
    fixed_other = rules["packing_fee_cny"] + rules["other_fixed_cost_cny"]
    fixed_cost = purchase_cost_cny + shipping_cost_cny + fixed_other
    total_rate = (
        commission_rate
        + rules["logistics_commission_rate"]
        + rules["acquiring_fee_rate"]
        + rules["withdrawal_fee_rate"]
    )
    if total_rate >= 1:
        raise ValueError("Combined percentage fees must be below 100%")
    raw_price = fixed_cost * (1 + rules["default_profit_rate"]) / (1 - total_rate)
    step = float(rules["rounding_step_cny"])
    selling_cny = math.ceil(raw_price / step) * step
    variable_fees = selling_cny * total_rate
    profit = selling_cny - fixed_cost - variable_fees
    return {
        "base_cost_cny": round(fixed_cost, 2),
        "base_price_before_percentage_fees_cny": calculate_base_price(
            purchase_cost_cny, shipping_cost_cny, fixed_other, rules["default_profit_rate"]
        ),
        "selling_price_cny": round(selling_cny, 2),
        "selling_price_rub": round(selling_cny * rub_per_cny, 2),
        "commission_rate": commission_rate,
        "logistics_commission_rate": rules["logistics_commission_rate"],
        "acquiring_fee_rate": rules["acquiring_fee_rate"],
        "withdrawal_fee_rate": rules["withdrawal_fee_rate"],
        "packing_fee_cny": rules["packing_fee_cny"],
        "other_fixed_cost_cny": rules["other_fixed_cost_cny"],
        "total_percentage_fee_rate": round(total_rate, 4),
        "estimated_variable_fees_cny": round(variable_fees, 2),
        "estimated_profit_cny": round(profit, 2),
        "profit_rate_markup": rules["default_profit_rate"],
    }


def commission_rate(category: str, category_id: Any, rules: Dict[str, Any]) -> Dict[str, Any]:
    overrides = rules.get("category_commission_rates", {})
    selected = overrides.get(str(category_id)) or overrides.get(category)
    source = "category_rule" if selected is not None else "default_unknown_category"
    value = float(selected if selected is not None else rules["commission_rate_default"])
    value = max(float(rules["commission_rate_min"]), min(float(rules["commission_rate_max"]), value))
    return {"value": value, "source": source}
