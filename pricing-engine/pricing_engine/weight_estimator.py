"""Product and package weight extraction, validation, and estimation."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable


def select_profile(source: Dict[str, Any], analysis: Dict[str, Any], profiles: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    haystack = " ".join([
        str(source.get("title_cn", "")),
        str(analysis.get("product_type", "")),
        str(analysis.get("category", "")),
    ]).casefold()
    fallback = None
    for profile in profiles:
        if profile["name"] == "default":
            fallback = profile
        if any(keyword.casefold() in haystack for keyword in profile["keywords"]):
            return profile
    if fallback is None:
        raise ValueError("measurement_profiles requires a default profile")
    return fallback


def _parse_weight(value: Any) -> float | None:
    match = re.search(
        r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)\s*(kg|公斤|千克|g|克)"
        r"(?![a-z])"
        r"(?!\s*[-_/]?\s*(?:wi[\s-]?fi|lte|网络|通信|版本|版))",
        str(value or ""),
        re.I,
    )
    if not match:
        return None
    number = float(match.group(1))
    return number * 1000 if match.group(2).casefold() in {"kg", "公斤", "千克"} else number


def _source_product_weight(source: Dict[str, Any]) -> float | None:
    names = {"重量", "产品重量", "商品重量", "单品重量", "净重"}
    for item in source.get("product_attributes") or []:
        if str(item.get("name_cn") or "").strip() in names:
            parsed = _parse_weight(item.get("value_cn"))
            if parsed:
                return parsed
    return None


def _sku_weight(source: Dict[str, Any]) -> float | None:
    values = []
    for sku in source.get("skus", []):
        texts = [sku.get("sku_name", "")]
        texts.extend(item.get("value_cn", "") for item in sku.get("option_values", []))
        values.extend(parsed for text in texts if (parsed := _parse_weight(text)))
    return max(values) if values else None


def _validated_product_weight(
    candidate: float,
    source_name: str,
    source_ref: str,
    confidence: int,
    estimated: bool,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    lower = float(profile["weight_g"]["min"])
    upper = float(profile["weight_g"]["max"])
    valid = lower <= candidate <= upper
    corrected = candidate if valid else float(profile["weight_g"]["estimate"])
    if not valid:
        source_name = "estimated"
        source_ref = f"pricing_rules.measurement_profiles.{profile['name']}"
        confidence = int(profile["confidence"])
        estimated = True
    return {
        "value": corrected,
        "unit": "g",
        "source": source_name,
        "source_ref": source_ref,
        "confidence": confidence,
        "estimated": estimated,
        "profile": profile["name"],
        "validation": {
            "status": "valid" if valid else "corrected",
            "original_value": candidate,
            "corrected_value": corrected,
            "reason": "within category profile" if valid else (
                f"{candidate:g}g is outside the {profile['name']} range {lower:g}-{upper:g}g"
            ),
        },
    }


def estimate_product_weight(
    source: Dict[str, Any], analysis: Dict[str, Any], profiles: list[Dict[str, Any]]
) -> Dict[str, Any]:
    """Return product net weight, using a labelled estimate only when facts are absent."""
    profile = select_profile(source, analysis, profiles)
    source_weight = _source_product_weight(source)
    facts = analysis.get("facts", {}).get("weight", {})
    analysis_weight = facts.get("value_g") if isinstance(facts, dict) else None
    sku_weight = _sku_weight(source)
    if source_weight:
        values = (source_weight, "1688", "source.product_attributes.product_weight", 100, False)
    elif isinstance(analysis_weight, (int, float)) and analysis_weight > 0:
        values = (float(analysis_weight), "product_analysis", "product-analysis.facts.weight", 90, False)
    elif sku_weight:
        values = (sku_weight, "sku_specification", "source.skus.option_values", 80, False)
    else:
        values = (
            float(profile["weight_g"]["estimate"]),
            "estimated",
            f"pricing_rules.measurement_profiles.{profile['name']}",
            int(profile["confidence"]),
            True,
        )
    return _validated_product_weight(*values, profile)


def estimate_package_weight(
    source: Dict[str, Any],
    product_weight: Dict[str, Any],
    package_rules: Dict[str, Any],
) -> Dict[str, Any]:
    """Return gross package weight and enforce gross weight > product weight."""
    raw = (source.get("package_weight") or {}).get("value_g")
    product_value = float(product_weight["value"])
    valid_source = isinstance(raw, (int, float)) and float(raw) > product_value
    if valid_source:
        value = float(raw)
        source_name = "1688"
        source_ref = "source.package_weight"
        confidence = 100
        estimated = False
        status = "valid"
        reason = "confirmed package weight is greater than product weight"
    else:
        value = max(
            product_value + float(package_rules["minimum_extra_weight_g"]),
            product_value * float(package_rules["weight_multiplier"]),
        )
        value = round(value, 2)
        source_name = "estimated"
        source_ref = "pricing_rules.package_estimation"
        confidence = min(int(product_weight["confidence"]), int(package_rules["confidence_cap"]))
        estimated = True
        status = "corrected" if isinstance(raw, (int, float)) else "valid"
        reason = (
            "source package weight was not greater than product weight; package weight was corrected"
            if isinstance(raw, (int, float))
            else "package weight estimated with configured packaging allowance"
        )
    return {
        "value": value,
        "unit": "g",
        "source": source_name,
        "source_ref": source_ref,
        "confidence": confidence,
        "estimated": estimated,
        "profile": str(product_weight["profile"]),
        "validation": {
            "status": status,
            "original_value": raw,
            "corrected_value": value,
            "reason": reason,
        },
    }


def fit_estimated_product_weight_to_confirmed_package(
    source: Dict[str, Any], product_weight: Dict[str, Any], package_rules: Dict[str, Any]
) -> Dict[str, Any]:
    """Preserve a confirmed gross weight by shrinking only an estimated net weight."""
    raw = (source.get("package_weight") or {}).get("value_g")
    if not (
        product_weight.get("estimated") is True
        and isinstance(raw, (int, float))
        and float(raw) > 0
        and float(product_weight["value"]) >= float(raw)
    ):
        return product_weight
    package_value = float(raw)
    allowance = min(float(package_rules["minimum_extra_weight_g"]), package_value * 0.2)
    corrected = min(package_value / float(package_rules["weight_multiplier"]), package_value - allowance)
    result = dict(product_weight)
    result["value"] = round(max(0.1, corrected), 2)
    result["source_ref"] = "source.package_weight + pricing_rules.package_estimation"
    result["confidence"] = min(int(result["confidence"]), int(package_rules["confidence_cap"]))
    result["validation"] = {
        "status": "corrected",
        "original_value": product_weight["value"],
        "corrected_value": result["value"],
        "reason": "estimated product weight reduced to remain below confirmed package weight",
    }
    return result


def estimate_weight(source: Dict[str, Any], analysis: Dict[str, Any], profiles: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Backward-compatible alias for callers that need product weight."""
    return estimate_product_weight(source, analysis, profiles)
