#!/usr/bin/env python3
"""Merge current workbench product facts with fixed precedence.

This is not a commercial content generator.  It produces one deterministic
facts snapshot for the current product so later steps do not re-scan old
outputs, cross-product data or low-priority estimates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

try:
    from pricing_engine.source_measurements import source_sku_measurements
    from pricing_engine.weight_estimator import estimate_sku_weights_from_dimensions
except ModuleNotFoundError:
    import sys
    ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT_FOR_IMPORT / "pricing-engine"))
    from pricing_engine.source_measurements import source_sku_measurements
    from pricing_engine.weight_estimator import estimate_sku_weights_from_dimensions

ROOT = Path(__file__).resolve().parents[1]
MERGER_VERSION = "product-fact-merger-v5-visual-measurement-precedence"
FACT_LOCK_VERSION = "product-fact-lock-v1-final"
DEFAULT_PACKAGE_PADDING_MM = 10
DEFAULT_PACKAGE_WEIGHT_PADDING_G = 300
CAPACITY_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|мл|毫升|l|л|литр|литра|升)(?![A-Za-zА-Яа-яЁё])", re.I)
JIN_CAPACITY_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*斤")
CONTAINER_CAPACITY_HINTS = ("容器", "罐", "桶", "瓶", "缸", "壶", "坛")
COLOR_TOKEN_PATTERN = re.compile(
    r"(透明|透色|白色|黑色|灰色|枪灰|银色|银灰|电镀|铬色|金色|玫瑰金|红色|粉色|蓝色|绿色|黄色|紫色|橙色|棕色|咖色|米色|卡其|"
    r"прозрачн\w*|бел\w*|черн\w*|чёрн\w*|сер\w*|графит\w*|серебр\w*|хром\w*|золот\w*|красн\w*|розов\w*|син\w*|зелен\w*|зелён\w*|желт\w*|жёлт\w*|фиолет\w*|оранж\w*|коричнев\w*|бежев\w*|хаки)",
    re.I,
)


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(path)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fffа-яё]", "", str(value or "").casefold())


def selected_skus(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    skus = list(source.get("skus") or [])
    explicitly_selected = [
        sku for sku in skus
        if sku.get("selected") is True or sku.get("is_selected") is True
        or str(sku.get("selection_status") or "").casefold() in {"selected", "chosen"}
    ]
    return explicitly_selected or skus


def sku_image_path(sku: Dict[str, Any]) -> str:
    """Return the current product's local SKU reference image when captured."""
    source_data = sku.get("source_data") or {}
    for key in (
        "variant_local_image_path",
        "local_image_path",
        "image_path",
        "sku_image_path",
        "image_local_path",
    ):
        value = str(sku.get(key) or source_data.get(key) or "").strip()
        if value and value.casefold() != "unknown":
            return value
    return "unknown"


def source_attr_items(source: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for item in source.get("product_attributes") or []:
        if isinstance(item, dict):
            yield item


def useful_source_value(item: Dict[str, Any]) -> str:
    """Return a value even when item.source is temporarily unknown.

    Old code sometimes discarded rows when ``source`` said ``unknown`` even
    though ``value_cn`` or ``source_text`` carried the captured fact.  This
    function deliberately treats the text fields as the evidence.
    """
    for key in ("value_cn", "value", "source_text", "raw_text"):
        value = str(item.get(key) or "").strip()
        if value and value.casefold() != "unknown":
            return value
    return ""


def explicit_attrs(source: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in source_attr_items(source):
        name = str(item.get("name_cn") or item.get("name") or "").strip()
        value = useful_source_value(item)
        if not name or not value:
            continue
        result[normalize_key(name)] = {
            "name": name,
            "value": value,
            "value_cn": item.get("value_cn"),
            "source_text": item.get("source_text"),
            "source_ref": "input/source.json.product_attributes",
            "precedence": 3,
        }
    return result


def sku_match_keys(sku: Dict[str, Any]) -> set[str]:
    values = {sku.get("sku_id"), sku.get("sku_name"), sku.get("name")}
    for item in sku.get("option_values") or []:
        values.add(item.get("value_cn"))
        values.add(item.get("source_text"))
        values.add(item.get("value"))
    return {normalize_key(value) for value in values if normalize_key(value)}


def sku_measurement_for(sku: Dict[str, Any], measurements: Dict[str, Dict[str, Any]]) -> Dict[str, Any] | None:
    sku_id = str(sku.get("sku_id") or "")
    if sku_id in measurements:
        return measurements[sku_id]
    keys = sku_match_keys(sku)
    for value in measurements.values():
        label = normalize_key(value.get("variant_label"))
        if label and any(label == key or label in key or key in label for key in keys):
            return value
    return None


def _normalized_product_image_ref(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    marker = "/input/"
    if marker in text:
        return f"input/{text.split(marker, 1)[1]}"
    if text.startswith("input/"):
        return text
    return text.lstrip("/")


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _dimension_triplet_from_any(value: Any) -> Tuple[Any, Any, Any] | None:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return value[0], value[1], value[2]
    text = str(value or "").strip()
    if not text:
        return None
    parts = re.findall(r"\d+(?:[.,]\d+)?", text)
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    return None


def _dimension_values_from_item(item: Dict[str, Any]) -> Tuple[float, float, float] | None:
    length = item.get("length_cm") or item.get("length")
    width = item.get("width_cm") or item.get("width")
    height = item.get("height_cm") or item.get("height")
    if not (length and width and height):
        triplet = (
            _dimension_triplet_from_any(item.get("external_cm"))
            or _dimension_triplet_from_any(item.get("dimensions_cm"))
            or _dimension_triplet_from_any(item.get("value_cm"))
            or _dimension_triplet_from_any(item.get("value"))
        )
        if triplet:
            length, width, height = triplet
    parsed = tuple(_float_or_none(value) for value in (length, width, height))
    if all(value is not None for value in parsed):
        return parsed  # type: ignore[return-value]
    diameter = max(
        (
            _float_or_none(item.get("top_diameter_cm")) or 0,
            _float_or_none(item.get("bottom_diameter_cm")) or 0,
            _float_or_none(item.get("diameter_cm")) or 0,
        )
    )
    height_value = _float_or_none(height)
    if diameter > 0 and height_value:
        return diameter, diameter, height_value
    return None


def _analysis_dimension_candidates(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = analysis.get("facts") or {}
    dimensions = facts.get("dimensions") or {}
    if not isinstance(dimensions, dict) or dimensions.get("status") not in {"source_image_text", "source_fact"}:
        return []
    candidates = [
        item for key, item in dimensions.items()
        if key not in {"status", "by_visible_variant"} and isinstance(item, dict)
    ]
    candidates.extend(
        item for item in (dimensions.get("by_visible_variant") or [])
        if isinstance(item, dict)
    )
    return candidates


def _analysis_weight_candidates(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = analysis.get("facts") or {}
    weights = facts.get("weight") or {}
    if not isinstance(weights, dict) or weights.get("status") not in {"source_image_text", "source_fact"}:
        return []
    candidates = [
        item for key, item in weights.items()
        if key not in {"status", "by_visible_variant"} and isinstance(item, dict)
    ]
    candidates.extend(
        item for item in (weights.get("by_visible_variant") or [])
        if isinstance(item, dict)
    )
    return candidates


def _analysis_evidence_refs(item: Dict[str, Any]) -> set[str]:
    return {
        _normalized_product_image_ref(value)
        for value in [*(item.get("evidence") or []), item.get("source_ref")]
        if _normalized_product_image_ref(value)
    }


def _visual_measurement_from_items(
    dimension_item: Dict[str, Any] | None,
    weight_item: Dict[str, Any] | None,
    *,
    method: str,
    confidence: int,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if isinstance(dimension_item, dict):
        values = _dimension_values_from_item(dimension_item)
        if values:
            result.update({
                "length": values[0],
                "width": values[1],
                "height": values[2],
                "dimension_source_ref": "output/product-analysis.json.facts.dimensions",
                "dimension_precedence": 4,
                "dimension_estimated": False,
                "dimension_confidence": confidence,
                "dimension_mapping_method": method,
            })
    if isinstance(weight_item, dict):
        weight = _ceil_or_none(weight_item.get("value_g") or weight_item.get("weight_g") or weight_item.get("value"))
        if weight:
            result.update({
                "weight_g": weight,
                "weight_source_ref": "output/product-analysis.json.facts.weight",
                "weight_precedence": 4,
                "weight_estimated": False,
                "weight_confidence": confidence,
                "weight_mapping_method": method,
            })
    return result


def analysis_sku_measurement(sku: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Use source-image text when it points at this exact SKU image or label."""
    sku_refs = {
        _normalized_product_image_ref(value)
        for value in (
            sku.get("local_image_path"),
            sku.get("variant_local_image_path"),
            sku.get("image_path"),
            (sku.get("source_data") or {}).get("local_image_path"),
        )
        if _normalized_product_image_ref(value)
    }
    keys = sku_match_keys(sku)

    def matches_evidence(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        evidence = _analysis_evidence_refs(item)
        return bool(sku_refs & evidence)

    def matches_label(item: Any) -> bool:
        if not isinstance(item, dict) or not keys:
            return False
        labels = {
            normalize_key(item.get(key))
            for key in (
                "sku_id", "sku_name", "variant_label", "label", "option_text",
                "capacity", "capacity_label", "color", "specification",
            )
            if normalize_key(item.get(key))
        }
        return any(label == key or label in key or key in label for label in labels for key in keys)

    dimension_item = next(
        (item for item in _analysis_dimension_candidates(analysis) if matches_evidence(item) or matches_label(item)),
        None,
    )
    weight_item = next(
        (item for item in _analysis_weight_candidates(analysis) if matches_evidence(item) or matches_label(item)),
        None,
    )
    return _visual_measurement_from_items(
        dimension_item,
        weight_item,
        method="source_image_text_exact_sku",
        confidence=90,
    )


def analysis_common_measurement(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Use a single common visual measurement from main/detail images as fallback.

    This is deliberately conservative: if visual analysis reports several
    different dimensions without a SKU match, we do not spread one variant's
    size across every SKU.
    """
    dimension_items = [
        item for item in _analysis_dimension_candidates(analysis)
        if _dimension_values_from_item(item)
    ]
    common_dimension_item = None
    if dimension_items:
        unique_dimensions = {
            tuple(round(value, 3) for value in (_dimension_values_from_item(item) or ()))
            for item in dimension_items
        }
        if len(unique_dimensions) == 1:
            common_dimension_item = dimension_items[0]
        elif len(dimension_items) == 1:
            common_dimension_item = dimension_items[0]
    weight_items = [
        item for item in _analysis_weight_candidates(analysis)
        if _ceil_or_none(item.get("value_g") or item.get("weight_g") or item.get("value"))
    ]
    common_weight_item = None
    if weight_items:
        unique_weights = {
            _ceil_or_none(item.get("value_g") or item.get("weight_g") or item.get("value"))
            for item in weight_items
        }
        if len(unique_weights) == 1:
            common_weight_item = weight_items[0]
        elif len(weight_items) == 1:
            common_weight_item = weight_items[0]
    return _visual_measurement_from_items(
        common_dimension_item,
        common_weight_item,
        method="source_image_text_common_product",
        confidence=84,
    )


def merge_visual_measurement_over_estimate(
    measurement: Dict[str, Any],
    visual_measurement: Dict[str, Any],
) -> Dict[str, Any]:
    """Let source-image/OCR measurements beat title/spec estimates.

    Manual or structured measurement rows remain stronger.  This only fixes the
    fallback order when an earlier estimate already filled the field and would
    otherwise block source-image evidence from being used.
    """
    if not visual_measurement:
        return measurement
    result = dict(measurement)
    current_dimension_estimated = result.get("dimension_estimated", result.get("estimated")) is True
    has_current_dimensions = all(result.get(key) not in {None, "", "unknown"} for key in ("length", "width", "height"))
    has_visual_dimensions = all(
        visual_measurement.get(key) not in {None, "", "unknown"}
        for key in ("length", "width", "height")
    )
    if has_visual_dimensions and (not has_current_dimensions or current_dimension_estimated):
        for key in (
            "length", "width", "height", "dimension_source_ref", "dimension_precedence",
            "dimension_estimated", "dimension_confidence", "dimension_mapping_method",
        ):
            if key in visual_measurement:
                result[key] = visual_measurement[key]
    current_weight_estimated = result.get("weight_estimated", result.get("estimated")) is True
    has_current_weight = result.get("weight_g") not in {None, "", "unknown"}
    if visual_measurement.get("weight_g") not in {None, "", "unknown"} and (not has_current_weight or current_weight_estimated):
        for key in (
            "weight_g", "weight_source_ref", "weight_precedence",
            "weight_estimated", "weight_confidence", "weight_mapping_method",
        ):
            if key in visual_measurement:
                result[key] = visual_measurement[key]
    return result


def _ceil_or_none(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return int(math.ceil(number))


def canonical_field(value: Any, unit: str, source: str, precedence: int, *, mapping_method: str = "direct", confidence: float = 1.0, source_ref: str = "") -> Dict[str, Any]:
    return {
        "canonical_value": value,
        "canonical_unit": unit,
        "source": source,
        "precedence": precedence,
        "mapping_method": mapping_method,
        "confidence": confidence,
        "source_ref": source_ref or source,
    }


def option_text_for_sku(sku: Dict[str, Any]) -> str:
    parts: List[str] = []
    for item in sku.get("option_values") or []:
        if isinstance(item, dict):
            name = str(item.get("name_cn") or item.get("prop_name") or item.get("name") or "").strip()
            value = str(item.get("value_cn") or item.get("source_text") or item.get("value") or "").strip()
            parts.append(" ".join(part for part in (name, value) if part))
        elif str(item).strip():
            parts.append(str(item).strip())
    return " / ".join(parts)


def extract_color(sku: Dict[str, Any]) -> str | None:
    color_keys = ("颜色", "цвет", "color")
    fallback_values: List[str] = []
    for item in sku.get("option_values") or []:
        if not isinstance(item, dict):
            if str(item).strip():
                fallback_values.append(str(item).strip())
            continue
        name = str(item.get("name_cn") or item.get("prop_name") or item.get("name") or "").casefold()
        value = str(item.get("value_cn") or item.get("source_text") or item.get("value") or "").strip()
        if value and any(key in name for key in color_keys):
            return value
        if value:
            fallback_values.append(value)
    for value in (sku.get("sku_name"), sku.get("name"), *fallback_values):
        match = COLOR_TOKEN_PATTERN.search(str(value or ""))
        if match:
            return match.group(0)
    return None


def extract_capacity_ml(*values: Any, allow_nominal_jin: bool = False) -> int | None:
    for value in values:
        text = str(value or "")
        match = CAPACITY_PATTERN.search(text)
        if not match:
            continue
        number = float(match.group(1).replace(",", "."))
        unit = match.group(2).casefold()
        if unit in {"l", "л", "литр", "литра", "升"}:
            number *= 1000
        if math.isfinite(number) and number > 0:
            return int(math.ceil(number))
    if allow_nominal_jin:
        for value in values:
            match = JIN_CAPACITY_PATTERN.search(str(value or ""))
            if not match:
                continue
            # Chinese container specifications commonly express nominal water
            # capacity in jin: 1 jin of water equals 500 ml.
            number = float(match.group(1).replace(",", ".")) * 500
            if math.isfinite(number) and number > 0:
                return int(math.ceil(number))
    return None


def cm_to_mm(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return round(number * 10, 3)


def dimensions_field(length_mm: Any, width_mm: Any, height_mm: Any, source: str, precedence: int) -> Dict[str, Any] | None:
    values = {}
    for key, raw in (("length_mm", length_mm), ("width_mm", width_mm), ("height_mm", height_mm)):
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        values[key] = round(number, 3)
    return canonical_field(values, "mm", source, precedence, source_ref=source)


def load_workbench_sku_overrides(product_dir: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    path = product_dir / "input/workbench-sku-overrides.json"
    if not path.is_file():
        return {}
    try:
        raw = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for item in raw.get("overrides") or []:
        if not isinstance(item, dict):
            continue
        sku_id = str(item.get("sku_id") or "").strip()
        field_name = str(item.get("field_name") or "").strip()
        if not sku_id or not field_name:
            continue
        result.setdefault(sku_id, {})[field_name] = item
    return result


def _current_dimension_value(row: Dict[str, Any], group_name: str) -> Dict[str, Any]:
    current = ((row.get(group_name) or {}).get("canonical_value") or {})
    return dict(current) if isinstance(current, dict) else {}


def _dimension_override_field(values: Dict[str, Any], source: str, precedence: int) -> Dict[str, Any] | None:
    cleaned: Dict[str, float] = {}
    for axis in ("length_mm", "width_mm", "height_mm"):
        if axis not in values:
            continue
        try:
            number = float(values[axis])
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            cleaned[axis] = round(number, 3)
    if not cleaned:
        return None
    if all(axis in cleaned for axis in ("length_mm", "width_mm", "height_mm")):
        return dimensions_field(
            cleaned["length_mm"], cleaned["width_mm"], cleaned["height_mm"], source, precedence
        )
    return canonical_field(cleaned, "mm", source, precedence, source_ref=source)


def extract_specification_text(sku: Dict[str, Any], color: str | None, capacity_ml: int | None) -> str | None:
    values: List[str] = []
    normalized_color = normalize_key(color)
    for item in sku.get("option_values") or []:
        if isinstance(item, dict):
            raw = str(item.get("value_cn") or item.get("source_text") or item.get("value") or "").strip()
        else:
            raw = str(item or "").strip()
        if not raw:
            continue
        if normalized_color and normalize_key(raw) == normalized_color:
            continue
        if capacity_ml and extract_capacity_ml(raw) == capacity_ml:
            continue
        values.append(raw)
    return " / ".join(dict.fromkeys(values)) or None


def apply_sku_overrides(row: Dict[str, Any], overrides: Dict[str, Dict[str, Any]]) -> None:
    dimension_updates: Dict[str, Dict[str, Any]] = {
        "product_dimensions": _current_dimension_value(row, "product_dimensions"),
        "package_dimensions": _current_dimension_value(row, "package_dimensions"),
    }
    dimension_changed = {"product_dimensions": False, "package_dimensions": False}
    for field_name, item in overrides.items():
        value = item.get("canonical_value")
        unit = str(item.get("canonical_unit") or "").strip() or "unknown"
        field = canonical_field(
            value,
            unit,
            "human_override",
            1,
            mapping_method="manual_workbench_edit",
            confidence=1.0,
            source_ref="input/workbench-sku-overrides.json",
        )
        if field_name in {"color", "capacity_ml", "specification_text", "product_weight_g", "package_weight_g", "quantity_pcs"}:
            target_name = {
                "capacity_ml": "capacity",
                "specification_text": "specification",
                "product_weight_g": "product_weight",
                "package_weight_g": "package_weight",
                "quantity_pcs": "quantity",
            }.get(field_name, field_name)
            row[target_name] = field
        elif field_name in {"product_length_mm", "product_width_mm", "product_height_mm", "package_length_mm", "package_width_mm", "package_height_mm"}:
            group_name = "product_dimensions" if field_name.startswith("product_") else "package_dimensions"
            axis = field_name.removeprefix("product_").removeprefix("package_")
            dimension_updates[group_name][axis] = value
            dimension_changed[group_name] = True
        elif field_name.startswith("attribute:"):
            row.setdefault("dynamic_attributes", {})[field_name.removeprefix("attribute:")] = field
    for group_name, changed in dimension_changed.items():
        if not changed:
            continue
        field = _dimension_override_field(
            dimension_updates[group_name],
            "input/workbench-sku-overrides.json",
            1,
        )
        if field:
            row[group_name] = field


def ensure_package_hierarchy(row: Dict[str, Any]) -> None:
    product_dims = ((row.get("product_dimensions") or {}).get("canonical_value") or {})
    package_dims = ((row.get("package_dimensions") or {}).get("canonical_value") or {})
    if not isinstance(product_dims, dict):
        product_dims = {}
    if not isinstance(package_dims, dict):
        package_dims = {}
    if product_dims:
        corrected = {}
        changed = False
        for key in ("length_mm", "width_mm", "height_mm"):
            product_value = float(product_dims.get(key) or 0)
            package_value = float(package_dims.get(key) or 0)
            if product_value <= 0:
                continue
            if package_value <= product_value:
                package_value = product_value + DEFAULT_PACKAGE_PADDING_MM
                changed = True
            corrected[key] = round(package_value, 3)
        if len(corrected) == 3 and (not package_dims or changed):
            row["package_dimensions"] = canonical_field(
                corrected, "mm", "auto_corrected_package_hierarchy", 6,
                mapping_method="package_dimensions_gt_product_dimensions",
                confidence=0.8,
                source_ref="merged-product-facts.package_hierarchy",
            )
    product_weight = ((row.get("product_weight") or {}).get("canonical_value"))
    package_weight = ((row.get("package_weight") or {}).get("canonical_value"))
    try:
        product_weight_number = float(product_weight)
    except (TypeError, ValueError):
        product_weight_number = 0.0
    try:
        package_weight_number = float(package_weight)
    except (TypeError, ValueError):
        package_weight_number = 0.0
    target_package_weight = int(math.ceil(product_weight_number + DEFAULT_PACKAGE_WEIGHT_PADDING_G))
    if product_weight_number > 0 and int(math.ceil(package_weight_number)) != target_package_weight:
        row["package_weight"] = canonical_field(
            target_package_weight,
            "g",
            "auto_corrected_package_hierarchy",
            6,
            mapping_method="product_weight_plus_packaging_allowance",
            confidence=0.8,
            source_ref="merged-product-facts.package_hierarchy",
        )


def apply_sku_dimension_weight_estimates(sku_rows: List[Dict[str, Any]], cost: Dict[str, Any]) -> None:
    base = cost.get("product_weight") or {}
    if base.get("estimated") is not True:
        return
    base_weight = _ceil_or_none(base.get("value"))
    if not base_weight:
        return
    variants = []
    for row in sku_rows:
        dimensions = ((row.get("product_dimensions") or {}).get("canonical_value") or {})
        variants.append({
            "sku_id": row.get("sku_id"),
            "label": " ".join((str(row.get("sku_name") or ""), str(row.get("option_text") or ""))),
            "dimensions_mm": dimensions if isinstance(dimensions, dict) else {},
        })
    estimates = estimate_sku_weights_from_dimensions(variants, base_weight)
    confidence = min(0.68, max(0.0, float(base.get("confidence") or 0) / 100.0))
    for row in sku_rows:
        if _ceil_or_none((row.get("product_weight") or {}).get("canonical_value")):
            continue
        value = estimates.get(str(row.get("sku_id") or ""))
        if not value:
            continue
        row["product_weight"] = canonical_field(
            value,
            "g",
            "AI_estimated",
            7,
            mapping_method="sku_dimension_scaled_estimate",
            confidence=confidence,
            source_ref="output/cost-analysis.json.product_weight+input/source.json.skus",
        )


def merge_product_facts(product_dir: Path) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    source_path = product_dir / "input/source.json"
    if not source_path.is_file():
        raise FileNotFoundError(f"missing {source_path}")
    source = load_json(source_path)
    category_path = product_dir / "input/category-selection.json"
    category = load_json(category_path) if category_path.is_file() else load_json(product_dir / "output/ozon-category.json")
    output = product_dir / "output"
    analysis = load_json(output / "product-analysis.json") if (output / "product-analysis.json").is_file() else {}
    cost = load_json(output / "cost-analysis.json") if (output / "cost-analysis.json").is_file() else {}
    measurements = source_sku_measurements(source)
    common_image_measurement = analysis_common_measurement(analysis)
    sku_overrides = load_workbench_sku_overrides(product_dir)
    selected = selected_skus(source)
    selected_ids = [str(sku.get("sku_id") or "") for sku in selected]
    sku_facts: List[Dict[str, Any]] = []
    sku_rows: List[Dict[str, Any]] = []
    max_axes = {"length_mm": 0.0, "width_mm": 0.0, "height_mm": 0.0}
    max_axes_include_estimate = False
    max_axes_precedence = 2
    max_weight = 0
    max_weight_precedence = 2
    max_weight_source = "selected_sku_measurement_table_max_weight"
    for sku in selected:
        sku_id = str(sku.get("sku_id") or "")
        measurement = dict(sku_measurement_for(sku, measurements) or {})
        image_measurement = analysis_sku_measurement(sku, analysis) or common_image_measurement
        measurement = merge_visual_measurement_over_estimate(measurement, image_measurement)
        option_text = option_text_for_sku(sku)
        dimensions = None
        product_dimensions = None
        if all(measurement.get(key) for key in ("length", "width", "height")):
            measurement_estimated = measurement.get("dimension_estimated", measurement.get("estimated")) is True
            measurement_precedence = int(
                measurement.get("dimension_precedence") or (7 if measurement_estimated else 2)
            )
            confidence_value = float(
                measurement.get("dimension_confidence", measurement.get("confidence", 82 if measurement_estimated else 100))
            )
            measurement_confidence = confidence_value / 100.0 if confidence_value > 1 else confidence_value
            measurement_mapping = str(
                measurement.get("dimension_mapping_method")
                or ("sku_label_dimension_estimate" if measurement_estimated else "direct")
            )
            dimension_source_ref = str(
                measurement.get("dimension_source_ref")
                or measurement.get("source_ref")
                or "input/source.json.sku_measurement_table"
            )
            length_mm = cm_to_mm(measurement["length"])
            width_mm = cm_to_mm(measurement["width"])
            height_mm = cm_to_mm(measurement["height"])
            dimensions = {
                "length_cm": float(measurement["length"]),
                "width_cm": float(measurement["width"]),
                "height_cm": float(measurement["height"]),
                "length_mm": length_mm,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "source": dimension_source_ref,
                "precedence": measurement_precedence,
                "estimated": measurement_estimated,
                "confidence": measurement_confidence,
            }
            product_dimensions = dimensions_field(
                length_mm, width_mm, height_mm,
                dimension_source_ref,
                measurement_precedence,
            )
            product_dimensions["mapping_method"] = measurement_mapping
            product_dimensions["confidence"] = measurement_confidence
            max_axes_include_estimate = max_axes_include_estimate or measurement_estimated
            max_axes_precedence = max(max_axes_precedence, measurement_precedence)
            for axis, source_axis in (
                ("length_mm", "length"),
                ("width_mm", "width"),
                ("height_mm", "height"),
            ):
                max_axes[axis] = max(max_axes[axis], float(cm_to_mm(measurement[source_axis]) or 0))
        weight_g = _ceil_or_none(measurement.get("weight_g"))
        weight_estimated = measurement.get("weight_estimated", measurement.get("estimated")) is True
        weight_precedence = int(measurement.get("weight_precedence") or (7 if weight_estimated else 2))
        weight_source_ref = str(
            measurement.get("weight_source_ref") or measurement.get("source_ref") or "unknown"
        )
        weight_confidence_value = float(
            measurement.get("weight_confidence", measurement.get("confidence", 82 if weight_estimated else 100))
        )
        weight_confidence = weight_confidence_value / 100.0 if weight_confidence_value > 1 else weight_confidence_value
        weight_mapping_method = str(
            measurement.get("weight_mapping_method")
            or ("sku_label_weight_estimate" if weight_estimated else "direct")
        )
        if weight_g:
            if weight_g >= max_weight:
                max_weight = weight_g
                max_weight_precedence = weight_precedence
                max_weight_source = weight_source_ref
        color = extract_color(sku)
        product_title = " ".join(
            str(source.get(key) or "") for key in ("title_cn", "title", "product_name")
        )
        allow_nominal_jin = any(hint in product_title for hint in CONTAINER_CAPACITY_HINTS)
        capacity_ml = extract_capacity_ml(
            sku.get("sku_name"), option_text, allow_nominal_jin=allow_nominal_jin
        )
        specification_text = extract_specification_text(sku, color, capacity_ml)
        row = {
            "sku_id": sku_id,
            "sku_name": sku.get("sku_name") or sku.get("name") or sku_id,
            "option_text": option_text,
            "image_path": sku_image_path(sku),
            "color": canonical_field(
                color or "unknown",
                "text",
                "input/source.json.sku_options" if color else "unknown",
                3 if color else 99,
                source_ref="input/source.json.skus.option_values",
            ),
            "capacity": canonical_field(
                capacity_ml if capacity_ml else "unknown",
                "ml",
                "input/source.json.sku_options" if capacity_ml else "unknown",
                3 if capacity_ml else 99,
                source_ref="input/source.json.skus",
            ),
            "specification": canonical_field(
                specification_text or "unknown",
                "text",
                "input/source.json.sku_options" if specification_text else "unknown",
                3 if specification_text else 99,
                source_ref="input/source.json.skus.option_values",
            ),
            "product_weight": canonical_field(
                weight_g if weight_g else "unknown",
                "g",
                weight_source_ref,
                weight_precedence if weight_g else 99,
                mapping_method=weight_mapping_method if weight_g else "direct",
                confidence=weight_confidence if weight_g else 0.0,
                source_ref=weight_source_ref,
            ),
            "product_dimensions": product_dimensions or canonical_field(
                "unknown", "mm", "unknown", 99, source_ref="missing_sku_measurement_table",
            ),
            "package_weight": canonical_field(
                "unknown", "g", "unknown", 99, source_ref="missing_package_weight",
            ),
            "package_dimensions": canonical_field(
                "unknown", "mm", "unknown", 99, source_ref="missing_package_dimensions",
            ),
            "quantity": canonical_field(1, "pcs", "project_default", 7, source_ref="project_defaults.quantity"),
            "dynamic_attributes": {},
        }
        apply_sku_overrides(row, sku_overrides.get(sku_id) or {})
        sku_rows.append(row)
        sku_facts.append({
            "sku_id": sku_id,
            "sku_name": sku.get("sku_name") or sku.get("name") or sku_id,
            "option_values": sku.get("option_values") or [],
            "image_path": sku_image_path(sku),
            "sku_row": row,
            "measurement": {
                "dimensions_cm": dimensions,
                "weight_g": {
                    "value": weight_g,
                    "source": weight_source_ref,
                    "precedence": weight_precedence,
                } if weight_g else None,
                "variant_label": measurement.get("variant_label"),
            },
        })
    apply_sku_dimension_weight_estimates(sku_rows, cost)
    for row in sku_rows:
        ensure_package_hierarchy(row)
    product_dimensions = None
    if all(value > 0 for value in max_axes.values()):
        product_dimensions = {
            "length_mm": max_axes["length_mm"],
            "width_mm": max_axes["width_mm"],
            "height_mm": max_axes["height_mm"],
            "length_cm": round(max_axes["length_mm"] / 10, 3),
            "width_cm": round(max_axes["width_mm"] / 10, 3),
            "height_cm": round(max_axes["height_mm"] / 10, 3),
            "source": "selected_sku_dimension_max_axes",
            "precedence": max_axes_precedence,
            "estimated": max_axes_include_estimate,
        }
    elif isinstance(cost.get("product_dimensions"), dict):
        dims = cost["product_dimensions"]
        if all(dims.get(key) for key in ("length", "width", "height")):
            product_dimensions = {
                "length_mm": cm_to_mm(dims["length"]),
                "width_mm": cm_to_mm(dims["width"]),
                "height_mm": cm_to_mm(dims["height"]),
                "length_cm": float(dims["length"]),
                "width_cm": float(dims["width"]),
                "height_cm": float(dims["height"]),
                "source": dims.get("source_ref") or "output/cost-analysis.json",
                "precedence": 7 if dims.get("estimated") else 1,
            }
    product_weight = None
    if max_weight:
        product_weight = {
            "value_g": max_weight,
            "source": max_weight_source,
            "precedence": max_weight_precedence,
        }
    elif isinstance(cost.get("product_weight"), dict) and cost["product_weight"].get("value"):
        product_weight = {
            "value_g": _ceil_or_none(cost["product_weight"]["value"]),
            "source": cost["product_weight"].get("source_ref") or "output/cost-analysis.json",
            "precedence": 7 if cost["product_weight"].get("estimated") else 1,
        }
    dependencies = {
        "source_json_sha256": file_sha256(source_path),
        "workbench_sku_overrides_sha256": file_sha256(product_dir / "input/workbench-sku-overrides.json")
        if (product_dir / "input/workbench-sku-overrides.json").is_file() else "",
        "selected_sku_hash": sha256_json(selected_ids),
        "category_hash": sha256_json({
            "category_id": category.get("category_id"),
            "type_id": category.get("type_id"),
        }),
        "analysis_hash": sha256_json(analysis) if analysis else "",
        "cost_hash": sha256_json(cost) if cost else "",
        "merger_version": MERGER_VERSION,
    }
    result = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "collection_id": str(source.get("collection_id") or ""),
        "source_kind": source.get("source_kind") or "workbench_collection",
        "generated_at": now(),
        "precedence_order": [
            "user_batch_sku_category_store_supplements",
            "selected_sku_structured_specs_and_measurement_table",
            "1688_structured_product_attributes",
            "1688_title_and_detail_text",
            "current_product_real_images",
            "ai_semantic_supplements",
            "project_defaults_and_measurement_estimates",
        ],
        "dependencies": dependencies,
        "dependency_hash": sha256_json(dependencies),
        "category": {
            "category_id": int(category.get("category_id") or 0),
            "type_id": int(category.get("type_id") or 0),
            "category_name": category.get("category_name") or category.get("category_name_zh") or "unknown",
            "category_path": category.get("category_path") or category.get("path") or [],
        },
        "selected_skus": sku_facts,
        "sku_rows": sku_rows,
        "facts": {
            "title_cn": source.get("title_cn") or source.get("title") or "",
            "structured_attributes": explicit_attrs(source),
            "product_dimensions": product_dimensions,
            "product_weight": product_weight,
            "sku_rows": sku_rows,
            "analysis_summary": {
                "product_type": analysis.get("product_type"),
                "facts": analysis.get("facts") or {},
                "inferences": analysis.get("inferences") or [],
                "unknowns": analysis.get("unknowns") or [],
            },
        },
    }
    write_json_atomic(output / "merged-product-facts.json", result)
    build_product_fact_lock(product_dir, merged=result)
    return result


def build_product_fact_lock(
    product_dir: Path,
    *,
    merged: Dict[str, Any] | None = None,
    run_snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Write the final fact lock consumed by downstream production steps.

    The lock is a compact contract, not another analyzer.  It tells later
    stages exactly which product/SKU facts are frozen and which high-risk
    claims must stay unknown unless a current-source reference exists.
    """
    product_dir = product_dir.resolve()
    output = product_dir / "output"
    snapshot = run_snapshot or {}
    facts = merged or snapshot.get("merged_product_facts") or load_json(output / "merged-product-facts.json")
    fact_source = "output/sku-run-snapshot.json" if snapshot else "output/merged-product-facts.json"
    dependencies = {
        "fact_source": fact_source,
        "merged_facts_hash": facts.get("dependency_hash") or "",
        "sku_run_snapshot_hash": snapshot.get("dependency_hash") or "",
        "source_json_sha256": (facts.get("dependencies") or {}).get("source_json_sha256") or "",
        "selected_sku_hash": (facts.get("dependencies") or {}).get("selected_sku_hash") or "",
        "category_hash": (facts.get("dependencies") or {}).get("category_hash") or "",
        "fact_lock_version": FACT_LOCK_VERSION,
    }
    locked_skus = []
    for row in facts.get("sku_rows") or (facts.get("facts") or {}).get("sku_rows") or []:
        if not isinstance(row, dict):
            continue
        locked_skus.append({
            "sku_id": str(row.get("sku_id") or ""),
            "sku_name": str(row.get("sku_name") or ""),
            "option_text": str(row.get("option_text") or ""),
            "image_path": row.get("image_path") or "unknown",
            "color": row.get("color") or {},
            "capacity": row.get("capacity") or {},
            "specification": row.get("specification") or {},
            "product_dimensions": row.get("product_dimensions") or {},
            "product_weight": row.get("product_weight") or {},
            "package_dimensions": row.get("package_dimensions") or {},
            "package_weight": row.get("package_weight") or {},
            "quantity": row.get("quantity") or {},
        })
    lock = {
        "schema_version": "1.0.0",
        "lock_version": FACT_LOCK_VERSION,
        "product_id": product_dir.name,
        "collection_id": facts.get("collection_id") or "",
        "source_kind": facts.get("source_kind") or "workbench_collection",
        "generated_at": now(),
        "dependencies": dependencies,
        "lock_hash": sha256_json(dependencies),
        "fact_source": fact_source,
        "category": facts.get("category") or {},
        "precedence_order": facts.get("precedence_order") or [],
        "locked_skus": locked_skus,
        "locked_common_facts": {
            "title_cn": (facts.get("facts") or {}).get("title_cn") or "",
            "structured_attributes": (facts.get("facts") or {}).get("structured_attributes") or [],
            "product_dimensions": (facts.get("facts") or {}).get("product_dimensions"),
            "product_weight": (facts.get("facts") or {}).get("product_weight"),
            "analysis_summary": (facts.get("facts") or {}).get("analysis_summary") or {},
        },
        "non_inventable_claims": [
            "brand",
            "material",
            "load_capacity",
            "certification",
            "functions",
            "accessories",
            "included_quantity",
            "exact_parameters",
        ],
        "unknown_policy": (
            "High-risk facts stay unknown unless supported by this collection's "
            "title, structured attributes, selected SKU rows, current input images, "
            "operator guidance or low-risk measurement estimates."
        ),
    }
    write_json_atomic(output / "product-fact-lock.json", lock)
    return lock


def freeze_sku_run_snapshot(
    product_dir: Path,
    *,
    batch_id: str,
    review_mode: str,
    auto_upload: bool,
    target_store_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """Freeze the current editable SKU table for one authorized run.

    The workbench may keep autosaving optional user corrections, but a running
    batch must consume the SKU facts that existed when the user clicked Run.
    """
    merged = merge_product_facts(product_dir)
    snapshot = {
        "schema_version": "1.0.0",
        "product_id": merged["product_id"],
        "collection_id": merged["collection_id"],
        "source_kind": merged["source_kind"],
        "batch_id": batch_id,
        "review_mode": review_mode,
        "auto_upload": bool(auto_upload),
        "target_store_ids": list(dict.fromkeys(target_store_ids or [])),
        "frozen_at": now(),
        "dependency_hash": merged["dependency_hash"],
        "dependencies": merged["dependencies"],
        "selected_sku_count": len(merged.get("selected_skus") or []),
        "sku_rows": merged.get("sku_rows") or [],
        "selected_skus": merged.get("selected_skus") or [],
        "merged_product_facts": merged,
    }
    write_json_atomic(product_dir / "output/sku-run-snapshot.json", snapshot)
    build_product_fact_lock(product_dir, merged=merged, run_snapshot=snapshot)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    args = parser.parse_args()
    result = merge_product_facts(Path(args.product_dir))
    print(json.dumps({
        "product_id": result["product_id"],
        "selected_sku_count": len(result["selected_skus"]),
        "dependency_hash": result["dependency_hash"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
