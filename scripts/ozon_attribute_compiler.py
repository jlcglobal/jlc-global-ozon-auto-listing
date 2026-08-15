#!/usr/bin/env python3
"""Deterministically compile ecommerce-designer attribute decisions for Ozon."""
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

ROOT = Path(__file__).resolve().parents[1]
COMPILER_VERSION = "ozon-attribute-compiler-v7-aspect-size-rank-fallback"
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
LOCAL_PATH_PATTERN = re.compile(r"(?i)(?:^|\\s)(?:/Users/|[A-Z]:\\\\|products/P\\d{6}/|file://)")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
JLC_BRAND_VALUE = "JLC GLOBAL"
NO_BRAND_VALUE = "Нет бренда"
RESIDENTIAL_GARDEN_CATEGORY_TOKENS = {
    "дом",
    "сад",
    "дача",
    "дачи",
    "garden",
    "home",
    "住宅",
    "花园",
    "家居",
    "园艺",
}


class AttributeCompileError(ValueError):
    pass


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def ceil_json_int(value: Any) -> int:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise AttributeCompileError(f"cannot compile integer from {value!r}")
    if not math.isfinite(number):
        raise AttributeCompileError(f"non-finite integer value {value!r}")
    return int(math.ceil(number))


def ceil_grams(value: Any) -> int:
    grams = ceil_json_int(value)
    if grams <= 0:
        raise AttributeCompileError("gram value must be positive")
    return grams


def ceil_mm(value: Any) -> int:
    mm = ceil_json_int(value)
    if mm <= 0:
        raise AttributeCompileError("millimeter value must be positive")
    return mm


def normalize_decimal(value: Any) -> float:
    text = str(value or "").strip().replace(",", ".")
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        raise AttributeCompileError(f"cannot compile decimal from {value!r}")
    number = float(match.group(0))
    if not math.isfinite(number):
        raise AttributeCompileError(f"non-finite decimal value {value!r}")
    return number


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in {"true", "yes", "y", "1", "да", "есть", "是"}:
        return True
    if text in {"false", "no", "n", "0", "нет", "без", "否", "无"}:
        return False
    raise AttributeCompileError(f"cannot compile boolean from {value!r}")


def clean_string(value: Any, *, max_chars: int | None = None, max_bytes: int | None = None) -> str:
    text = CONTROL_PATTERN.sub(" ", str(value or ""))
    text = LOCAL_PATH_PATTERN.sub(" ", text)
    text = CJK_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip()
    if max_bytes:
        while len(text.encode("utf-8")) > max_bytes and text:
            text = text[:-1].rstrip()
    return text or "unknown"


def normalize_attr_name(value: Any) -> str:
    return re.sub(r"[^a-zа-яё0-9]", "", str(value or "").casefold())


def is_brand_attribute(attribute: Dict[str, Any]) -> bool:
    name = normalize_attr_name(attribute.get("attribute_name"))
    try:
        attribute_id = int(attribute.get("attribute_id") or 0)
    except (TypeError, ValueError):
        attribute_id = 0
    return attribute_id == 85 or "бренд" in name or "brand" in name


def _collect_category_text(value: Any, parts: List[str], *, max_items: int = 80) -> None:
    if len(parts) >= max_items:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            parts.append(text)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_category_text(item, parts, max_items=max_items)
            if len(parts) >= max_items:
                return
        return
    if isinstance(value, list):
        for item in value:
            _collect_category_text(item, parts, max_items=max_items)
            if len(parts) >= max_items:
                return


def category_text_for_brand_policy(fill_input: Dict[str, Any] | None) -> str:
    parts: List[str] = []
    if fill_input:
        _collect_category_text(fill_input.get("category"), parts)
        facts = fill_input.get("merged_facts") or {}
        _collect_category_text({
            "category_cn": facts.get("category_cn"),
            "category": facts.get("category"),
            "category_path": facts.get("category_path"),
        }, parts)
    return " ".join(parts).casefold()


def is_residential_garden_category(fill_input: Dict[str, Any] | None) -> bool:
    text = category_text_for_brand_policy(fill_input)
    normalized = normalize_attr_name(text)
    return any(token in text or token in normalized for token in RESIDENTIAL_GARDEN_CATEGORY_TOKENS)


def is_rich_content_attribute(attribute: Dict[str, Any]) -> bool:
    try:
        if int(attribute.get("attribute_id") or attribute.get("id") or 0) == 11254:
            return True
    except (TypeError, ValueError):
        pass
    name = normalize_attr_name(attribute.get("attribute_name"))
    return "rich" in name and ("контент" in name or "content" in name)


def physical_dimension_for(attribute: Dict[str, Any]) -> str:
    name = normalize_attr_name(attribute.get("attribute_name"))
    if any(token in name for token in ("хештег", "тег", "hashtag", "tag")):
        return "text"
    if any(token in name for token in ("вес", "weight")):
        return "weight"
    if any(token in name for token in ("объем", "объём", "литр", "мл", "capacity", "volume")):
        return "capacity"
    if any(token in name for token in ("нагруз", "грузопод", "load")):
        return "load"
    if any(token in name for token in ("количеств", "штук", "pcs", "упаков")) or re.search(r"(^|[^а-яa-z])шт([^а-яa-z]|$)", str(attribute.get("attribute_name") or "").casefold()):
        return "quantity"
    if any(token in name for token in ("длина", "ширина", "высота", "размер", "габарит", "диаметр", "diameter", "直径")):
        return "dimension"
    if any(token in name for token in ("цвет", "color")):
        return "color"
    if any(token in name for token in ("материал", "material")):
        return "material"
    return "text"


def is_cable_length_attribute(attribute: Dict[str, Any]) -> bool:
    """Return true only for a real cable/cord-length field.

    Ozon has ordinary product-length fields and specialised cord-length fields.
    Both contain ``Длина`` in their display name, so matching every length to a
    product dimension silently put package/product dimensions into a cable
    attribute.  Attribute 5391 is the currently observed cord-length field;
    the name check keeps this rule valid for equivalent future categories.
    """
    try:
        if int(attribute.get("attribute_id") or attribute.get("id") or 0) == 5391:
            return True
    except (TypeError, ValueError):
        pass
    name = normalize_attr_name(attribute.get("attribute_name"))
    return any(token in name for token in ("длинашнура", "длинакабеля", "длинапровода", "cordlength", "cablelength"))


def decision_has_explicit_cable_evidence(decision: Dict[str, Any]) -> bool:
    """Whether this decision is grounded in explicit cable/cord source data."""
    evidence = " ".join(
        str(value or "")
        for value in (
            decision.get("raw_semantic_value"),
            decision.get("canonical_value"),
            decision.get("source_text"),
            *(decision.get("source_refs") or []),
        )
    ).casefold()
    return any(token in evidence for token in (
        "шнур", "кабель", "провод", "cord", "cable", "power wire",
        "电源线", "线长", "充电线", "数据线",
    ))


def measurement_role_for_attribute(attribute: Dict[str, Any]) -> str | None:
    """Map live Ozon measurement attributes to canonical per-SKU facts.

    These attributes describe the physical SKU that will be shipped or shown on
    the card.  Even when every selected SKU currently has the same estimated
    numbers, the values must stay attached to each ``sku_id`` so a future user
    correction for one SKU cannot be overwritten by a product-level common
    attribute.
    """
    name = str(attribute.get("attribute_name") or "").casefold()
    normalized = normalize_attr_name(name)
    if is_cable_length_attribute(attribute):
        return None
    if physical_dimension_for(attribute) == "capacity":
        return "capacity"
    if "вес" in normalized and "упаков" in normalized:
        return "package_weight"
    if "вес" in normalized:
        return "product_weight"
    if "длина" in normalized:
        return "product_length"
    if "ширина" in normalized:
        return "product_width"
    if "высота" in normalized:
        return "product_height"
    if any(token in normalized for token in ("размер", "габарит")):
        if "упаков" in normalized:
            return "package_dimensions"
        return "product_dimensions"
    return None


def value_looks_like_wrong_dimension(attribute_dimension: str, value: Any) -> bool:
    text = str(value or "").casefold()
    if attribute_dimension == "capacity" and re.search(r"\b(г|гр|kg|кг|gram|грам)", text):
        return True
    if attribute_dimension == "weight" and re.search(r"\b(л|ml|мл|литр)", text):
        return True
    if attribute_dimension == "load" and re.search(r"\b(мл|литр|л)\b", text):
        return True
    return False


def allowed_by_id(attribute: Dict[str, Any], value_id: Any) -> Dict[str, Any] | None:
    if value_id in {None, ""}:
        return None
    try:
        needle = int(value_id)
    except (TypeError, ValueError):
        return None
    return next((item for item in attribute.get("allowed_values") or [] if int(item.get("dictionary_value_id", item.get("id")) or 0) == needle), None)


def allowed_by_value(attribute: Dict[str, Any], value: Any) -> Dict[str, Any] | None:
    normalized = normalize_attr_name(value)
    return next(
        (
            item for item in attribute.get("allowed_values") or []
            if normalize_attr_name(item.get("value")) == normalized
        ),
        None,
    )


def normalize_confidence(value: Any, *, default: float = 0.95) -> float:
    """Normalize confidence to the compiled-attribute 0..1 contract."""
    if value in {None, ""}:
        number = default
    else:
        try:
            number = float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            number = default
    if number > 1 and number <= 100:
        number = number / 100
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def first_present_value(*values: Any) -> Any:
    for value in values:
        if value not in {None, "", "unknown"}:
            return value
    return "unknown"


def _normalized_material_text(value: Any) -> str:
    text = str(value or "").replace("ё", "е").casefold()
    # Material matching has to bridge Chinese source facts and Russian Ozon
    # dictionaries.  The generic attribute-name normalizer intentionally drops
    # CJK, so keep Han characters here while still removing punctuation/spaces.
    return re.sub(r"[^a-zа-яе0-9\u3400-\u9fff]", "", text)


MATERIAL_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "碳钢": ("углеродистаясталь", "углероднаясталь", "carbonsteel"),
    "不锈钢": ("нержавеющаясталь", "нержавейка", "stainlesssteel"),
    "铁": ("железо", "сталь", "металл"),
    "钢": ("сталь",),
    "塑料": ("пластик", "пластмасса"),
    "abs塑料": ("absпластик", "абспластик"),
    "pp": ("полипропилен", "pp"),
    "pet": ("pet", "пэт", "полиэтилентерефталат"),
    "硅胶": ("силикон",),
    "玻璃": ("стекло",),
    "木": ("дерево", "древесина"),
    "铝": ("алюминий",),
}


def material_fact_from_fill_input(fill_input: Dict[str, Any]) -> Dict[str, Any] | None:
    structured = (fill_input.get("merged_facts") or {}).get("structured_attributes") or {}
    for key, raw in structured.items():
        if not isinstance(raw, dict):
            continue
        name = str(key or raw.get("name_cn") or raw.get("field") or "")
        if "材质" not in name and "material" not in name.casefold():
            continue
        value = raw.get("value_cn") or raw.get("value") or raw.get("canonical_value")
        if not str(value or "").strip() or str(value).casefold() == "unknown":
            continue
        return {
            "fact_name": name,
            "raw_value": str(value).strip(),
            "source_text": raw.get("source_text") or raw.get("text") or "",
            "source_ref": raw.get("source_ref") or "input/source.json.product_attributes",
            "source": "1688",
        }
    for raw in fill_input.get("source_attributes") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name_cn") or raw.get("name") or "")
        if "材质" not in name and "material" not in name.casefold():
            continue
        value = raw.get("value_cn") or raw.get("value")
        if not str(value or "").strip() or str(value).casefold() == "unknown":
            continue
        return {
            "fact_name": name,
            "raw_value": str(value).strip(),
            "source_text": raw.get("source_text") or "",
            "source_ref": raw.get("source_ref") or "input/source.json.product_attributes",
            "source": "1688",
        }
    return None


def material_decision_from_fact(attribute: Dict[str, Any], fill_input: Dict[str, Any]) -> Dict[str, Any] | None:
    """Select a legal material dictionary value from current product facts."""
    if physical_dimension_for(attribute) != "material":
        return None
    fact = material_fact_from_fill_input(fill_input)
    if not fact:
        return None
    attribute_name = normalize_attr_name(attribute.get("attribute_name"))
    fact_name = _normalized_material_text(fact.get("fact_name"))
    component_markers = {
        "крышк": ("盖", "盖子", "крышк", "lid", "cover"),
        "ручк": ("把手", "手柄", "ручк", "handle"),
        "лезви": ("刀片", "刀头", "лезви", "blade"),
        "щетин": ("刷毛", "毛", "щетин", "bristle"),
    }
    for attribute_marker, fact_markers in component_markers.items():
        if attribute_marker in attribute_name and not any(
            _normalized_material_text(marker) in fact_name for marker in fact_markers
        ):
            return None
    raw = fact["raw_value"]
    direct = allowed_by_value(attribute, raw)
    matched = direct
    mapping_method = "deterministic_exact_dictionary_match"
    if not matched:
        raw_norm = _normalized_material_text(raw)
        synonym_needles = set(MATERIAL_SYNONYMS.get(raw_norm, ()))
        synonym_needles.add(raw_norm)
        for allowed in attribute.get("allowed_values") or []:
            allowed_norm = _normalized_material_text(allowed.get("value"))
            if allowed_norm in synonym_needles or any(
                needle and (needle == allowed_norm or needle in allowed_norm)
                for needle in synonym_needles
            ):
                matched = allowed
                mapping_method = "AI_semantic_match"
                break
    if not matched:
        return None
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute.get("attribute_name") or "Материал",
        "scope": "common",
        "decision_status": "filled",
        "raw_semantic_value": raw,
        "canonical_value": raw,
        "canonical_unit": "text",
        "ozon_value": str(matched.get("value") or ""),
        "dictionary_value_id": int(matched.get("dictionary_value_id", matched.get("id"))),
        "source": "1688",
        "mapping_method": mapping_method,
        "confidence": 0.98 if mapping_method == "deterministic_exact_dictionary_match" else 0.92,
        "source_refs": [
            str(fact.get("source_ref") or "input/source.json.product_attributes"),
            f"1688 material fact: {raw}",
            f"Ozon dictionary value: {matched.get('value')} ({matched.get('dictionary_value_id', matched.get('id'))})",
            *([str(fact.get("source_text"))] if fact.get("source_text") else []),
        ],
    }


def is_gender_attribute(attribute: Dict[str, Any]) -> bool:
    try:
        if int(attribute.get("attribute_id") or attribute.get("id") or 0) == 9163:
            return True
    except (TypeError, ValueError):
        pass
    return normalize_attr_name(attribute.get("attribute_name")) == "пол"


def gender_fact_from_fill_input(fill_input: Dict[str, Any]) -> Dict[str, Any] | None:
    """Return explicit unisex gender evidence from current source facts only."""
    candidates: List[Dict[str, Any]] = []
    structured = (fill_input.get("merged_facts") or {}).get("structured_attributes") or {}
    for key, raw in structured.items():
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name_cn") or raw.get("name") or key or "")
        if any(token in name.casefold() for token in ("性别", "gender", "пол")):
            candidates.append(raw)
    for raw in fill_input.get("source_attributes") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name_cn") or raw.get("name") or "")
        if any(token in name.casefold() for token in ("性别", "gender", "пол")):
            candidates.append(raw)
    neutral_tokens = (
        "中性", "男女均可", "男女通用", "男女同款", "男女皆宜", "男/女", "男 女",
        "унисекс", "unisex", "мужской и женский", "для мужчин и женщин",
    )
    for raw in candidates:
        value = raw.get("value_cn") or raw.get("value") or raw.get("canonical_value") or ""
        source_text = raw.get("source_text") or raw.get("text") or ""
        evidence_text = f"{value} {source_text}".casefold()
        if any(token.casefold() in evidence_text for token in neutral_tokens):
            return {
                "raw_value": str(value).strip(),
                "source_text": str(source_text or "").strip(),
                "source_ref": raw.get("source_ref") or "input/source.json.product_attributes",
            }
    return None


def gender_decision_from_fact(attribute: Dict[str, Any], fill_input: Dict[str, Any]) -> Dict[str, Any] | None:
    if not is_gender_attribute(attribute):
        return None
    fact = gender_fact_from_fill_input(fill_input)
    if not fact:
        return None
    male = allowed_by_value(attribute, "Мужской")
    female = allowed_by_value(attribute, "Женский")
    if not male or not female:
        return None
    values = [
        {"value": str(male.get("value") or "Мужской"), "dictionary_value_id": int(male.get("dictionary_value_id", male.get("id")))},
        {"value": str(female.get("value") or "Женский"), "dictionary_value_id": int(female.get("dictionary_value_id", female.get("id")))},
    ]
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute.get("attribute_name") or "Пол",
        "scope": "common",
        "decision_status": "filled",
        "raw_semantic_value": fact["raw_value"],
        "canonical_value": "unisex",
        "canonical_unit": "dictionary_collection",
        "ozon_value": "; ".join(item["value"] for item in values),
        "dictionary_values": values,
        "source": "1688",
        "mapping_method": "deterministic_unisex_gender_split",
        "confidence": 0.94,
        "source_refs": [
            str(fact.get("source_ref") or "input/source.json.product_attributes"),
            f"1688 gender fact: {fact['raw_value']}",
            "Ozon gender dictionary has no unisex value; selected Мужской and Женский",
            *([str(fact.get("source_text"))] if fact.get("source_text") else []),
        ],
    }


def infer_target_unit(attribute: Dict[str, Any], dimension: str) -> str:
    name = str(attribute.get("attribute_name") or "").casefold()
    if dimension == "dimension":
        return "cm" if any(token in name for token in ("см", "cm")) else "mm"
    if dimension == "weight":
        return "kg" if any(token in name for token in ("кг", "kg")) else "g"
    if dimension == "capacity":
        if any(token in name for token in ("мл", "ml")):
            return "ml"
        if "литр" in name or re.search(r"(^|[^а-яa-z])(л|l)([^а-яa-z]|$)", name):
            return "l"
        return "ml"
    if dimension == "quantity":
        return "pcs"
    return "text"


def infer_source_unit(value: Any, dimension: str, fallback: str) -> str:
    text = str(value or "").casefold()
    if dimension == "dimension":
        if "см" in text or "cm" in text:
            return "cm"
        if "мм" in text or "mm" in text:
            return "mm"
    if dimension == "weight":
        if "кг" in text or "kg" in text:
            return "kg"
        if "г" in text or "gram" in text:
            return "g"
    if dimension == "capacity":
        if "мл" in text or "ml" in text:
            return "ml"
        if "литр" in text or re.search(r"\d\s*[lл]\b", text):
            return "l"
    return fallback


def number_from_any(value: Any) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value or "").strip().replace(",", ".")
        text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            raise AttributeCompileError(f"cannot compile numeric value from {value!r}")
        number = float(match.group(0))
    if not math.isfinite(number):
        raise AttributeCompileError(f"non-finite numeric value {value!r}")
    return number


def convert_unit(value: Any, source_unit: str, target_unit: str, dimension: str) -> Tuple[Any, str]:
    if dimension not in {"dimension", "weight", "capacity", "quantity"}:
        return value, "none"
    source_unit = (source_unit or target_unit or "").casefold()
    target_unit = (target_unit or source_unit or "").casefold()
    number = number_from_any(value)
    if source_unit == target_unit:
        return number, f"{source_unit}_to_{target_unit}"
    rules = {
        ("mm", "cm"): lambda item: item / 10,
        ("cm", "mm"): lambda item: item * 10,
        ("ml", "l"): lambda item: item / 1000,
        ("l", "ml"): lambda item: item * 1000,
        ("g", "kg"): lambda item: item / 1000,
        ("kg", "g"): lambda item: item * 1000,
    }
    converter = rules.get((source_unit, target_unit))
    if not converter:
        raise AttributeCompileError(f"unsupported unit conversion {source_unit}->{target_unit}")
    return converter(number), f"{source_unit}_to_{target_unit}"


def source_for_decision(decision: Dict[str, Any]) -> str:
    explicit = str(decision.get("source") or "").strip()
    if explicit and explicit.casefold() not in {"unknown", "ai"}:
        return explicit
    refs = " ".join(str(item) for item in decision.get("source_refs") or [])
    mapping = str(decision.get("mapping_method") or "").casefold()
    if "workbench-sku-overrides" in refs:
        return "human_override"
    if "input/source.json" in refs or "1688" in refs or "source." in refs:
        return "1688"
    if mapping in {"ai_semantic_match", "semantic_match"} and refs:
        return "1688"
    if "estimated" in mapping or "estimate" in mapping:
        return "AI_estimated"
    return "unknown"


def string_limits_for_attribute(attribute: Dict[str, Any]) -> Tuple[int | None, int | None]:
    """Return field-specific string limits from live metadata/field contract.

    The compiler must not apply one global length rule to every string field.
    When Ozon metadata provides a concrete limit, use it.  Otherwise keep a
    conservative per-meaning default so ordinary attributes, title-like fields
    and long descriptions are not silently treated as the same contract.
    """
    if is_rich_content_attribute(attribute):
        return None, None
    constraints = attribute.get("constraints") or attribute.get("field_contract") or {}
    max_chars = (
        attribute.get("max_length")
        or attribute.get("max_chars")
        or attribute.get("max_value_length")
        or constraints.get("max_length")
        or constraints.get("max_chars")
    )
    max_bytes = attribute.get("max_bytes") or constraints.get("max_bytes")
    try:
        max_chars = int(max_chars) if max_chars not in {None, "", 0} else None
    except (TypeError, ValueError):
        max_chars = None
    try:
        max_bytes = int(max_bytes) if max_bytes not in {None, "", 0} else None
    except (TypeError, ValueError):
        max_bytes = None
    if max_chars or max_bytes:
        return max_chars, max_bytes
    name = normalize_attr_name(attribute.get("attribute_name"))
    if "описание" in name or "аннотац" in name or "description" in name:
        return 4000, 8000
    if "название" in name or "name" in name:
        return 500, 1000
    if "цвет" in name or "color" in name:
        return 80, 160
    return 1000, 2000


def format_compiled_value(
    value: Any,
    attr_type: str,
    dimension: str,
    target_unit: str,
    attribute: Dict[str, Any],
) -> Any:
    attr_type = attr_type.casefold()
    if is_rich_content_attribute(attribute):
        text = str(value or "").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AttributeCompileError("Rich Content attribute must be valid JSON") from exc
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    if attr_type in {"integer", "int32", "int64"}:
        number = number_from_any(value)
        if dimension in {"weight", "dimension", "capacity"} and target_unit in {"g", "mm", "ml"}:
            return int(math.ceil(number))
        if dimension == "quantity":
            return int(round(number))
        return int(number) if float(number).is_integer() else int(round(number))
    if attr_type in {"decimal", "double", "float"}:
        number = number_from_any(value)
        return int(number) if float(number).is_integer() else round(number, 6)
    if attr_type in {"boolean", "bool"}:
        return normalize_bool(value)
    max_chars, max_bytes = string_limits_for_attribute(attribute)
    return clean_string(value, max_chars=max_chars, max_bytes=max_bytes)


def numeric_bounds_for_attribute(attribute: Dict[str, Any]) -> Tuple[float | None, float | None]:
    """Read live numeric bounds without confusing list-count limits for value limits."""
    containers = [attribute]
    for key in ("constraints", "field_contract", "restrictions", "value_limits", "validation"):
        value = attribute.get(key)
        if isinstance(value, dict):
            containers.append(value)
    minimum = maximum = None
    for container in containers:
        if minimum is None:
            for key in ("min_value", "minimum", "min", "minValue", "lower_bound"):
                value = container.get(key)
                if value not in {None, ""}:
                    try:
                        minimum = float(value)
                    except (TypeError, ValueError):
                        pass
                    break
        if maximum is None:
            for key in ("max_value", "maximum", "max", "maxValue", "upper_bound"):
                value = container.get(key)
                if value not in {None, ""}:
                    try:
                        maximum = float(value)
                    except (TypeError, ValueError):
                        pass
                    break
    return minimum, maximum


def ensure_numeric_range(attribute: Dict[str, Any], value: Any) -> None:
    minimum, maximum = numeric_bounds_for_attribute(attribute)
    if minimum is None and maximum is None:
        return
    number = number_from_any(value)
    if minimum is not None and number < minimum:
        raise AttributeCompileError(
            f"attribute {attribute['attribute_id']} value {number:g} is below current Ozon minimum {minimum:g}"
        )
    if maximum is not None and number > maximum:
        raise AttributeCompileError(
            f"attribute {attribute['attribute_id']} value {number:g} exceeds current Ozon maximum {maximum:g}"
        )


def missing_compiled_attribute(attribute: Dict[str, Any], *, scope: str = "common", sku_id: str | None = None, reason: str = "missing ecommerce designer decision") -> Dict[str, Any]:
    dimension = physical_dimension_for(attribute)
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute["attribute_name"],
        "scope": scope,
        **({"sku_id": sku_id} if sku_id else {}),
        "required": bool(attribute.get("required")),
        "value": "unknown",
        "canonical_value": "unknown",
        "canonical_unit": "unknown",
        "target_value": "unknown",
        "target_unit": infer_target_unit(attribute, dimension),
        "conversion_rule": "none",
        "source": "unknown",
        "mapping_method": "not_filled",
        "confidence": 0,
        "dictionary_value_id": None,
        "evidence": [reason],
    }


def category_brand_default_decision(attribute: Dict[str, Any], fill_input: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    """Return the deterministic project brand for the current Ozon category."""
    if not is_brand_attribute(attribute):
        return None
    target_value = JLC_BRAND_VALUE if is_residential_garden_category(fill_input) else NO_BRAND_VALUE
    allowed = allowed_by_value(attribute, target_value)
    fallback_used = False
    if not allowed and target_value != NO_BRAND_VALUE:
        fallback_used = True
        allowed = allowed_by_value(attribute, NO_BRAND_VALUE)
        target_value = NO_BRAND_VALUE
    if not allowed:
        return None
    category_note = "residential/garden category default" if is_residential_garden_category(fill_input) else "non-residential category default"
    refs = [f"project brand policy: {category_note} -> {target_value}"]
    if fallback_used:
        refs.append("JLC GLOBAL was not present in current live Ozon brand allowed_values; used Нет бренда fallback")
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute.get("attribute_name") or "Бренд",
        "scope": "common",
        "decision_status": "filled",
        "raw_semantic_value": target_value,
        "canonical_value": target_value,
        "canonical_unit": "dictionary",
        "ozon_value": str(allowed["value"]),
        "dictionary_value_id": int(allowed.get("dictionary_value_id", allowed.get("id"))),
        "source": "shop_default_brand_policy",
        "mapping_method": "project_default_brand_by_category",
        "confidence": 1.0,
        "source_refs": refs,
    }


def unbranded_default_decision(attribute: Dict[str, Any]) -> Dict[str, Any] | None:
    return category_brand_default_decision(attribute, None)


def is_merge_model_name_attribute(attribute: Dict[str, Any]) -> bool:
    try:
        attribute_id = int(attribute.get("attribute_id") or 0)
    except (TypeError, ValueError):
        attribute_id = 0
    name = normalize_attr_name(attribute.get("attribute_name"))
    return attribute_id == 9048 or "названиемоделидляобъединения" in name


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.casefold() not in {"unknown", "none", "null"}:
            return text
    return ""


def merge_model_name_default_decision(
    attribute: Dict[str, Any],
    fill_input: Dict[str, Any],
    design: Dict[str, Any],
    *,
    product_id: str,
) -> Dict[str, Any] | None:
    if not is_merge_model_name_attribute(attribute):
        return None
    listing = design.get("listing") or {}
    facts = fill_input.get("merged_facts") or {}
    base = _first_text(
        listing.get("seo_title_ru"),
        listing.get("title_ru"),
        facts.get("title_ru"),
        facts.get("product_type_ru"),
        facts.get("title_cn"),
        facts.get("product_type"),
        facts.get("product_type_cn"),
        product_id,
    )
    value = clean_string(f"{base} {product_id}", max_chars=100)
    if value == "unknown":
        value = clean_string(product_id, max_chars=100)
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute.get("attribute_name") or "Название модели (для объединения в одну карточку)",
        "scope": "common",
        "decision_status": "filled",
        "raw_semantic_value": value,
        "canonical_value": value,
        "canonical_unit": "text",
        "ozon_value": value,
        "source": "deterministic_compiler_default",
        "mapping_method": "stable_model_name_from_listing_and_product_id",
        "confidence": 0.96,
        "source_refs": [
            "output/ozon-ecommerce-design.json.listing.seo_title_ru",
            "product_id stable suffix prevents cross-product merge collisions",
        ],
    }


def is_product_type_attribute(attribute: Dict[str, Any]) -> bool:
    try:
        attribute_id = int(attribute.get("attribute_id") or 0)
    except (TypeError, ValueError):
        attribute_id = 0
    name = normalize_attr_name(attribute.get("attribute_name"))
    return attribute_id == 8229 or name == "тип"


def product_type_default_decision(
    attribute: Dict[str, Any],
    fill_input: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    if not is_product_type_attribute(attribute):
        return None
    allowed_values = attribute.get("allowed_values") or []
    allowed = None
    category_type_id = None
    if fill_input:
        category = fill_input.get("category") if isinstance(fill_input.get("category"), dict) else {}
        category_type_id = fill_input.get("type_id") or category.get("type_id")
    if category_type_id not in {None, "", "unknown"}:
        allowed = allowed_by_id(attribute, category_type_id)
    if not allowed and len(allowed_values) == 1:
        allowed = allowed_values[0]
    if not allowed:
        return None
    value = str(allowed.get("value") or "").strip()
    value_id = allowed.get("dictionary_value_id", allowed.get("id"))
    if not value or value_id in {None, ""}:
        return None
    mapping_method = (
        "required_product_type_from_selected_category_type_id"
        if category_type_id not in {None, "", "unknown"} else
        "required_single_allowed_value_default"
    )
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute.get("attribute_name") or "Тип",
        "scope": "common",
        "decision_status": "filled",
        "raw_semantic_value": value,
        "canonical_value": value,
        "canonical_unit": "dictionary",
        "ozon_value": value,
        "dictionary_value_id": int(value_id),
        "source": "ozon_category_metadata",
        "mapping_method": mapping_method,
        "confidence": 1.0,
        "source_refs": [
            "output/attribute-fill-input.json.ozon_attributes",
            "current Ozon category type_id matched required product type dictionary",
        ],
    }


def deterministic_common_default_decision(
    attribute: Dict[str, Any],
    fill_input: Dict[str, Any],
    design: Dict[str, Any],
    *,
    product_id: str,
) -> Dict[str, Any] | None:
    return (
        category_brand_default_decision(attribute, fill_input)
        or merge_model_name_default_decision(attribute, fill_input, design, product_id=product_id)
        or product_type_default_decision(attribute, fill_input)
    )


FREE_SIZE_TOKENS = {
    "均码", "通用", "универсальный", "универсальная", "универсальное",
    "one size", "onesize", "free size", "freesize",
}
# Only apparel-size option names belong here.  Generic Chinese "尺寸/大小" often
# carries product length/width/height, and must not block the safe Ozon
# "универсальный" Russian-size default.
SIZE_OPTION_NAME_TOKENS = {"尺码", "размер", "size"}
CONCRETE_SIZE_PATTERN = re.compile(
    r"(?i)(?:^|[\s,;/])(?:xxs|xs|s|m|l|xl|xxl|xxxl|[2-9][0-9])(?:$|[\s,;/])"
)


def is_russian_size_attribute(attribute: Dict[str, Any]) -> bool:
    name = normalize_attr_name(attribute.get("attribute_name"))
    return "российскийразмер" in name or name in {"размер", "размертовара"}


def _iter_sku_texts(fill_input: Dict[str, Any]) -> Iterable[Tuple[str, str]]:
    """Yield SKU option names and values from the current product only."""
    def text_value(value: Any) -> str:
        if value in (None, "", "unknown"):
            return ""
        if isinstance(value, dict):
            return " ".join(
                part for part in (text_value(item) for item in value.values()) if part
            )
        if isinstance(value, list):
            return " ".join(
                part for part in (text_value(item) for item in value) if part
            )
        return str(value)

    for container_name in ("selected_skus", "sku_rows"):
        for item in fill_input.get(container_name) or []:
            if not isinstance(item, dict):
                continue
            for key in ("sku_name", "source_sku_name", "specification", "specification_text", "color", "capacity"):
                value = text_value(item.get(key))
                if value:
                    yield "", value
            for option in item.get("option_values") or item.get("options") or []:
                if not isinstance(option, dict):
                    continue
                yield text_value(option.get("name_cn") or option.get("name")), text_value(
                    option.get("value_cn") or option.get("value")
                )


def size_evidence_state(fill_input: Dict[str, Any]) -> str:
    """Return none/free/concrete for explicit size evidence in this product.

    Do not treat arbitrary digits in colour/spec strings as a size.  A concrete
    size must either come from a size-named option or look like a standalone
    apparel size token.
    """
    saw_size_named_value = False
    for name, value in _iter_sku_texts(fill_input):
        text = f"{name} {value}".strip().casefold()
        if not text:
            continue
        if any(token in text for token in FREE_SIZE_TOKENS):
            return "free"
        name_norm = normalize_attr_name(name)
        value_norm = normalize_attr_name(value)
        if any(token in name.casefold() or token in name_norm for token in SIZE_OPTION_NAME_TOKENS):
            saw_size_named_value = True
            if value_norm and not any(token in value.casefold() for token in FREE_SIZE_TOKENS):
                return "concrete"
        explicit_size_context = any(
            token in text for token in ("尺码", "размер", " size ")
        ) or saw_size_named_value
        standalone_size_value = bool(re.fullmatch(r"(?i)(xxs|xs|s|m|l|xl|xxl|xxxl|[2-9][0-9])", value.strip()))
        if CONCRETE_SIZE_PATTERN.search(f" {value} ") and (explicit_size_context or standalone_size_value):
            return "concrete"
    return "concrete" if saw_size_named_value else "none"


def universal_russian_size_decision(
    attribute: Dict[str, Any],
    fill_input: Dict[str, Any],
    *,
    sku_id: str | None = None,
) -> Dict[str, Any] | None:
    if not is_russian_size_attribute(attribute):
        return None
    allowed = allowed_by_value(attribute, "универсальный")
    if not allowed:
        return None
    state = size_evidence_state(fill_input)
    if state == "concrete":
        return None
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute.get("attribute_name") or "Российский размер",
        "scope": "sku" if sku_id else "common",
        **({"sku_id": sku_id} if sku_id else {}),
        "decision_status": "filled",
        "raw_semantic_value": "универсальный размер" if state == "none" else "free size",
        "canonical_value": "универсальный",
        "canonical_unit": "dictionary",
        "ozon_value": str(allowed.get("value") or "универсальный"),
        "dictionary_value_id": int(allowed.get("dictionary_value_id", allowed.get("id"))),
        "source": "AI_estimated" if state == "none" else "1688",
        "mapping_method": "deterministic_universal_size_default" if state == "none" else "AI_semantic_match",
        "confidence": 0.72 if state == "none" else 0.90,
        "source_refs": [
            "current category allowed_values contains универсальный",
            "no explicit per-SKU apparel size was captured" if state == "none" else "source contains free-size wording",
        ],
    }


SIZE_RANK_TOKEN_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("xs", ("迷你", "特小", "мини", "mini", "extra small", "xs")),
    ("s", ("小号", "小款", "малый", "малая", "маленький", "маленькая", "small")),
    ("m", ("中号", "中款", "средний", "средняя", "medium")),
    ("l", ("大号", "大款", "большой", "большая", "large")),
    ("xl", ("加大", "超大", "крупный", "крупная", "очень большой", "xl")),
)
SIZE_RANK_SCALE = {"xs": 0.50, "s": 0.67, "m": 0.83, "l": 1.00, "xl": 1.17}


def sku_text_for_id(fill_input: Dict[str, Any], sku_id: str) -> str:
    texts: List[str] = []

    def append_measurement_text(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("canonical_value", "target_value", "value", "raw_value"):
                if key in value:
                    append_measurement_text(value.get(key))
            return
        if isinstance(value, list):
            for item in value:
                append_measurement_text(item)
            return
        if value in {None, "", "unknown"}:
            return
        text = str(value).strip()
        if text and text != "unknown":
            texts.append(text)

    def append_text(value: Any) -> None:
        if isinstance(value, dict):
            append_measurement_text(value)
            return
        if isinstance(value, list):
            for item in value:
                append_text(item)
            return
        if value in {None, "", "unknown"}:
            return
        text = str(value).strip()
        if text and text != "unknown":
            texts.append(text)

    for container_name in ("selected_skus", "sku_rows"):
        for item in fill_input.get(container_name) or []:
            if not isinstance(item, dict):
                continue
            current_id = str(item.get("sku_id") or item.get("source_sku_id") or "").strip()
            if current_id != sku_id:
                continue
            for key in ("sku_name", "source_sku_name", "option_text", "specification", "specification_text"):
                append_text(item.get(key))
            for option in item.get("option_values") or item.get("options") or []:
                if not isinstance(option, dict):
                    continue
                for key in ("name_cn", "name", "value_cn", "value", "source_text"):
                    append_text(option.get(key))
            sku_row = item.get("sku_row")
            if isinstance(sku_row, dict):
                for key in ("sku_name", "option_text", "specification", "specification_text"):
                    append_measurement_text(sku_row.get(key))
    return " ".join(dict.fromkeys(texts))


def size_rank_from_text(text: str) -> str | None:
    lowered = text.casefold()
    normalized = normalize_attr_name(lowered)
    for rank, tokens in SIZE_RANK_TOKEN_GROUPS:
        for token in tokens:
            token_lower = token.casefold()
            token_normalized = normalize_attr_name(token_lower)
            if token_lower in lowered or (token_normalized and token_normalized in normalized):
                return rank
    return None


def _positive_number(value: Any) -> float | None:
    try:
        number = number_from_any(value)
    except AttributeCompileError:
        return None
    return number if number > 0 else None


def product_reference_dimension_cm(fill_input: Dict[str, Any]) -> Tuple[float | None, str]:
    candidates = [
        ((fill_input.get("measurements") or {}).get("product_dimensions"), "attribute-fill-input.measurements.product_dimensions"),
        ((fill_input.get("merged_facts") or {}).get("product_dimensions"), "attribute-fill-input.merged_facts.product_dimensions"),
    ]
    for field, source_ref in candidates:
        value = _measurement_field_value(field)
        if not isinstance(value, dict):
            value = field if isinstance(field, dict) else None
        if not isinstance(value, dict):
            continue
        cm_values: List[float] = []
        for axis in ("length", "width", "height"):
            cm_value = _positive_number(value.get(f"{axis}_cm"))
            if cm_value is not None:
                cm_values.append(cm_value)
                continue
            mm_value = _positive_number(value.get(f"{axis}_mm") or value.get(axis))
            if mm_value is not None:
                cm_values.append(mm_value / 10)
        if cm_values:
            return max(cm_values), source_ref
    return None, "size-rank-default"


def aspect_size_rank_decision(
    attribute: Dict[str, Any],
    fill_input: Dict[str, Any],
    *,
    sku_id: str,
) -> Dict[str, Any] | None:
    if not attribute.get("is_aspect") or physical_dimension_for(attribute) != "dimension":
        return None
    attr_type = str(attribute.get("type") or "String").casefold()
    if attr_type not in {"integer", "int32", "int64", "decimal", "double", "float"}:
        return None
    text = sku_text_for_id(fill_input, sku_id)
    rank = size_rank_from_text(text)
    if not rank:
        return None
    reference_cm, reference_source = product_reference_dimension_cm(fill_input)
    if reference_cm is None:
        reference_cm = 30.0
    value_cm = max(1.0, round(reference_cm * SIZE_RANK_SCALE[rank], 1))
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute.get("attribute_name") or str(attribute["attribute_id"]),
        "scope": "sku",
        "sku_id": sku_id,
        "decision_status": "filled",
        "raw_semantic_value": value_cm,
        "canonical_value": value_cm,
        "canonical_unit": "cm",
        "ozon_value": value_cm,
        "source": "AI_estimated",
        "mapping_method": "deterministic_size_rank_aspect_estimate",
        "confidence": 0.62,
        "source_refs": [
            f"input/source.json.skus.{sku_id}: {text}",
            reference_source,
            "Ozon category marks this numeric dimension as is_aspect; exact value was not captured, so the value only separates SKU variants.",
        ],
    }


def compile_decision(
    attribute: Dict[str, Any],
    decision: Dict[str, Any],
    *,
    scope: str = "common",
    sku_id: str | None = None,
    fill_input: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if is_brand_attribute(attribute) and decision.get("mapping_method") != "project_default_brand_by_category":
        default_decision = category_brand_default_decision(attribute, fill_input)
        if default_decision:
            return compile_decision(attribute, default_decision, scope=scope, sku_id=sku_id, fill_input=fill_input)
    status = str(decision.get("decision_status") or "filled")
    if status != "filled" or decision.get("ozon_value") in {None, "", "unknown"}:
        default_decision = category_brand_default_decision(attribute, fill_input)
        if default_decision:
            return compile_decision(attribute, default_decision, scope=scope, sku_id=sku_id, fill_input=fill_input)
        return missing_compiled_attribute(attribute, scope=scope, sku_id=sku_id)
    dimension = physical_dimension_for(attribute)
    if is_cable_length_attribute(attribute) and not decision_has_explicit_cable_evidence(decision):
        raise AttributeCompileError(
            f"attribute {attribute['attribute_id']} {attribute['attribute_name']} has no explicit cable-length source"
        )
    if value_looks_like_wrong_dimension(dimension, decision.get("raw_semantic_value")):
        raise AttributeCompileError(
            f"attribute {attribute['attribute_id']} {attribute['attribute_name']} received incompatible physical value"
        )
    allowed_values = attribute.get("allowed_values") or []
    dictionary_value_id = None
    raw_value = decision.get("ozon_value")
    target_unit = infer_target_unit(attribute, dimension)
    canonical_value = first_present_value(
        decision.get("canonical_value"),
        decision.get("raw_semantic_value"),
        raw_value,
        decision.get("target_value"),
        decision.get("value"),
    )
    canonical_unit = decision.get("canonical_unit") or infer_source_unit(canonical_value, dimension, target_unit)
    attr_type = str(attribute.get("type") or "String").casefold()
    conversion_rule = "none"
    if allowed_values:
        dictionary_values = decision.get("dictionary_values") or []
        if dictionary_values:
            compiled_values: List[Dict[str, Any]] = []
            for item in dictionary_values:
                allowed = allowed_by_id(attribute, item.get("dictionary_value_id")) or allowed_by_value(attribute, item.get("value"))
                if not allowed:
                    raise AttributeCompileError(
                        f"attribute {attribute['attribute_id']} dictionary value is absent from current allowed_values"
                    )
                compiled_values.append({
                    "dictionary_value_id": int(allowed.get("dictionary_value_id", allowed.get("id"))),
                    "value": str(allowed.get("value") or ""),
                })
            value = "; ".join(item["value"] for item in compiled_values)
            return {
                "attribute_id": int(attribute["attribute_id"]),
                "attribute_name": attribute["attribute_name"],
                "scope": scope,
                **({"sku_id": sku_id} if sku_id else {}),
                "required": bool(attribute.get("required")),
                "value": value,
                "canonical_value": canonical_value,
                "canonical_unit": canonical_unit,
                "target_value": value,
                "target_unit": "dictionary",
                "conversion_rule": "none",
                "source": source_for_decision(decision),
                "mapping_method": decision.get("mapping_method") or "AI_semantic_match",
                "confidence": normalize_confidence(decision.get("confidence"), default=0.95),
                "dictionary_value_id": None,
                "dictionary_values": compiled_values,
                "evidence": list(decision.get("source_refs") or ["ozon-ecommerce-design.attribute_decisions"]),
            }
        allowed = allowed_by_id(attribute, decision.get("dictionary_value_id")) or allowed_by_value(attribute, raw_value)
        if not allowed:
            raise AttributeCompileError(
                f"attribute {attribute['attribute_id']} dictionary value is absent from current allowed_values"
            )
        raw_value = str(allowed["value"])
        dictionary_value_id = int(allowed.get("dictionary_value_id", allowed.get("id")))
        target_value: Any = raw_value
        target_unit = "dictionary"
    else:
        if (
            dimension in {"dimension", "weight", "capacity", "quantity"}
            and not (attr_type in {"string", "text"} and str(canonical_unit).casefold() == "text")
        ):
            target_value, conversion_rule = convert_unit(canonical_value, canonical_unit, target_unit, dimension)
        else:
            target_value = raw_value
    if not allowed_values and attr_type in {"integer", "int32", "int64", "decimal", "double", "float"}:
        ensure_numeric_range(attribute, target_value)
    if allowed_values:
        max_chars, max_bytes = string_limits_for_attribute(attribute)
        value = clean_string(target_value, max_chars=max_chars, max_bytes=max_bytes)
    else:
        value = format_compiled_value(target_value, attr_type, dimension, target_unit, attribute)
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute["attribute_name"],
        "scope": scope,
        **({"sku_id": sku_id} if sku_id else {}),
        "required": bool(attribute.get("required")),
        "value": value,
        "canonical_value": canonical_value,
        "canonical_unit": canonical_unit,
        "target_value": value,
        "target_unit": target_unit,
        "conversion_rule": conversion_rule,
        "source": source_for_decision(decision),
        "mapping_method": decision.get("mapping_method") or ("AI_semantic_match" if allowed_values else "direct"),
        "confidence": normalize_confidence(decision.get("confidence"), default=0.95),
        "dictionary_value_id": dictionary_value_id,
        "evidence": list(decision.get("source_refs") or ["ozon-ecommerce-design.attribute_decisions"]),
    }


def decisions_by_attribute(decisions: Iterable[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    result: Dict[int, Dict[str, Any]] = {}
    for decision in decisions:
        try:
            attribute_id = int(decision["attribute_id"])
        except (KeyError, TypeError, ValueError):
            continue
        result[attribute_id] = decision
    return result


def sku_ids_from_fill_input(fill_input: Dict[str, Any]) -> List[str]:
    result: List[str] = []
    for item in fill_input.get("selected_skus") or []:
        if isinstance(item, dict):
            sku_id = str(item.get("sku_id") or item.get("source_sku_id") or "").strip()
        else:
            sku_id = str(item or "").strip()
        if sku_id and sku_id not in result:
            result.append(sku_id)
    for item in fill_input.get("sku_rows") or []:
        if not isinstance(item, dict):
            continue
        sku_id = str(item.get("sku_id") or "").strip()
        if sku_id and sku_id not in result:
            result.append(sku_id)
    return result


def normalize_measurement_confidence(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: Dict[str, Any] = {}
        for key, item in value.items():
            normalized[key] = normalize_confidence(item, default=0.0) if key == "confidence" else normalize_measurement_confidence(item)
        return normalized
    if isinstance(value, list):
        return [normalize_measurement_confidence(item) for item in value]
    return value


def sku_measurements_from_fill_input(fill_input: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = fill_input.get("sku_rows") or fill_input.get("merged_facts", {}).get("sku_rows") or []
    product_fallbacks = (fill_input.get("measurements") or {}) or {}
    merged_facts = fill_input.get("merged_facts") or {}
    product_dimensions = product_fallbacks.get("product_dimensions") or merged_facts.get("product_dimensions")
    product_weight = product_fallbacks.get("product_weight") or merged_facts.get("product_weight")
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sku_id = str(row.get("sku_id") or "")
        if not sku_id:
            continue
        row_product_dimensions = row.get("product_dimensions")
        if not _known_measurement_field(row_product_dimensions) and product_dimensions:
            row_product_dimensions = product_level_measurement_field(
                product_dimensions,
                fallback_unit="mm",
                fallback=f"attribute-fill-input.merged_facts.product_dimensions",
            )
        row_product_weight = row.get("product_weight")
        if not _known_measurement_field(row_product_weight) and product_weight:
            row_product_weight = product_level_measurement_field(
                product_weight,
                fallback_unit="g",
                fallback=f"attribute-fill-input.merged_facts.product_weight",
            )
        result[sku_id] = normalize_measurement_confidence({
            "product_dimensions": row_product_dimensions,
            "product_weight": row_product_weight,
            "package_dimensions": row.get("package_dimensions"),
            "package_weight": row.get("package_weight"),
            "capacity": row.get("capacity"),
            "quantity": row.get("quantity"),
        })
    return result


def _known_measurement_field(field: Any) -> bool:
    value = _measurement_field_value(field)
    if value is None:
        return False
    if isinstance(value, str) and value in {"", "unknown"}:
        return False
    if isinstance(value, dict):
        return any(item not in {None, "", "unknown", 0, 0.0} for item in value.values())
    return True


def product_level_measurement_field(value: Any, *, fallback_unit: str, fallback: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        canonical = value.get("canonical_value") or value.get("target_value") or value.get("value")
        if canonical is None and fallback_unit == "mm":
            canonical = {
                "length_mm": value.get("length_mm"),
                "width_mm": value.get("width_mm"),
                "height_mm": value.get("height_mm"),
            }
        elif canonical is None and fallback_unit == "g":
            canonical = value.get("value_g")
        source = value.get("source") or value.get("source_ref") or "unknown"
        confidence = value.get("confidence")
        if confidence in {None, ""}:
            confidence = 0.72 if value.get("estimated") or str(source).startswith("pricing_rules.") else 0.9
        mapping_method = value.get("mapping_method")
        if mapping_method in {None, "", "unknown"}:
            mapping_method = "product_level_measurement_fallback"
        return {
            "canonical_value": canonical,
            "canonical_unit": value.get("canonical_unit") or fallback_unit,
            "source": source,
            "mapping_method": mapping_method,
            "confidence": confidence,
            "source_refs": [value.get("source_ref") or fallback],
        }
    return {
        "canonical_value": value,
        "canonical_unit": fallback_unit,
        "source": "unknown",
        "mapping_method": "product_level_measurement_fallback",
        "confidence": 0.72,
        "source_refs": [fallback],
    }


def _dimension_axis_value(dimensions: Any, axis: str) -> Any:
    if not isinstance(dimensions, dict):
        return None
    value = dimensions.get(f"{axis}_mm")
    if value is None:
        value = dimensions.get(axis)
    return value


def _measurement_field_value(field: Any) -> Any:
    if isinstance(field, dict):
        return field.get("canonical_value", field.get("target_value", field.get("value")))
    return field


def _measurement_unit(field: Any, *, default: str) -> str:
    if isinstance(field, dict):
        unit = field.get("canonical_unit") or field.get("target_unit") or field.get("unit")
        if unit not in {None, "", "unknown"}:
            return str(unit)
    return default


def _format_measurement_number(value: Any) -> str:
    number = number_from_any(value)
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


def _measurement_weight_value(field: Any) -> Any:
    value = _measurement_field_value(field)
    if isinstance(value, dict):
        return value.get("value_g", value.get("value"))
    return value


def _measurement_source(field: Any) -> str:
    if isinstance(field, dict):
        source = field.get("source")
        if source not in {None, "", "unknown"}:
            return str(source)
    return "unknown"


def _measurement_confidence(field: Any) -> float:
    if isinstance(field, dict):
        return normalize_confidence(field.get("confidence"), default=0.0)
    return 0.0


def _measurement_mapping_method(field: Any) -> str:
    if isinstance(field, dict):
        value = field.get("mapping_method")
        if value not in {None, "", "unknown"}:
            return str(value)
    return "measurement_from_sku_row"


def _measurement_source_refs(field: Any, *, fallback: str) -> List[str]:
    refs: List[str] = []
    if isinstance(field, dict):
        raw_refs = field.get("source_refs")
        if isinstance(raw_refs, list):
            refs.extend(str(item) for item in raw_refs if str(item).strip())
        if field.get("source_ref"):
            refs.append(str(field["source_ref"]))
    if not refs:
        refs.append(fallback)
    return refs


def measurement_decision_for_attribute(
    attribute: Dict[str, Any],
    sku_id: str,
    measurement: Dict[str, Any],
) -> Dict[str, Any] | None:
    role = measurement_role_for_attribute(attribute)
    if not role:
        return None
    if role == "package_weight":
        field = measurement.get("package_weight")
        raw_value = _measurement_weight_value(field)
        canonical_unit = "g"
    elif role == "product_weight":
        field = measurement.get("product_weight")
        raw_value = _measurement_weight_value(field)
        canonical_unit = "g"
    elif role in {"product_length", "product_width", "product_height"}:
        field = measurement.get("product_dimensions")
        dimensions = _measurement_field_value(field)
        axis = role.removeprefix("product_")
        raw_value = _dimension_axis_value(dimensions, axis)
        canonical_unit = "mm"
    elif role in {"product_dimensions", "package_dimensions"}:
        # Composite size attributes are less common, but some categories expose
        # a single free-text size field.  Keep the source/confidence from the SKU
        # row and let the compiler format the string deterministically.
        field = measurement.get(role)
        dimensions = _measurement_field_value(field)
        if not isinstance(dimensions, dict):
            return None
        values = [_dimension_axis_value(dimensions, axis) for axis in ("length", "width", "height")]
        if any(value in {None, "", "unknown"} for value in values):
            return None
        raw_value = " x ".join(_format_measurement_number(value) for value in values)
        canonical_unit = "text"
    elif role == "capacity":
        field = measurement.get("capacity")
        raw_value = _measurement_field_value(field)
        canonical_unit = _measurement_unit(field, default="ml")
    else:
        return None
    if raw_value in {None, "", "unknown"}:
        return None
    return {
        "attribute_id": int(attribute["attribute_id"]),
        "attribute_name": attribute.get("attribute_name") or str(attribute["attribute_id"]),
        "scope": "sku",
        "decision_status": "filled",
        "raw_semantic_value": raw_value,
        "canonical_value": raw_value,
        "canonical_unit": canonical_unit,
        "ozon_value": raw_value,
        "source": _measurement_source(field),
        "mapping_method": _measurement_mapping_method(field),
        "confidence": _measurement_confidence(field),
        "source_refs": _measurement_source_refs(
            field,
            fallback=f"attribute-fill-input.sku_rows.{sku_id}.{role}",
        ),
    }


def measurement_attribute_ids_for_sku_projection(
    attributes_by_id: Dict[int, Dict[str, Any]],
    sku_measurements: Dict[str, Dict[str, Any]],
) -> set[int]:
    if not sku_measurements:
        return set()
    result: set[int] = set()
    for attribute_id, attribute in attributes_by_id.items():
        role = measurement_role_for_attribute(attribute)
        if not role:
            continue
        for sku_id, measurement in sku_measurements.items():
            if measurement_decision_for_attribute(attribute, sku_id, measurement):
                result.add(attribute_id)
                break
    return result


def compile_product_attributes(product_dir: Path) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    output = product_dir / "output"
    fill_input = load_json(output / "attribute-fill-input.json")
    design = load_json(output / "ozon-ecommerce-design.json")
    by_id = {int(item["attribute_id"]): item for item in fill_input.get("ozon_attributes") or []}
    section = design.get("attribute_decisions") or {}
    common_decisions = decisions_by_attribute(section.get("common_attributes") or [])
    sku_decisions = {
        str(sku_id): decisions_by_attribute(values or [])
        for sku_id, values in (section.get("attributes_by_sku") or {}).items()
    }
    sku_ids = sku_ids_from_fill_input(fill_input)
    sku_measurements = sku_measurements_from_fill_input(fill_input)
    measurement_sku_attribute_ids = measurement_attribute_ids_for_sku_projection(by_id, sku_measurements)
    common_attributes: List[Dict[str, Any]] = []
    attributes_by_sku: Dict[str, List[Dict[str, Any]]] = {sku_id: [] for sku_id in sku_ids}
    errors: List[str] = []
    repair_report: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "generated_at": now(),
        "compiler_version": COMPILER_VERSION,
        "converted": [],
        "dictionary_matches": [],
        "omitted_optional": [],
        "required_errors": [],
    }

    def compile_or_repair(
        attribute: Dict[str, Any],
        decision: Dict[str, Any],
        *,
        scope: str,
        sku_id: str | None = None,
    ) -> Dict[str, Any] | None:
        try:
            compiled = compile_decision(attribute, decision, scope=scope, sku_id=sku_id, fill_input=fill_input)
        except AttributeCompileError as exc:
            event = {
                "attribute_id": int(attribute["attribute_id"]),
                "attribute_name": attribute.get("attribute_name"),
                "scope": scope,
                **({"sku_id": sku_id} if sku_id else {}),
                "action": "blocked_required" if attribute.get("required") else "omitted_optional",
                "reason": str(exc),
            }
            if attribute.get("required"):
                errors.append(str(exc))
                repair_report["required_errors"].append(event)
                return missing_compiled_attribute(attribute, scope=scope, sku_id=sku_id, reason=str(exc))
            repair_report["omitted_optional"].append(event)
            return None
        if compiled.get("value") == "unknown":
            size_default = universal_russian_size_decision(attribute, fill_input, sku_id=sku_id)
            if size_default:
                compiled = compile_decision(attribute, size_default, scope=scope, sku_id=sku_id, fill_input=fill_input)
        if compiled.get("value") == "unknown" and sku_id:
            aspect_default = aspect_size_rank_decision(attribute, fill_input, sku_id=sku_id)
            if aspect_default:
                compiled = compile_decision(attribute, aspect_default, scope=scope, sku_id=sku_id, fill_input=fill_input)
        if compiled.get("conversion_rule") not in {None, "", "none", "mm_to_mm", "g_to_g", "ml_to_ml", "pcs_to_pcs"}:
            repair_report["converted"].append({
                "attribute_id": compiled["attribute_id"],
                "scope": scope,
                **({"sku_id": sku_id} if sku_id else {}),
                "from": compiled.get("canonical_value"),
                "from_unit": compiled.get("canonical_unit"),
                "to": compiled.get("target_value"),
                "to_unit": compiled.get("target_unit"),
                "conversion_rule": compiled.get("conversion_rule"),
            })
        if compiled.get("dictionary_value_id") is not None:
            repair_report["dictionary_matches"].append({
                "attribute_id": compiled["attribute_id"],
                "scope": scope,
                **({"sku_id": sku_id} if sku_id else {}),
                "value": compiled.get("value"),
                "dictionary_value_id": compiled.get("dictionary_value_id"),
                "mapping_method": compiled.get("mapping_method"),
            })
        for value in compiled.get("dictionary_values") or []:
            repair_report["dictionary_matches"].append({
                "attribute_id": compiled["attribute_id"],
                "scope": scope,
                **({"sku_id": sku_id} if sku_id else {}),
                "value": value.get("value"),
                "dictionary_value_id": value.get("dictionary_value_id"),
                "mapping_method": compiled.get("mapping_method"),
            })
        return compiled
    for attribute_id, attribute in by_id.items():
        current = common_decisions.get(attribute_id)
        if current and str(current.get("decision_status") or "filled") == "filled" and current.get("ozon_value") not in {None, "", "unknown"}:
            continue
        default_decision = deterministic_common_default_decision(
            attribute,
            fill_input,
            design,
            product_id=product_dir.name,
        )
        if default_decision:
            common_decisions[attribute_id] = default_decision
            continue
        gender_decision = gender_decision_from_fact(attribute, fill_input)
        if gender_decision:
            common_decisions[attribute_id] = gender_decision
            continue
        material_decision = material_decision_from_fact(attribute, fill_input)
        if material_decision:
            common_decisions[attribute_id] = material_decision
    for attribute_id, decision in common_decisions.items():
        attribute = by_id.get(attribute_id)
        if not attribute:
            continue
        if attribute_id in measurement_sku_attribute_ids:
            continue
        compiled = compile_or_repair(attribute, decision, scope="common")
        if compiled:
            common_attributes.append(compiled)
    # Required common attributes with no designer decision and no deterministic
    # default must be reported honestly instead of silently dropped (previously
    # required_summary could show total: 0 while the category requires values).
    for attribute_id, attribute in by_id.items():
        if not attribute.get("required") or attribute.get("is_aspect"):
            continue
        if any(int(item["attribute_id"]) == attribute_id for item in common_attributes):
            continue
        common_attributes.append(missing_compiled_attribute(attribute, scope="common"))
    common_ids = {int(item["attribute_id"]) for item in common_attributes}
    for sku_id in sku_ids:
        decisions = sku_decisions.get(sku_id, {})
        seen_ids: set[int] = set()
        measurement = sku_measurements.get(sku_id) or {}
        for attribute_id in sorted(measurement_sku_attribute_ids):
            if attribute_id in decisions:
                continue
            attribute = by_id.get(attribute_id)
            if not attribute:
                continue
            decision = measurement_decision_for_attribute(attribute, sku_id, measurement)
            if not decision:
                continue
            compiled = compile_or_repair(attribute, decision, scope="sku", sku_id=sku_id)
            if compiled:
                attributes_by_sku.setdefault(sku_id, []).append(compiled)
            seen_ids.add(attribute_id)
        for attribute_id, decision in decisions.items():
            if attribute_id in seen_ids:
                continue
            attribute = by_id.get(attribute_id)
            if not attribute:
                continue
            compiled = compile_or_repair(attribute, decision, scope="sku", sku_id=sku_id)
            if compiled:
                attributes_by_sku.setdefault(sku_id, []).append(compiled)
            seen_ids.add(attribute_id)
        for attribute_id, attribute in by_id.items():
            if (
                not attribute.get("required")
                and not attribute.get("is_aspect")
            ) or attribute_id in common_ids or attribute_id in seen_ids:
                continue
            size_default = universal_russian_size_decision(attribute, fill_input, sku_id=sku_id)
            if size_default:
                compiled = compile_or_repair(attribute, size_default, scope="sku", sku_id=sku_id)
                if compiled:
                    attributes_by_sku.setdefault(sku_id, []).append(compiled)
                seen_ids.add(attribute_id)
                continue
            aspect_default = aspect_size_rank_decision(attribute, fill_input, sku_id=sku_id)
            if aspect_default:
                compiled = compile_or_repair(attribute, aspect_default, scope="sku", sku_id=sku_id)
                if compiled:
                    attributes_by_sku.setdefault(sku_id, []).append(compiled)
                seen_ids.add(attribute_id)
                continue
            if attribute.get("required"):
                attributes_by_sku.setdefault(sku_id, []).append(
                    missing_compiled_attribute(attribute, scope="sku", sku_id=sku_id)
                )
    write_json_atomic(output / "ozon-field-repair-report.json", repair_report)
    if errors:
        raise AttributeCompileError("; ".join(errors))
    all_compiled = [
        *common_attributes,
        *[item for values in attributes_by_sku.values() for item in values],
    ]
    required_items = [item for item in all_compiled if item["required"]]
    filled_required = [
        f"{item.get('sku_id', 'common')}:{item['attribute_id']}" for item in required_items
        if item["required"] and item["value"] != "unknown"
    ]
    missing_required = [
        f"{item.get('sku_id', 'common')}:{item['attribute_id']}" for item in required_items
        if item["value"] == "unknown"
    ]
    dependencies = {
        "attribute_fill_input_hash": fill_input.get("input_hash") or "",
        "ecommerce_design_hash": sha256_json(design.get("attribute_decisions") or {}),
        "compiler_version": COMPILER_VERSION,
    }
    result = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "category_id": int(fill_input["category_id"]),
        "type_id": int(fill_input["type_id"]),
        "schema_source": "ozon_seller_api",
        "compiler": dependencies,
        "common_attributes": common_attributes,
        "attributes_by_sku": attributes_by_sku,
        "sku_measurements": sku_measurements,
        "attributes": common_attributes,
        "required_summary": {
            "total": len(required_items),
            "filled": len(filled_required),
            "missing": len(missing_required),
            "missing_attribute_ids": missing_required,
        },
        "warnings": [
            "Attributes were compiled from ozon-ecommerce-designer semantic decisions and current live allowed_values.",
            "Unknown high-risk claims are omitted rather than invented; low-risk formatting issues are normalized automatically.",
        ],
    }
    write_json_atomic(output / "ozon-attributes-final.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    args = parser.parse_args()
    result = compile_product_attributes(Path(args.product_dir))
    compiled_count = len(result.get("common_attributes") or []) + sum(
        len(values or []) for values in (result.get("attributes_by_sku") or {}).values()
    )
    print(json.dumps({
        "product_id": result["product_id"],
        "attribute_count": compiled_count,
        "missing_required": result["required_summary"]["missing"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
