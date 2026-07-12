"""RETS route eligibility and lowest-cost shipping calculation."""

from __future__ import annotations

from typing import Any, Dict, List


def billable_weight(weight_g: float, dimensions: Dict[str, Any], volumetric: Dict[str, Any]) -> Dict[str, Any]:
    volumetric_weight_g = 0.0
    if volumetric.get("enabled"):
        volume_cm3 = dimensions["length"] * dimensions["width"] * dimensions["height"]
        volumetric_weight_g = volume_cm3 / float(volumetric["divisor_cm3_per_kg"]) * 1000
    billed = max(weight_g, volumetric_weight_g)
    return {
        "actual_weight_g": round(weight_g, 2),
        "volumetric_weight_g": round(volumetric_weight_g, 2),
        "billable_weight_g": round(billed, 2),
        "basis": "volumetric" if volumetric_weight_g > weight_g else "actual",
        "volumetric_source": volumetric.get("source", "unknown"),
    }


def route_is_eligible(route: Dict[str, Any], weight_g: float, value_rub: float, dimensions: Dict[str, Any]) -> bool:
    minimum = route.get("min_weight_g_exclusive")
    min_value = route.get("min_value_rub_exclusive")
    sizes = [dimensions["length"], dimensions["width"], dimensions["height"]]
    return (
        (minimum is None or weight_g > minimum)
        and weight_g <= route["max_weight_g"]
        and (min_value is None or value_rub > min_value)
        and value_rub <= route["max_value_rub"]
        and max(sizes) <= route["max_side_cm"]
        and sum(sizes) <= route["max_sum_cm"]
    )


def shipping_cost(route_name: str, weight_g: float, workbook_rules: Dict[str, Any]) -> float:
    cost = workbook_rules["route_costs"][route_name]
    return round(cost["base_fee_cny"] + cost["rate_per_kg_cny"] * (weight_g / 1000), 2)


def eligible_routes(
    weight_g: float,
    dimensions: Dict[str, Any],
    value_rub_by_route: Dict[str, float],
    shipping_rules: Dict[str, Any],
    workbook_rules: Dict[str, Any],
) -> List[Dict[str, Any]]:
    candidates = []
    for name, constraints in shipping_rules["routes"].items():
        if name not in workbook_rules["route_costs"]:
            continue
        value_rub = value_rub_by_route[name]
        if route_is_eligible(constraints, weight_g, value_rub, dimensions):
            candidates.append({
                "route_name": name,
                "shipping_cost_cny": shipping_cost(name, weight_g, workbook_rules),
                "source": "shipping_table",
                "source_sheet": "RETS",
                "source_cells": constraints["source_cells"],
                "calculated_value_rub": round(value_rub, 2),
            })
    return sorted(candidates, key=lambda item: (item["shipping_cost_cny"], item["route_name"]))
