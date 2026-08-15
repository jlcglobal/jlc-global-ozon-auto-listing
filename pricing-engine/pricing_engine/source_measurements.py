"""Extract SKU-level dimensions and weights from captured 1688 measurement tables."""

from __future__ import annotations

import math
import re
from typing import Any, Dict


DIMENSION_PATTERN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mm|毫米|cm|厘米)?\s*[x×*]\s*"
    r"(\d+(?:[.,]\d+)?)\s*(mm|毫米|cm|厘米)?\s*[x×*]\s*"
    r"(\d+(?:[.,]\d+)?)\s*(mm|毫米|cm|厘米)?",
    re.I,
)

WEIGHT_WITH_UNIT_PATTERN = re.compile(
    r"(?<![0-9.])([0-9]+(?:[.,][0-9]+)?)\s*(kg|公斤|千克|g|克)(?![a-z])",
    re.I,
)

LUGGAGE_SIZE_PATTERN = re.compile(r"(?<!\d)(1[6-9]|2[0-9]|3[0-2])\s*(?:寸|吋|英寸|inch|in\b)", re.I)
LUGGAGE_KEYWORDS = ("行李箱", "拉杆箱", "旅行箱", "登机箱", "托运箱", "皮箱", "suitcase", "luggage")
LUGGAGE_SCALE_KEYWORDS = ("行李秤", "行李称", "luggage scale")
FOLDING_LUGGAGE_KEYWORDS = ("折叠", "可折叠", "fold")

LUGGAGE_NOMINAL_DIMENSIONS_CM = {
    16: (31.0, 20.0, 45.0),
    18: (34.0, 21.0, 49.0),
    20: (39.0, 22.0, 55.0),
    22: (42.0, 24.0, 60.0),
    24: (46.0, 26.0, 66.0),
    26: (50.0, 28.0, 70.0),
    28: (54.0, 30.0, 76.0),
    30: (58.0, 32.0, 82.0),
    32: (62.0, 34.0, 88.0),
}
FOLDING_LUGGAGE_NOMINAL_DIMENSIONS_CM = {
    20: (39.0, 11.0, 55.0),
    24: (46.0, 11.0, 66.0),
}
LUGGAGE_NOMINAL_WEIGHT_G = {
    16: 1800,
    18: 2100,
    20: 2500,
    22: 2900,
    24: 3300,
    26: 3900,
    28: 4500,
    30: 5200,
    32: 6000,
}


def _normalize_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").casefold())


def _number_variants(value: float, original: str | None = None) -> list[str]:
    variants: list[str] = []
    if original:
        variants.append(original.replace(",", "."))
    variants.append(f"{value:g}")
    variants.append(f"{value:.1f}")
    variants.append(f"{value:.2f}")
    variants.append(f"{value:.3f}")
    if float(value).is_integer():
        variants.append(str(int(value)))
    result: list[str] = []
    for item in variants:
        item = item.rstrip("0").rstrip(".") if "." in item else item
        if item and item not in result:
            result.append(item)
    return result


def _parse_dimensions_with_tokens(value: Any) -> tuple[float, float, float, tuple[str, str, str]] | None:
    match = DIMENSION_PATTERN.search(str(value or ""))
    if not match:
        return None
    units = [match.group(index) for index in (2, 4, 6) if match.group(index)]
    if not units:
        return None
    normalized_units = {
        "mm" if unit.casefold() in {"mm", "毫米"} else "cm"
        for unit in units
    }
    if len(normalized_units) != 1:
        return None
    factor = 0.1 if "mm" in normalized_units else 1.0
    raw = tuple(match.group(index).replace(",", ".") for index in (1, 3, 5))
    values = tuple(float(item) * factor for item in raw)
    return values[0], values[1], values[2], raw


def _parse_unitless_sku_dimensions(value: Any) -> tuple[float, float, float, tuple[str, str, str]] | None:
    """Infer centimetres only from a SKU label containing a bare LxWxH triple."""
    match = DIMENSION_PATTERN.search(str(value or ""))
    if not match or any(match.group(index) for index in (2, 4, 6)):
        return None
    raw = tuple(match.group(index).replace(",", ".") for index in (1, 3, 5))
    values = tuple(float(item) for item in raw)
    if not all(math.isfinite(item) and 1 <= item <= 300 for item in values):
        return None
    if sum(item >= 3 for item in values) < 2:
        return None
    return values[0], values[1], values[2], raw


def _sku_label_dimension_measurement(sku: Dict[str, Any]) -> Dict[str, Any] | None:
    text = _sku_text(sku)
    dimensions = _parse_dimensions_with_tokens(text)
    if dimensions:
        return {
            "variant_label": str(sku.get("sku_name") or sku.get("name") or "").strip(),
            "length": dimensions[0],
            "width": dimensions[1],
            "height": dimensions[2],
            "source_ref": "source.skus.option_values_dimensions",
            "estimated": False,
            "confidence": 92,
        }
    dimensions = _parse_unitless_sku_dimensions(text)
    if not dimensions:
        return None
    return {
        "variant_label": str(sku.get("sku_name") or sku.get("name") or "").strip(),
        "length": dimensions[0],
        "width": dimensions[1],
        "height": dimensions[2],
        "source_ref": "source.skus.variant_label_dimension_estimate",
        "estimated": True,
        "confidence": 82,
    }


def _parse_weight_with_unit(value: Any) -> int | None:
    match = WEIGHT_WITH_UNIT_PATTERN.search(str(value or ""))
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    grams = number * 1000 if match.group(2).casefold() in {"kg", "公斤", "千克"} else number
    if not math.isfinite(grams) or grams <= 0:
        return None
    return int(math.ceil(grams))


def _strip_prefix_once(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def _parse_concatenated_table_weight(
    source_text: Any,
    variant_label: str,
    dimensions: tuple[float, float, float, tuple[str, str, str]] | None,
) -> int | None:
    """Recover weights from 1688 rows where cells were glued together.

    Example row text captured from 1688:
    ``加厚型50L不锈钢油桶47.503047669755500``

    We already know dimensions from ``value_cn``.  Removing ``47.50`` + ``30`` +
    ``47`` and the computed volume ``66975`` leaves the row weight ``5500``.
    """
    unit_weight = _parse_weight_with_unit(source_text)
    if unit_weight:
        return unit_weight
    if not dimensions:
        return None
    text = str(source_text or "")
    if variant_label:
        index = text.find(variant_label)
        if index >= 0:
            text = text[index + len(variant_label):]
    numeric = re.sub(r"[^0-9.]", "", text)
    if not numeric:
        return None
    length, width, height, tokens = dimensions
    dimension_prefixes = [tokens[0] + tokens[1] + tokens[2]]
    for first in _number_variants(length, tokens[0]):
        for second in _number_variants(width, tokens[1]):
            for third in _number_variants(height, tokens[2]):
                candidate = first + second + third
                if candidate not in dimension_prefixes:
                    dimension_prefixes.append(candidate)
    rest = numeric
    for prefix in sorted(dimension_prefixes, key=len, reverse=True):
        if rest.startswith(prefix):
            rest = rest[len(prefix):]
            break
    volume = length * width * height
    volume_candidates = [
        f"{volume:.3f}",
        f"{volume:.2f}",
        f"{volume:.1f}",
        f"{volume:g}",
        str(int(round(volume))),
    ]
    for candidate in sorted(set(volume_candidates), key=len, reverse=True):
        candidate = candidate.rstrip("0").rstrip(".") if "." in candidate else candidate
        rest = _strip_prefix_once(rest, candidate)
        if rest != numeric:
            break
    match = re.search(r"([1-9]\d{1,5})$", rest)
    if not match:
        return None
    grams = int(match.group(1))
    if 10 <= grams <= 200000:
        return grams
    return None


def _variant_label_from_attribute_name(name: Any, prefix: str) -> str:
    raw = str(name or "")
    match = re.match(rf"\s*{re.escape(prefix)}\s*[-:：]\s*(.+)\s*$", raw, re.I)
    return match.group(1).strip() if match else ""


def _sku_match_keys(sku: Dict[str, Any]) -> set[str]:
    values = {sku.get("sku_name")}
    for item in sku.get("option_values") or []:
        values.add(item.get("value_cn"))
        values.add(item.get("source_text"))
    return {_normalize_label(value) for value in values if _normalize_label(value)}


def _source_haystack(source: Dict[str, Any]) -> str:
    parts = [source.get("title_cn"), source.get("title")]
    for item in source.get("product_attributes") or []:
        parts.append(item.get("name_cn"))
        parts.append(item.get("value_cn"))
    return " ".join(str(part or "") for part in parts).casefold()


def _is_luggage_source(source: Dict[str, Any]) -> bool:
    haystack = _source_haystack(source)
    if any(keyword.casefold() in haystack for keyword in LUGGAGE_SCALE_KEYWORDS):
        return False
    return any(keyword.casefold() in haystack for keyword in LUGGAGE_KEYWORDS)


def _sku_text(sku: Dict[str, Any]) -> str:
    parts = [sku.get("sku_name"), sku.get("name")]
    for item in sku.get("option_values") or []:
        parts.extend([item.get("name_cn"), item.get("value_cn"), item.get("source_text")])
    return " ".join(str(part or "") for part in parts)


def _luggage_size_inches(sku: Dict[str, Any]) -> int | None:
    matches = [int(match.group(1)) for match in LUGGAGE_SIZE_PATTERN.finditer(_sku_text(sku))]
    return max(matches) if matches else None


def _nominal_luggage_measurement(source: Dict[str, Any], sku: Dict[str, Any]) -> Dict[str, Any] | None:
    if not _is_luggage_source(source):
        return None
    size = _luggage_size_inches(sku)
    if not size:
        return None
    haystack = _source_haystack(source)
    dimension_map = (
        FOLDING_LUGGAGE_NOMINAL_DIMENSIONS_CM
        if any(keyword.casefold() in haystack for keyword in FOLDING_LUGGAGE_KEYWORDS)
        else LUGGAGE_NOMINAL_DIMENSIONS_CM
    )
    dimensions = dimension_map.get(size) or LUGGAGE_NOMINAL_DIMENSIONS_CM.get(size)
    if not dimensions:
        return None
    return {
        "variant_label": f"{size}寸",
        "length": dimensions[0],
        "width": dimensions[1],
        "height": dimensions[2],
        "weight_g": LUGGAGE_NOMINAL_WEIGHT_G.get(size),
        "source_ref": "source.skus.nominal_luggage_size_estimate",
        "estimated": True,
        "confidence": 72,
    }


def _luggage_measurement_conflicts_size(sku: Dict[str, Any], measurement: Dict[str, Any]) -> bool:
    size = _luggage_size_inches(sku)
    if not size:
        return False
    dimensions = [
        float(measurement.get(key) or 0)
        for key in ("length", "width", "height")
    ]
    if not all(value > 0 for value in dimensions):
        return False
    largest_side = max(dimensions)
    minimum_largest_side = {
        16: 35,
        18: 40,
        20: 45,
        22: 50,
        24: 55,
        26: 60,
        28: 65,
        30: 70,
        32: 75,
    }.get(size)
    return bool(minimum_largest_side and largest_side < minimum_largest_side)


def _match_measurement_for_sku(
    measurements: Dict[str, Dict[str, Any]],
    sku: Dict[str, Any],
) -> Dict[str, Any] | None:
    sku_keys = _sku_match_keys(sku)
    for key in sku_keys:
        if key in measurements:
            return measurements[key]
    for key, value in measurements.items():
        if any(key and sku_key and (key in sku_key or sku_key in key) for sku_key in sku_keys):
            return value
    return None


def source_sku_measurements(source: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return matched SKU measurements captured from the 1688 detail page."""
    by_label: Dict[str, Dict[str, Any]] = {}
    for item in source.get("product_attributes") or []:
        name = str(item.get("name_cn") or "")
        normalized_name = _normalize_label(name)
        if normalized_name.startswith(_normalize_label("SKU尺寸")):
            label = _variant_label_from_attribute_name(name, "SKU尺寸")
            dimensions = _parse_dimensions_with_tokens(item.get("value_cn"))
            if not label or not dimensions:
                continue
            by_label[_normalize_label(label)] = {
                "variant_label": label,
                "length": dimensions[0],
                "width": dimensions[1],
                "height": dimensions[2],
                "weight_g": _parse_concatenated_table_weight(item.get("source_text"), label, dimensions)
                or _parse_weight_with_unit(item.get("value_cn")),
                "source_ref": "source.product_attributes.sku_measurement_table",
            }
        elif normalized_name.startswith(_normalize_label("SKU重量")):
            label = _variant_label_from_attribute_name(name, "SKU重量")
            if not label:
                continue
            weight = _parse_weight_with_unit(item.get("value_cn")) or _parse_weight_with_unit(item.get("source_text"))
            if not weight:
                number = re.search(r"([1-9]\d{1,5})", str(item.get("value_cn") or ""))
                weight = int(number.group(1)) if number else None
            if not weight:
                continue
            by_label.setdefault(_normalize_label(label), {
                "variant_label": label,
                "source_ref": "source.product_attributes.sku_measurement_table",
            })["weight_g"] = weight

    matched: Dict[str, Dict[str, Any]] = {}
    for sku in source.get("skus") or []:
        sku_id = str(sku.get("sku_id") or "")
        if not sku_id:
            continue
        measurement = _match_measurement_for_sku(by_label, sku)
        nominal = _nominal_luggage_measurement(source, sku)
        if measurement and nominal and _luggage_measurement_conflicts_size(sku, measurement):
            measurement = nominal
        elif not measurement:
            measurement = nominal or _sku_label_dimension_measurement(sku)
        if measurement:
            matched[sku_id] = dict(measurement)
    return matched


def max_source_sku_dimension_summary(source: Dict[str, Any]) -> Dict[str, Any] | None:
    values = [
        item for item in source_sku_measurements(source).values()
        if all(isinstance(item.get(key), (int, float)) and float(item[key]) > 0 for key in ("length", "width", "height"))
    ]
    if not values:
        return None
    estimated = any(item.get("estimated") is True for item in values)
    source_refs = {
        str(item.get("source_ref") or "").strip()
        for item in values
        if str(item.get("source_ref") or "").strip()
    }
    return {
        "length": max(float(item["length"]) for item in values),
        "width": max(float(item["width"]) for item in values),
        "height": max(float(item["height"]) for item in values),
        "source_ref": next(iter(source_refs)) if len(source_refs) == 1 else "source.skus.mixed_dimension_sources",
        "confidence": min(int(item.get("confidence", 100 if not item.get("estimated") else 72)) for item in values),
        "estimated": estimated,
    }


def max_source_sku_dimensions(source: Dict[str, Any]) -> tuple[float, float, float] | None:
    summary = max_source_sku_dimension_summary(source)
    if not summary:
        return None
    return (summary["length"], summary["width"], summary["height"])


def max_source_sku_weight_summary(source: Dict[str, Any]) -> Dict[str, Any] | None:
    measurements = [
        item for item in source_sku_measurements(source).values()
        if isinstance(item.get("weight_g"), (int, float)) and int(item["weight_g"]) > 0
    ]
    if not measurements:
        return None
    values = [
        int(item["weight_g"]) for item in measurements
    ]
    estimated = any(item.get("estimated") is True for item in measurements)
    return {
        "value_g": max(values),
        "source_ref": (
            "source.skus.nominal_luggage_size_estimate.weight"
            if estimated
            else "source.product_attributes.sku_measurement_table.weight"
        ),
        "confidence": min(int(item.get("confidence", 100 if not item.get("estimated") else 72)) for item in measurements),
        "estimated": estimated,
    }


def max_source_sku_weight(source: Dict[str, Any]) -> int | None:
    summary = max_source_sku_weight_summary(source)
    return int(summary["value_g"]) if summary else None
