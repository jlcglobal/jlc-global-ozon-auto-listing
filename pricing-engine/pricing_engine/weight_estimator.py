"""Product and package weight extraction, validation, and estimation."""

from __future__ import annotations

import re
import math
from typing import Any, Dict, Iterable

from .source_measurements import max_source_sku_weight_summary


DIVIDER_ABSENT_TERMS = ("无隔板", "без перегород", "without divider")
DIVIDER_PRESENT_TERMS = ("带隔板", "含隔板", "有隔板", "с перегород", "with divider")
DIVIDER_COUNT_PATTERN = re.compile(r"(\d+)\s*(?:个\s*)?(?:隔板|перегород)", re.I)


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


def _integer_grams(value: Any, *, minimum: int = 1) -> int:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("weight must be finite")
    return max(minimum, int(math.ceil(number)))


def _variant_divider_factor(label: Any) -> float:
    text = str(label or "").casefold()
    if any(term in text for term in DIVIDER_ABSENT_TERMS):
        return 1.0
    match = DIVIDER_COUNT_PATTERN.search(text)
    if match:
        return 1.0 + min(int(match.group(1)), 4) * 0.08
    if any(term in text for term in DIVIDER_PRESENT_TERMS):
        return 1.08
    return 1.0


def estimate_sku_weights_from_dimensions(
    variants: Iterable[Dict[str, Any]],
    anchor_weight_g: Any,
) -> Dict[str, int]:
    """Scale one estimated product weight across SKU sizes without replacing facts."""
    try:
        anchor = float(anchor_weight_g)
    except (TypeError, ValueError):
        return {}
    if not math.isfinite(anchor) or anchor <= 0:
        return {}

    scores: Dict[str, float] = {}
    signatures = set()
    for variant in variants:
        sku_id = str(variant.get("sku_id") or "").strip()
        dimensions = variant.get("dimensions_mm") or {}
        try:
            length = float(dimensions.get("length_mm"))
            width = float(dimensions.get("width_mm"))
            height = float(dimensions.get("height_mm"))
        except (TypeError, ValueError):
            continue
        if not sku_id or not all(math.isfinite(value) and value > 0 for value in (length, width, height)):
            continue
        factor = _variant_divider_factor(variant.get("label"))
        scores[sku_id] = (length * width + length * height + width * height) * factor
        signatures.add((round(length, 3), round(width, 3), round(height, 3), factor))
    if len(scores) < 2 or len(signatures) < 2:
        return {}

    maximum = max(scores.values())
    if maximum <= 0:
        return {}
    estimates: Dict[str, int] = {}
    for sku_id, score in scores.items():
        ratio = max(0.25, min(1.0, score / maximum))
        estimates[sku_id] = max(10, int(math.ceil(anchor * ratio / 10.0) * 10))
    return estimates


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
    corrected = _integer_grams(candidate if valid else float(profile["weight_g"]["estimate"]))
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
    source_sku_weight = max_source_sku_weight_summary(source)
    facts = analysis.get("facts", {}).get("weight", {})
    analysis_weight = facts.get("value_g") if isinstance(facts, dict) else None
    sku_weight = _sku_weight(source)
    if source_weight:
        values = (source_weight, "1688", "source.product_attributes.product_weight", 100, False)
    elif source_sku_weight:
        values = (
            float(source_sku_weight["value_g"]),
            "estimated" if source_sku_weight.get("estimated") else "1688",
            str(source_sku_weight.get("source_ref") or "source.product_attributes.sku_measurement_table.weight"),
            int(source_sku_weight.get("confidence", 100)),
            bool(source_sku_weight.get("estimated")),
        )
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
    """Return gross package weight as product weight plus the packaging allowance."""
    raw = (source.get("package_weight") or {}).get("value_g")
    product_value = float(product_weight["value"])
    value = _integer_grams(product_value + float(package_rules["minimum_extra_weight_g"]))
    source_name = "estimated"
    source_ref = "pricing_rules.package_estimation"
    confidence = min(int(product_weight["confidence"]), int(package_rules["confidence_cap"]))
    estimated = True
    status = "corrected" if isinstance(raw, (int, float)) and int(math.ceil(float(raw))) != value else "valid"
    reason = (
        "package weight is derived from the recognized product weight plus the configured packaging allowance"
        if not isinstance(raw, (int, float))
        else "source package weight was ignored; package weight is product weight plus the configured packaging allowance"
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
    """Keep product weight independent; package weight is derived after product recognition."""
    return product_weight


def estimate_weight(source: Dict[str, Any], analysis: Dict[str, Any], profiles: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Backward-compatible alias for callers that need product weight."""
    return estimate_product_weight(source, analysis, profiles)
