"""Product and package dimension extraction, validation, and estimation."""

from __future__ import annotations

import re
from typing import Any, Dict

from .source_measurements import max_source_sku_dimension_summary
from .weight_estimator import select_profile


def _parse_dimensions(value: Any) -> tuple[float, float, float] | None:
    match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(mm|毫米|cm|厘米)?\s*[x×*]\s*"
        r"(\d+(?:[.,]\d+)?)\s*(mm|毫米|cm|厘米)?\s*[x×*]\s*"
        r"(\d+(?:[.,]\d+)?)\s*(mm|毫米|cm|厘米)?",
        str(value or ""),
        re.I,
    )
    if not match:
        return None
    units = [match.group(index) for index in (2, 4, 6) if match.group(index)]
    if not units:
        return None
    normalized_units = {
        "mm" if unit.casefold() in {"mm", "毫米"} else "cm" for unit in units
    }
    if len(normalized_units) != 1:
        return None
    factor = 0.1 if "mm" in normalized_units else 1.0
    return tuple(
        float(match.group(index).replace(",", ".")) * factor
        for index in (1, 3, 5)
    )


def _source_product_dimensions(source: Dict[str, Any]) -> tuple[float, float, float] | None:
    names = {
        "尺寸", "产品尺寸", "商品尺寸", "规格尺寸", "长宽高",
        "规格长宽高", "产品规格长宽高", "商品规格长宽高",
    }

    def normalize_name(value: Any) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").casefold())

    for item in source.get("product_attributes") or []:
        if normalize_name(item.get("name_cn")) in names:
            parsed = _parse_dimensions(item.get("value_cn"))
            if parsed:
                return parsed
    return None


def estimate_product_dimensions(
    source: Dict[str, Any], analysis: Dict[str, Any], profiles: list[Dict[str, Any]]
) -> Dict[str, Any]:
    profile = select_profile(source, analysis, profiles)
    profile_name = profile["name"]
    source_values = _source_product_dimensions(source)
    source_sku_values = max_source_sku_dimension_summary(source)
    facts = analysis.get("facts", {}).get("dimensions", {})
    facts = facts if isinstance(facts, dict) else {}
    fact_values = (facts.get("length_cm"), facts.get("width_cm"), facts.get("height_cm"))
    if source_values:
        length, width, height = source_values
        source_name, source_ref, confidence, estimated = (
            "1688", "source.product_attributes.product_dimensions", 100, False
        )
    elif source_sku_values:
        length, width, height = (
            float(source_sku_values[key]) for key in ("length", "width", "height")
        )
        source_name, source_ref, confidence, estimated = (
            "estimated" if source_sku_values.get("estimated") else "1688",
            str(source_sku_values.get("source_ref") or "source.product_attributes.sku_measurement_table"),
            int(source_sku_values.get("confidence", 100)),
            bool(source_sku_values.get("estimated")),
        )
    elif isinstance(facts.get("by_sku_cm"), dict) and facts["by_sku_cm"]:
        sku_dimensions = [
            item for item in facts["by_sku_cm"].values()
            if isinstance(item, dict) and all(
                isinstance(item.get(key), (int, float)) and float(item[key]) > 0
                for key in ("length", "width", "height")
            )
        ]
        if len(sku_dimensions) != len(facts["by_sku_cm"]):
            sku_dimensions = []
        if sku_dimensions:
            length, width, height = (
                max(float(item[key]) for item in sku_dimensions)
                for key in ("length", "width", "height")
            )
            source_name, source_ref, confidence, estimated = (
                "product_analysis",
                "product-analysis.facts.dimensions.by_sku_cm",
                95,
                facts.get("provenance") != "confirmed_source",
            )
            profile_name = (
                "manual_confirmation"
                if facts.get("provenance") == "estimated_human_approved"
                else profile_name
            )
        else:
            estimate = profile["dimensions_cm"]
            length, width, height = (float(estimate[key]) for key in ("length", "width", "height"))
            source_name, source_ref, confidence, estimated = (
                "estimated", f"pricing_rules.measurement_profiles.{profile['name']}", int(profile["confidence"]), True
            )
    elif all(isinstance(value, (int, float)) and value > 0 for value in fact_values):
        length, width, height = (float(value) for value in fact_values)
        source_name, source_ref, confidence, estimated = (
            "product_analysis", "product-analysis.facts.dimensions", 90, False
        )
    else:
        estimate = profile["dimensions_cm"]
        length, width, height = (float(estimate[key]) for key in ("length", "width", "height"))
        source_name, source_ref, confidence, estimated = (
            "estimated", f"pricing_rules.measurement_profiles.{profile['name']}", int(profile["confidence"]), True
        )

    dimensions = [length, width, height]
    valid = all(0 < value <= 150 for value in dimensions) and sum(dimensions) <= 310
    original = {"length": length, "width": width, "height": height}
    if not valid:
        estimate = profile["dimensions_cm"]
        length, width, height = (float(estimate[key]) for key in ("length", "width", "height"))
        source_name, source_ref, confidence, estimated = (
            "estimated", f"pricing_rules.measurement_profiles.{profile['name']}", int(profile["confidence"]), True
        )
    corrected = {"length": length, "width": width, "height": height}
    return {
        **corrected,
        "unit": "cm",
        "source": source_name,
        "source_ref": source_ref,
        "confidence": confidence,
        "estimated": estimated,
        "profile": profile_name,
        "validation": {
            "status": "valid" if valid else "corrected",
            "original_value": original,
            "corrected_value": corrected,
            "reason": "within RETS size limits" if valid else "dimensions exceed RETS maximum limits",
        },
    }


def estimate_package_dimensions(
    source: Dict[str, Any],
    product_dimensions: Dict[str, Any],
    package_rules: Dict[str, Any],
) -> Dict[str, Any]:
    """Return package dimensions and enforce every package side > product side."""
    raw = source.get("package_dimensions") or {}
    raw_values = [raw.get(f"{key}_cm") for key in ("length", "width", "height")]
    product_values = [float(product_dimensions[key]) for key in ("length", "width", "height")]
    valid_source = all(
        isinstance(value, (int, float)) and float(value) > product
        for value, product in zip(raw_values, product_values)
    )
    if valid_source:
        values = [float(value) for value in raw_values]
        source_name, source_ref, confidence, estimated = "1688", "source.package_dimensions", 100, False
        status = "valid"
        reason = "confirmed package dimensions are greater than product dimensions"
    else:
        multiplier = float(package_rules["dimension_multiplier"])
        minimum_extra = float(package_rules["minimum_extra_dimension_cm"])
        if (
            product_dimensions.get("source") == "1688"
            and "sku_measurement_table" in str(product_dimensions.get("source_ref") or "")
        ):
            values = [round(value + minimum_extra, 2) for value in product_values]
        else:
            values = [round(max(value + minimum_extra, value * multiplier), 2) for value in product_values]
        source_name, source_ref = "estimated", "pricing_rules.package_estimation"
        confidence = min(int(product_dimensions["confidence"]), int(package_rules["confidence_cap"]))
        estimated = True
        status = "corrected" if any(isinstance(value, (int, float)) for value in raw_values) else "valid"
        reason = (
            "source package dimensions were not all greater than product dimensions; package dimensions were corrected"
            if status == "corrected"
            else "package dimensions estimated with configured packaging allowance"
        )
    corrected = dict(zip(("length", "width", "height"), values))
    return {
        **corrected,
        "unit": "cm",
        "source": source_name,
        "source_ref": source_ref,
        "confidence": confidence,
        "estimated": estimated,
        "profile": str(product_dimensions["profile"]),
        "validation": {
            "status": status,
            "original_value": dict(zip(("length", "width", "height"), raw_values)),
            "corrected_value": corrected,
            "reason": reason,
        },
    }


def fit_estimated_product_dimensions_to_confirmed_package(
    source: Dict[str, Any], product_dimensions: Dict[str, Any], package_rules: Dict[str, Any]
) -> Dict[str, Any]:
    """Preserve confirmed package sides by shrinking only estimated product sides."""
    raw = source.get("package_dimensions") or {}
    raw_values = [raw.get(f"{key}_cm") for key in ("length", "width", "height")]
    if not (
        product_dimensions.get("estimated") is True
        and all(isinstance(value, (int, float)) and float(value) > 0 for value in raw_values)
        and any(
            float(product_dimensions[key]) >= float(raw_value)
            for key, raw_value in zip(("length", "width", "height"), raw_values)
        )
    ):
        return product_dimensions
    corrected = {}
    for key, raw_value in zip(("length", "width", "height"), raw_values):
        package_value = float(raw_value)
        allowance = min(float(package_rules["minimum_extra_dimension_cm"]), package_value * 0.2)
        corrected[key] = round(max(
            0.1,
            min(package_value / float(package_rules["dimension_multiplier"]), package_value - allowance),
        ), 2)
    result = dict(product_dimensions)
    result.update(corrected)
    result["source_ref"] = "source.package_dimensions + pricing_rules.package_estimation"
    result["confidence"] = min(int(result["confidence"]), int(package_rules["confidence_cap"]))
    result["validation"] = {
        "status": "corrected",
        "original_value": {
            key: product_dimensions[key] for key in ("length", "width", "height")
        },
        "corrected_value": corrected,
        "reason": "estimated product dimensions reduced to remain below confirmed package dimensions",
    }
    return result


def estimate_dimensions(source: Dict[str, Any], analysis: Dict[str, Any], profiles: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Backward-compatible alias for callers that need product dimensions."""
    return estimate_product_dimensions(source, analysis, profiles)
