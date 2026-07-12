"""Build and persist cost, pricing, and profitability outputs for one product."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from jsonschema import Draft202012Validator

from .dimension_estimator import (
    estimate_package_dimensions,
    estimate_product_dimensions,
    fit_estimated_product_dimensions_to_confirmed_package,
)
from .pricing_calculator import calculate_ozon_price, commission_rate
from .profitability_checker import analyze_profit
from .shipping_calculator import billable_weight, eligible_routes, shipping_cost
from .weight_estimator import (
    estimate_package_weight,
    estimate_product_weight,
    fit_estimated_product_weight_to_confirmed_package,
)
from .xlsx_rets import load_rets_rules


ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "pricing-engine" / "pricing_rules.json"
SCHEMAS = {
    "cost-analysis.json": ROOT / "templates" / "cost-analysis.schema.json",
    "pricing-result.json": ROOT / "templates" / "pricing-result.schema.json",
    "profit-analysis.json": ROOT / "templates" / "profit-analysis.schema.json",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def schema_errors(value: Any, schema_path: Path) -> List[str]:
    schema = load_json(schema_path)
    return [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    ]


def _purchase_cost(sku: Dict[str, Any], source: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(sku.get("purchase_price"), (int, float)) and sku["purchase_price"] > 0:
        return {
            "value_cny": float(sku["purchase_price"]),
            "source": "sku_specific_price",
            "source_ref": f"source.skus[{sku['sku_id']}].purchase_price",
            "confidence": 100,
        }
    analysis_sku = next(
        (
            item for item in analysis.get("facts", {}).get("skus", [])
            if str(item.get("sku_id")) == str(sku.get("sku_id"))
        ),
        None,
    )
    if analysis_sku and isinstance(analysis_sku.get("price_cny"), (int, float)) and analysis_sku["price_cny"] > 0:
        return {
            "value_cny": float(analysis_sku["price_cny"]),
            "source": "product_analysis",
            "source_ref": f"product-analysis.facts.skus[{sku['sku_id']}].price_cny",
            "confidence": 90,
        }
    ranges = [
        item.get("price_cny") for item in source.get("price_information", {}).get("price_ranges", [])
        if isinstance(item.get("price_cny"), (int, float)) and item["price_cny"] > 0
    ]
    if ranges:
        return {
            "value_cny": float(max(ranges)),
            "source": "price_range_conservative",
            "source_ref": "source.price_information.price_ranges.max",
            "confidence": 70,
        }
    return {
        "value_cny": None,
        "source": "unknown",
        "source_ref": "unknown",
        "confidence": 0,
    }


def _build_cost_analysis(
    product_dir: Path,
    source: Dict[str, Any],
    analysis: Dict[str, Any],
    rules: Dict[str, Any],
    generated_at: str,
) -> Dict[str, Any]:
    product_weight = estimate_product_weight(source, analysis, rules["measurement_profiles"])
    product_dimensions = estimate_product_dimensions(source, analysis, rules["measurement_profiles"])
    product_weight = fit_estimated_product_weight_to_confirmed_package(
        source, product_weight, rules["package_estimation"]
    )
    product_dimensions = fit_estimated_product_dimensions_to_confirmed_package(
        source, product_dimensions, rules["package_estimation"]
    )
    package_weight = estimate_package_weight(source, product_weight, rules["package_estimation"])
    package_dimensions = estimate_package_dimensions(source, product_dimensions, rules["package_estimation"])
    sku_costs = [
        {
            "sku_id": str(sku["sku_id"]),
            "sku_name": sku["sku_name"],
            "purchase_cost": _purchase_cost(sku, source, analysis),
        }
        for sku in source["skus"]
    ]
    warnings = []
    if product_weight["estimated"]:
        warnings.append("Product weight is estimated and is not a 1688 confirmed fact.")
    if product_dimensions["estimated"]:
        warnings.append("Product dimensions are estimated and are not a 1688 confirmed fact.")
    if package_weight["estimated"]:
        warnings.append("Package weight is estimated and is strictly greater than product weight.")
    if package_dimensions["estimated"]:
        warnings.append("Package dimensions are estimated and every side is strictly greater than the product side.")
    if any(item["purchase_cost"]["source"] == "price_range_conservative" for item in sku_costs):
        warnings.append("At least one SKU uses the highest captured price-range value because no SKU-specific purchase price was available.")
    if any(item["purchase_cost"]["value_cny"] is None for item in sku_costs):
        warnings.append("At least one SKU has no usable purchase price and must be rejected before upload.")
    return {
        "schema_version": "1.1.0",
        "product_id": source["product_id"],
        "source_refs": [
            f"products/{source['product_id']}/input/source.json",
            f"products/{source['product_id']}/output/product-analysis.json",
            f"products/{source['product_id']}/output/product-positioning.json",
        ],
        "product_weight": product_weight,
        "product_dimensions": product_dimensions,
        "package_weight": package_weight,
        "package_dimensions": package_dimensions,
        # Backward-compatible aliases used by older shipping consumers.
        "weight": package_weight,
        "dimensions": package_dimensions,
        "measurement_hierarchy": {
            "valid": package_weight["value"] > product_weight["value"]
            and all(
                package_dimensions[key] > product_dimensions[key]
                for key in ("length", "width", "height")
            ),
            "rule": "package weight and every package dimension must be greater than product measurements",
        },
        "sku_costs": sku_costs,
        "warnings": warnings,
        "generated_at": generated_at,
    }


def _apply_manual_confirmation(
    source: Dict[str, Any], confirmation: Dict[str, Any]
) -> None:
    """Overlay human-approved estimates in memory without changing source.json."""
    prices = confirmation.get("sku_purchase_prices_cny") or {}
    for sku in source.get("skus") or []:
        sku_id = str(sku.get("sku_id"))
        if sku_id in prices and isinstance(prices[sku_id], (int, float)) and prices[sku_id] > 0:
            sku["purchase_price"] = float(prices[sku_id])
            sku["price"] = float(prices[sku_id])
            sku["price_source"] = "sku_specific_price"


def _apply_manual_measurements(
    cost_analysis: Dict[str, Any], confirmation: Dict[str, Any]
) -> None:
    fields = confirmation.get("fields") or {}
    product_dimensions = fields.get("product_dimensions") or {}
    package_dimensions = fields.get("package_dimensions") or {}
    product_weight = fields.get("product_weight") or {}
    package_weight = fields.get("package_weight") or {}
    if not all(isinstance(product_dimensions.get(key), (int, float)) for key in ("length", "width", "height")):
        return
    if not all(isinstance(package_dimensions.get(key), (int, float)) for key in ("length", "width", "height")):
        return
    if not isinstance(product_weight.get("value_g"), (int, float)) or not isinstance(package_weight.get("value_g"), (int, float)):
        return
    common = {
        "source": "estimated",
        "source_ref": "input/manual-confirmation.json",
        "confidence": 100,
        "estimated": True,
        "profile": "manual_confirmation",
    }
    product_dimensions_value = {
        **{key: float(product_dimensions[key]) for key in ("length", "width", "height")},
        "unit": "cm", **common,
    }
    package_dimensions_value = {
        **{key: float(package_dimensions[key]) for key in ("length", "width", "height")},
        "unit": "cm", **common,
    }
    product_weight_value = {"value": float(product_weight["value_g"]), "unit": "g", **common}
    package_weight_value = {"value": float(package_weight["value_g"]), "unit": "g", **common}
    for item in (product_dimensions_value, package_dimensions_value, product_weight_value, package_weight_value):
        item["validation"] = {
            "status": "valid",
            "original_value": "manual_confirmation",
            "corrected_value": item.get("value") or {key: item[key] for key in ("length", "width", "height") if key in item},
            "reason": "estimated value approved in the one-time manual batch confirmation",
        }
    cost_analysis.update({
        "product_dimensions": product_dimensions_value,
        "package_dimensions": package_dimensions_value,
        "product_weight": product_weight_value,
        "package_weight": package_weight_value,
        "dimensions": package_dimensions_value,
        "weight": package_weight_value,
        "measurement_hierarchy": {
            "valid": package_weight_value["value"] > product_weight_value["value"]
            and all(package_dimensions_value[key] > product_dimensions_value[key] for key in ("length", "width", "height")),
            "rule": "package weight and every package dimension must be greater than product measurements",
        },
    })
    reference = f"products/{cost_analysis['product_id']}/input/manual-confirmation.json"
    if reference not in cost_analysis["source_refs"]:
        cost_analysis["source_refs"].append(reference)
    cost_analysis["warnings"] = [
        warning for warning in cost_analysis.get("warnings") or []
        if not warning.startswith(("Product weight is estimated", "Product dimensions are estimated", "Package weight is estimated", "Package dimensions are estimated"))
    ]
    cost_analysis["warnings"].append("Measurements are estimates approved by the user during one-time batch confirmation.")


def _price_sku(
    sku_cost: Dict[str, Any],
    cost_analysis: Dict[str, Any],
    source: Dict[str, Any],
    analysis: Dict[str, Any],
    rules: Dict[str, Any],
    workbook_rules: Dict[str, Any],
    commission: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    purchase = sku_cost["purchase_cost"]["value_cny"]
    sku_id = sku_cost["sku_id"]
    if purchase is None:
        rejected = {
            "sku_id": sku_id,
            "purchase_cost_cny": None,
            "shipping": None,
            "selling_price_cny": None,
            "selling_price_rub": None,
            "commission_rate": commission["value"],
            "commission_source": commission["source"],
            "estimated_profit_cny": None,
            "status": "REJECT",
            "errors": ["purchase_price_missing"],
        }
        return rejected, {
            "sku_id": sku_id,
            "total_cost_cny": None,
            "selling_price_cny": None,
            "profit_cny": None,
            "profit_margin": None,
            "logistics_ratio": None,
            "recommendation": "REJECT",
            "issues": ["purchase_price_missing"],
        }

    weight = cost_analysis.get("package_weight", cost_analysis["weight"])["value"]
    dimensions = cost_analysis.get("package_dimensions", cost_analysis["dimensions"])
    billed = billable_weight(weight, dimensions, rules["shipping"]["volumetric_weight"])
    rub_per_cny = workbook_rules["exchange_rate_rub_per_cny"]
    value_by_route: Dict[str, float] = {}
    pricing_by_route: Dict[str, Dict[str, Any]] = {}
    for route_name in rules["shipping"]["routes"]:
        route_shipping = shipping_cost(route_name, billed["billable_weight_g"], workbook_rules)
        calculated = calculate_ozon_price(
            purchase,
            route_shipping,
            rules["pricing"],
            commission["value"],
            rub_per_cny,
        )
        value_by_route[route_name] = calculated["selling_price_rub"]
        pricing_by_route[route_name] = calculated

    candidates = eligible_routes(
        billed["billable_weight_g"],
        dimensions,
        value_by_route,
        rules["shipping"],
        workbook_rules,
    )
    if not candidates:
        rejected = {
            "sku_id": sku_id,
            "purchase_cost_cny": purchase,
            "shipping": None,
            "selling_price_cny": None,
            "selling_price_rub": None,
            "commission_rate": commission["value"],
            "commission_source": commission["source"],
            "estimated_profit_cny": None,
            "status": "REJECT",
            "errors": ["no_eligible_rets_route"],
        }
        return rejected, {
            "sku_id": sku_id,
            "total_cost_cny": None,
            "selling_price_cny": None,
            "profit_cny": None,
            "profit_margin": None,
            "logistics_ratio": None,
            "recommendation": "REJECT",
            "issues": ["no_eligible_rets_route"],
        }

    selected = candidates[0]
    calculated = pricing_by_route[selected["route_name"]]
    selected["weight"] = billed
    profit = analyze_profit(
        purchase,
        selected["shipping_cost_cny"],
        calculated,
        rules["profitability"],
    )
    pricing = {
        "sku_id": sku_id,
        "purchase_cost_cny": round(purchase, 2),
        "purchase_cost_source": sku_cost["purchase_cost"]["source"],
        "shipping": selected,
        **calculated,
        "commission_source": commission["source"],
        "status": profit["recommendation"],
        "errors": profit["issues"],
    }
    return pricing, {"sku_id": sku_id, **profit}


def build_pricing_package(product_dir: Path, generated_at: str | None = None) -> Dict[str, Dict[str, Any]]:
    product_dir = product_dir.resolve()
    timestamp = generated_at or utc_now()
    source = load_json(product_dir / "input" / "source.json")
    confirmation_path = product_dir / "input" / "manual-confirmation.json"
    confirmation = load_json(confirmation_path) if confirmation_path.is_file() else {}
    if confirmation:
        _apply_manual_confirmation(source, confirmation)
    analysis = load_json(product_dir / "output" / "product-analysis.json")
    rules = load_json(RULES_PATH)
    workbook_path = ROOT / rules["shipping"]["workbook"]
    workbook_rules = load_rets_rules(workbook_path)
    cost_analysis = _build_cost_analysis(product_dir, source, analysis, rules, timestamp)
    if confirmation:
        _apply_manual_measurements(cost_analysis, confirmation)

    category_id = "unknown"
    category_path = product_dir / "output" / "ozon-category.json"
    if category_path.is_file():
        category_id = load_json(category_path).get("category_id", "unknown")
    commission = commission_rate(analysis.get("category", "unknown"), category_id, rules["pricing"])
    sku_pricing = []
    sku_profit = []
    for sku_cost in cost_analysis["sku_costs"]:
        pricing, profit = _price_sku(
            sku_cost, cost_analysis, source, analysis, rules, workbook_rules, commission
        )
        sku_pricing.append(pricing)
        sku_profit.append(profit)

    recommendation_order = {"UPLOAD": 0, "WARNING": 1, "REJECT": 2}
    overall = max(
        (item["recommendation"] for item in sku_profit),
        key=lambda value: recommendation_order[value],
        default="REJECT",
    )
    pricing_result = {
        "schema_version": "1.0.0",
        "product_id": source["product_id"],
        "pricing_source": "pricing-engine",
        "shipping_rules": {
            "workbook": rules["shipping"]["workbook"],
            "worksheet": workbook_rules["worksheet"],
            "workbook_sha256": workbook_rules["workbook_sha256"],
            "selection_strategy": rules["shipping"]["selection_strategy"],
        },
        "exchange_rate": {
            "rub_per_cny": workbook_rules["exchange_rate_rub_per_cny"],
            "source": workbook_rules["exchange_rate_source"],
        },
        "commission": commission,
        "sku_pricing": sku_pricing,
        "recommendation": overall,
        "warnings": [
            "Only the RETS worksheet is read; GUOO and all other workbook sheets are ignored.",
            *cost_analysis["warnings"],
        ],
        "generated_at": timestamp,
    }
    valid_profit = [item for item in sku_profit if item["profit_cny"] is not None]
    profit_analysis = {
        "schema_version": "1.0.0",
        "product_id": source["product_id"],
        "sku_analysis": sku_profit,
        "summary": {
            "total_cost_cny": round(sum(item["total_cost_cny"] for item in valid_profit), 2) if valid_profit else None,
            "selling_price_cny": round(sum(item["selling_price_cny"] for item in valid_profit), 2) if valid_profit else None,
            "profit_cny": round(sum(item["profit_cny"] for item in valid_profit), 2) if valid_profit else None,
            "profit_margin": round(
                sum(item["profit_cny"] for item in valid_profit) / sum(item["selling_price_cny"] for item in valid_profit), 4
            ) if valid_profit and sum(item["selling_price_cny"] for item in valid_profit) else None,
            "logistics_ratio": round(
                sum(item["logistics_ratio"] for item in valid_profit) / len(valid_profit), 4
            ) if valid_profit else None,
            "recommendation": overall,
        },
        "generated_at": timestamp,
    }
    package = {
        "cost-analysis.json": cost_analysis,
        "pricing-result.json": pricing_result,
        "profit-analysis.json": profit_analysis,
    }
    for filename, value in package.items():
        errors = schema_errors(value, SCHEMAS[filename])
        if errors:
            raise ValueError(f"{filename} failed schema validation: {'; '.join(errors)}")
    return package


def write_pricing_package(product_dir: Path, generated_at: str | None = None) -> Dict[str, Dict[str, Any]]:
    package = build_pricing_package(product_dir, generated_at)
    output = product_dir.resolve() / "output"
    for filename, value in package.items():
        write_json_atomic(output / filename, value)
    return package


def apply_pricing_to_existing_draft(
    product_dir: Path, package: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    draft_path = product_dir / "output" / "ozon-draft.json"
    draft = load_json(draft_path)
    pricing = package["pricing-result.json"]
    profit = package["profit-analysis.json"]
    by_sku = {str(item["sku_id"]): item for item in pricing["sku_pricing"]}
    prices = []
    for sku in draft["skus"]:
        item = by_sku[str(sku["source_sku_id"])]
        sku["sale_price"] = str(item["selling_price_cny"]) if item["selling_price_cny"] is not None else None
        sku["sale_price_rub"] = str(item["selling_price_rub"]) if item["selling_price_rub"] is not None else None
        sku["sale_currency_code"] = "CNY" if item["selling_price_cny"] is not None else "unknown"
        if item["selling_price_cny"] is not None:
            prices.append(item["selling_price_cny"])
    draft["price"]["price"] = str(min(prices)) if prices else None
    draft["price"]["currency_code"] = "CNY" if prices else "unknown"
    draft["currency"] = "CNY" if prices else "unknown"
    draft["pricing_source"] = f"products/{product_dir.name}/output/pricing-result.json"
    draft["profit_warning"] = sorted({
        issue for item in profit["sku_analysis"] for issue in item["issues"]
    })
    for filename in ("cost-analysis.json", "pricing-result.json", "profit-analysis.json"):
        reference = f"products/{product_dir.name}/output/{filename}"
        if reference not in draft["source_refs"]:
            draft["source_refs"].append(reference)
    draft["upload_allowed"] = False
    draft["preflight"]["status"] = "failed"
    draft["preflight"]["errors"] = [
        error for error in draft["preflight"]["errors"]
        if "sale price" not in error.casefold() and "pricing engine" not in error.casefold()
    ]
    if pricing["recommendation"] == "REJECT":
        draft["preflight"]["errors"].append(
            "Pricing Engine rejected at least one SKU; upload is forbidden."
        )
    errors = schema_errors(draft, ROOT / "templates" / "ozon-draft.schema.json")
    if errors:
        raise ValueError("ozon-draft.json failed schema validation: " + "; ".join(errors))
    write_json_atomic(draft_path, draft)
    return draft
