"""Decide whether collected SKUs use variant fields allowed by an Ozon category."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlsplit, urlunsplit

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_METADATA_ROOT = ROOT / "ozon-adapter" / "metadata"
ASPECT_RULE_ROOT = DEFAULT_METADATA_ROOT / "live-aspect-rules"
SCHEMA_PATH = ROOT / "templates" / "variant-decision.schema.json"
GROUPING_SCHEMA_PATH = ROOT / "templates" / "variant-grouping-result.schema.json"
PLATFORM_GROUPING_SCHEMA_PATH = ROOT / "templates" / "platform-grouping-result.schema.json"


class RuleDatabaseError(ValueError):
    pass


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


def latest_snapshot(metadata_root: Path = DEFAULT_METADATA_ROOT) -> Path:
    candidates = [
        path for path in metadata_root.iterdir()
        if path.is_dir() and (path / "version.json").is_file() and (path / "variants.json").is_file()
    ] if metadata_root.is_dir() else []
    if not candidates:
        raise RuleDatabaseError(f"No Ozon rule snapshot found under {metadata_root}")
    return max(candidates, key=lambda path: load_json(path / "version.json").get("updatedAt", ""))


def load_variant_rule(snapshot_dir: Path, category_id: int, type_id: int) -> Dict[str, Any]:
    aspect_path = ASPECT_RULE_ROOT / f"category-{category_id}-type-{type_id}.json"
    if aspect_path.is_file():
        cached = load_json(aspect_path)
        raw_items = (cached.get("raw_response") or {}).get("result", [])
        if not isinstance(raw_items, list):
            raw_items = []
        attributes = []
        for item in raw_items:
            if item.get("is_aspect") is not True:
                continue
            attribute_id = item.get("id", item.get("attribute_id"))
            name = str(item.get("name", item.get("attribute_name", ""))).strip()
            if attribute_id is not None and name:
                attributes.append({
                    "attributeId": str(attribute_id),
                    "nameRu": name,
                    "required": bool(item.get("is_required", item.get("required", False))),
                    "dictionaryId": item.get("dictionary_id") or None,
                    "isAspect": True,
                    "values": [],
                })
        return {
            "categoryId": str(category_id),
            "typeId": str(type_id),
            "attributes": attributes,
            "rule_data_complete": all(isinstance(item.get("is_aspect"), bool) for item in raw_items),
            "source": "local_official_ozon_aspect_metadata",
            "fetched_at": cached.get("fetched_at", "unknown"),
        }

    # The imported snapshot omits is_aspect, so its legacy variants list is
    # never evidence that a field can merge one Ozon product card.
    return {
        "categoryId": str(category_id),
        "typeId": str(type_id),
        "attributes": [],
        "rule_data_complete": False,
        "source": "legacy_snapshot_missing_is_aspect",
        "fetched_at": "unknown",
    }

def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", text)


def distinct(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        key = normalize(value)
        if key and key not in seen:
            seen.add(key)
            result.append(str(value).strip())
    return result


def likely_label_noise(values: List[str]) -> bool:
    """Treat small wording changes of the same base item as collection noise."""
    if len(values) < 2 or any(re.search(r"\d", normalize(value)) for value in values):
        return False
    normalized = [normalize(value) for value in values]
    ratios = [
        difflib.SequenceMatcher(None, left, right).ratio()
        for index, left in enumerate(normalized)
        for right in normalized[index + 1:]
    ]
    return bool(ratios) and min(ratios) >= 0.72


def difference_kind(source_name: str, values: List[str]) -> str:
    combined = normalize(source_name + " " + " ".join(values))
    has_color = any(token in combined for token in ("颜色", "色号", "colour", "color")) or bool(
        re.search(r"(?:黑|白|红|蓝|绿|银|金|粉|紫|灰|棕|橙|黄)色", combined)
    )
    if has_color:
        # 1688 often packs color and another option into one text value, for
        # example "银色>iPhone17Pro". It is a color variant only when the
        # non-color remainder is identical across all selected SKUs.
        color_words = re.compile(
            r"(?:浅|深|亮|哑光|磨砂)?(?:黑|白|红|蓝|绿|银|金|粉|紫|灰|棕|橙|黄|咖啡|卡其)色?"
            r"|湖水蓝|天蓝|藏青|玫红|粉红"
        )
        remainders = distinct(color_words.sub("", normalize(value)).strip(">/#*-_ ") for value in values)
        if len(remainders) > 1:
            remainder_text = " ".join(remainders)
            if any(token in remainder_text for token in ("尺寸", "尺码", "长度", "宽度", "高度", "cm", "mm")):
                return "size_or_measurement"
            if re.search(r"\d+(?:\.\d+)?(?:个|片|件|套|只|支|枚|包|组|pcs?)", remainder_text):
                return "configuration"
            if any(token in remainder_text for token in (
                "iphone", "ipad", "三星", "华为", "小米", "oppo", "vivo", "型号", "款式", "版本",
            )):
                return "model_or_style"
            return "unknown"
    if has_color and any(token in combined for token in ("背光", "灯光", "发光", "无光")):
        return "configuration"
    if has_color:
        return "color"
    if any(token in combined for token in ("尺寸", "尺码", "长度", "宽度", "高度", "容量", "容积", "重量", "斤装")):
        return "size_or_measurement"
    if re.search(r"\d+(?:\.\d+)?(?:斤|公斤|千克|kg|毫升|ml|升)", combined):
        return "size_or_measurement"
    if any(token in combined for token in ("套装", "套餐", "组合", "配件", "包装")):
        return "configuration"
    if re.search(r"\d+(?:\.\d+)?(?:个|片|件|套|只|支|枚|包|组|pcs?)", combined):
        return "configuration"
    if any(token in combined for token in ("型号", "款式", "版本")):
        return "model_or_style"
    return "unknown"


def field_matches(
    kind: str,
    allowed: List[Dict[str, Any]],
    source_name: str = "",
    values: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    def contains(field: Dict[str, Any], fragments: Iterable[str]) -> bool:
        name = str(field.get("nameRu", "")).casefold()
        return any(fragment in name for fragment in fragments)

    if kind == "color":
        selected = [field for field in allowed if contains(field, ("цвет",))]
    elif kind == "configuration":
        # A textual kit composition preserves facts better than guessing a total item count.
        preferred = [field for field in allowed if str(field.get("attributeId")) == "4384"]
        selected = preferred or [
            field for field in allowed
            if contains(field, ("комплектац", "количество предметов", "количество в упаковке"))
        ]
    elif kind == "size_or_measurement":
        measurement_text = normalize(source_name + " " + " ".join(values or []))
        is_capacity = any(token in measurement_text for token in (
            "容量", "容积", "斤装", "毫升", "ml", "升",
        )) or bool(re.search(r"\d+(?:\.\d+)?(?:斤|公斤|千克|kg)", measurement_text))
        if is_capacity:
            selected = [field for field in allowed if contains(field, ("объем",))]
        else:
            selected = [
                field for field in allowed
                if contains(field, ("размер", "длина", "ширина", "высота", "объем", "вес"))
            ]
    elif kind == "model_or_style":
        selected = [field for field in allowed if contains(field, ("модель", "вариант", "тип"))]
    else:
        selected = []
    return [
        {"attribute_id": int(field["attributeId"]), "attribute_name": field["nameRu"]}
        for field in selected
    ]


def detect_differences(skus: List[Dict[str, Any]], allowed: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[str]]:
    dimensions: Dict[str, List[str]] = {}
    for sku in skus:
        for option in sku.get("option_values", []):
            name = str(option.get("name_cn") or "unknown").strip()
            value = str(option.get("value_cn") or "unknown").strip()
            dimensions.setdefault(name, []).append(value)

    differences = []
    warnings = []
    for name, raw_values in dimensions.items():
        values = distinct(raw_values)
        if len(values) <= 1:
            continue
        if likely_label_noise(values):
            warnings.append(
                f"Ignored likely source-label inconsistency in {name}: {', '.join(values)}"
            )
            continue
        kind = difference_kind(name, values)
        matches = field_matches(kind, allowed, name, values)
        differences.append({
            "source_field": name,
            "source_values": values,
            "difference_kind": kind,
            "mapped_variant_fields": matches,
            "compatible": bool(matches),
        })

    if not differences and len(skus) > 1:
        sku_names = distinct(str(sku.get("sku_name") or "") for sku in skus)
        if len(sku_names) > 1:
            kind = difference_kind("sku_configuration", sku_names)
            matches = field_matches(kind, allowed, "sku_configuration", sku_names)
            differences.append({
                "source_field": "sku_configuration",
                "source_values": sku_names,
                "difference_kind": kind,
                "mapped_variant_fields": matches,
                "compatible": bool(matches),
            })
    return differences, warnings


def build_variant_decision(
    product_id: str,
    source: Dict[str, Any],
    category_id: int,
    type_id: int,
    variant_rule: Dict[str, Any],
    rule_version: str,
) -> Dict[str, Any]:
    # Only Ozon's explicit aspect fields can distinguish offers in one card.
    # A normal category attribute may differ per SKU, but that is not evidence
    # that Ozon will merge the offers.
    rule_data_complete = variant_rule.get("rule_data_complete") is True
    allowed_source = [
        item for item in variant_rule.get("attributes", [])
        if item.get("isAspect") is True
    ] if rule_data_complete else []
    allowed = [
        {
            "attribute_id": int(item["attributeId"]),
            "attribute_name": str(item["nameRu"]),
            "required": bool(item.get("required", False)),
        }
        for item in allowed_source
    ]
    skus = source.get("skus", [])
    differences, warnings = detect_differences(skus, allowed_source)
    mapping_supported = bool(differences) and all(
        item["compatible"] for item in differences
    )
    internal_product_group = len(skus) > 1
    platform_can_merge = bool(
        internal_product_group and rule_data_complete and mapping_supported
    )
    mapped_fields = [
        field for item in differences for field in item["mapped_variant_fields"]
    ]
    difference_type = (
        mapped_fields[0]["attribute_name"]
        if mapped_fields else (differences[0]["difference_kind"] if differences else "single_sku")
    )
    if len(skus) <= 1:
        confidence = 100
        reasons = ["Only one selected SKU exists, so the product is a single-SKU group."]
    elif not differences:
        confidence = 100
        reasons = [
            "All selected SKUs come from the same collected product and must remain one product group.",
            "No reliable Ozon variant attribute mapping was found; a local rule is required before upload.",
        ]
    elif not rule_data_complete:
        confidence = 0
        reasons = [
            "The local category metadata does not contain official is_aspect flags.",
            "No SKU difference is treated as merge-compatible until official aspect metadata is cached.",
        ]
    elif mapping_supported:
        confidence = 100
        mapped = sorted({
            f"{field['attribute_name']} ({field['attribute_id']})"
            for item in differences for field in item["mapped_variant_fields"]
        })
        reasons = [
            "All selected SKUs come from the same collected product and remain one internal product group.",
            "Official Ozon aspect metadata maps every detected SKU difference to: " + ", ".join(mapped),
        ]
    else:
        confidence = 100
        unsupported = [item["source_field"] for item in differences if not item["compatible"]]
        reasons = [
            "All selected SKUs come from the same collected product and remain one internal product group.",
            "Official Ozon aspect metadata cannot map these SKU differences: " + ", ".join(unsupported),
        ]

    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rule_source": str(variant_rule.get("source") or "unknown"),
        "rule_version": rule_version,
        "category_id": category_id,
        "type_id": type_id,
        "sku_count": len(skus),
        "can_merge": platform_can_merge,
        "internal_product_group": internal_product_group,
        "platform_can_merge": platform_can_merge,
        "variant_rule_data_incomplete": not rule_data_complete,
        "confidence": confidence,
        "difference_type": difference_type,
        "matched_rule_source": str(variant_rule.get("source") or "unknown"),
        "mapping_supported": mapping_supported,
        "allowed_variant_fields": allowed,
        "detected_difference_fields": differences,
        "reason": reasons,
        "warnings": warnings,
    }


def validate_variant_decision(value: Dict[str, Any]) -> List[str]:
    validator = Draft202012Validator(load_json(SCHEMA_PATH))
    errors = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def validate_grouping_result(value: Dict[str, Any]) -> List[str]:
    validator = Draft202012Validator(load_json(GROUPING_SCHEMA_PATH))
    errors = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def build_platform_grouping_result(decision: Dict[str, Any]) -> Dict[str, Any]:
    sku_count = int(decision["sku_count"])
    platform_can_merge = bool(decision.get("platform_can_merge", False))
    if sku_count == 1:
        strategy = "single_sku"
        card_count = 1
        reason = "One selected SKU creates one Ozon product card."
    elif platform_can_merge:
        strategy = "merged_variants"
        card_count = 1
        reason = "Every detected SKU difference maps to an official Ozon aspect attribute."
    elif decision.get("variant_rule_data_incomplete"):
        strategy = "rule_required"
        card_count = sku_count
        reason = "Official is_aspect metadata is unavailable, so platform card merging is not inferred."
    else:
        strategy = "separate_cards"
        card_count = sku_count
        reason = "The selected SKU differences do not map to an official Ozon aspect attribute in this category."
    mapped = [
        field for difference in decision["detected_difference_fields"]
        for field in difference["mapped_variant_fields"]
    ]
    return {
        "internal_group_count": 1,
        "platform_card_count": card_count,
        "platform_can_merge": platform_can_merge,
        "allowed_aspect_attributes": decision["allowed_variant_fields"],
        "detected_sku_differences": decision["detected_difference_fields"],
        "mapped_aspect_attributes": mapped,
        "upload_strategy": strategy,
        "reason": reason,
    }


def validate_platform_grouping_result(value: Dict[str, Any]) -> List[str]:
    validator = Draft202012Validator(load_json(PLATFORM_GROUPING_SCHEMA_PATH))
    errors = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def canonical_source_url(value: str) -> str:
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return urlunsplit((scheme, host, path, "", ""))


def source_product_id(source: Dict[str, Any]) -> str:
    explicit = str(source.get("source_product_id") or "").strip()
    if explicit:
        return explicit
    raw_url = str(source.get("source_url") or "")
    parsed = urlsplit(raw_url)
    query = parse_qs(parsed.query)
    for key in ("offerId", "offer_id", "id"):
        if query.get(key) and str(query[key][0]).isdigit():
            return str(query[key][0])
    match = re.search(r"/offer/(\d+)\.html", parsed.path)
    return match.group(1) if match else "unknown"


def _sku_option_value(sku: Dict[str, Any], source_field: str) -> str:
    for option in sku.get("option_values", []):
        if str(option.get("name_cn") or "") == source_field:
            return str(option.get("value_cn") or "unknown")
    return "unknown"


def _configuration_value(model_name: str, source_value: str, fallback: str) -> str:
    disc_match = re.search(r"(\d+)\s*个?磨片", source_value)
    if disc_match and "точил" in model_name.casefold():
        count = int(disc_match.group(1))
        noun = "заточных диска" if count in {2, 3, 4} else "заточных дисков"
        return f"Электрическая точилка, {count} {noun}"
    return fallback


def _cached_attribute_values(category_id: int, type_id: int, attribute_id: int) -> List[Dict[str, Any]]:
    cache_root = DEFAULT_METADATA_ROOT / "live-category-cache"
    candidates = list(cache_root.glob(
        f"*/category-{category_id}-type-{type_id}/attribute-{attribute_id}-values.json"
    )) if cache_root.is_dir() else []
    if not candidates:
        return []
    data = load_json(max(candidates, key=lambda path: path.stat().st_mtime))
    return [item for item in data.get("values") or [] if isinstance(item, dict)]


def _capacity_variant_value(
    source_value: str,
    category_id: int,
    type_id: int,
    attribute_id: int,
) -> Optional[Dict[str, Any]]:
    normalized = unicodedata.normalize("NFKC", source_value)
    jin_match = re.search(r"(\d+(?:\.\d+)?)\s*斤", normalized)
    if not jin_match:
        return None
    jin = float(jin_match.group(1))
    # Chinese rice-bin labels use jin as nominal rice capacity. One jin of
    # uncooked rice occupies roughly 625 ml; the Ozon value remains explicitly estimated.
    target_ml = int(round(jin * 625))
    values = _cached_attribute_values(category_id, type_id, attribute_id)
    selected = next(
        (
            item for item in values
            if (match := re.search(r"(\d+(?:[.,]\d+)?)\s*мл", str(item.get("value") or ""), re.IGNORECASE))
            and abs(float(match.group(1).replace(",", ".")) - target_ml) < 0.01
        ),
        None,
    )
    return {
        "value": str(selected.get("value") if selected else f"{target_ml} мл"),
        "dictionary_value_id": int(selected["id"]) if selected and selected.get("id") is not None else None,
        "estimated": True,
        "confidence": 0.75,
        "conversion_note": f"{jin:g}斤按大米常用体积估算为约{target_ml}毫升（1斤≈625毫升）",
    }


def build_grouping_result(
    product_id: str,
    source: Dict[str, Any],
    decision: Dict[str, Any],
    draft: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    raw_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    skus = source.get("skus", [])
    selected_count = len(skus)
    source_id = source_product_id(source)
    canonical_url = canonical_source_url(str(source.get("source_url") or ""))
    selected_at = str(
        ((raw_snapshot or {}).get("sku_selection") or {}).get("selected_at")
        or source.get("captured_at")
        or "unknown"
    )
    if source_id != "unknown":
        grouping_rule = "same_source_product"
        group_seed = f"1688:{source_id}"
    elif canonical_url:
        grouping_rule = "same_canonical_source_url"
        group_seed = canonical_url
    elif product_id:
        grouping_rule = "same_collection_product_id"
        group_seed = product_id
    else:
        grouping_rule = "same_sku_selection_task"
        group_seed = selected_at
    group_hash = hashlib.sha256(group_seed.encode("utf-8")).hexdigest()[:12].upper()
    product_group_id = f"PG-{group_hash}"

    internal_product_group = selected_count > 1
    platform_can_merge = bool(decision.get("platform_can_merge", decision["mapping_supported"]))
    if not internal_product_group:
        mapping_status = "NOT_REQUIRED"
    elif platform_can_merge:
        mapping_status = "MAPPED"
    else:
        mapping_status = "SEPARATE_CARDS_REQUIRED"

    mapped_fields = []
    for difference in decision["detected_difference_fields"]:
        for field in difference["mapped_variant_fields"]:
            if field not in mapped_fields:
                mapped_fields.append(field)
    primary_attribute = mapped_fields[0] if mapped_fields else None
    draft_skus = {
        str(item["source_sku_id"]): item for item in (draft or {}).get("skus", [])
    }
    price_config = {
        str(item["source_sku_id"]): item["price"]
        for item in (config or {}).get("sku_prices", [])
    }
    color_config = {
        str(item["source_sku_id"]): str(item["value"])
        for item in (config or {}).get("sku_colors", [])
    }
    model = (config or {}).get("model_name", {})
    model_name = str(model.get("value") or "unknown")
    difference_fields = decision["detected_difference_fields"]
    variants = []
    for order, sku in enumerate(skus, start=1):
        sku_id = str(sku.get("sku_id") or "unknown")
        draft_sku = draft_skus.get(sku_id, {})
        attribute_values = []
        for difference in difference_fields:
            source_value = _sku_option_value(sku, difference["source_field"])
            for field in difference["mapped_variant_fields"]:
                value = (
                    _configuration_value(
                        model_name,
                        source_value,
                        str(draft_sku.get("display_name_ru") or source_value),
                    )
                    if field["attribute_id"] == 4384 else source_value
                )
                if difference["difference_kind"] == "color" and field["attribute_id"] in {10096, 10097}:
                    value = color_config.get(sku_id, source_value)
                attribute_value = {
                    "attribute_id": field["attribute_id"],
                    "attribute_name": field["attribute_name"],
                    "value": value,
                    "source_value": source_value,
                }
                if "объем" in str(field["attribute_name"]).casefold():
                    capacity = _capacity_variant_value(
                        source_value,
                        int(decision["category_id"]),
                        int(decision["type_id"]),
                        int(field["attribute_id"]),
                    )
                    if capacity:
                        attribute_value.update(capacity)
                attribute_values.append(attribute_value)
        variants.append({
            "selection_order": int(sku.get("selection_order") or order),
            "sku_id": sku_id,
            "offer_id": str(draft_sku.get("offer_id") or f"{product_id}-{sku_id}"),
            "sku_name": str(sku.get("sku_name") or "unknown"),
            "variant_attribute_values": attribute_values,
            "purchase_price_cny": sku.get("purchase_price"),
            "selling_price": price_config.get(sku_id, "unknown"),
            "currency_code": str((config or {}).get("currency_code") or "unknown"),
            "image": str(sku.get("local_image_path") or "unknown"),
        })

    brand = (config or {}).get("brand", {})
    result = {
        "schema_version": "1.0.0",
        "product_group_id": product_group_id,
        "source_product_id": source_id,
        "canonical_source_url": canonical_url or "unknown",
        "collection_product_id": product_id,
        "sku_selection_task": selected_at,
        "selected_sku_count": selected_count,
        "product_group_count": 1,
        "variant_count": selected_count,
        "grouping_rule": grouping_rule,
        "must_merge": internal_product_group,
        "internal_product_group": internal_product_group,
        "internal_group_count": 1,
        "platform": "ozon",
        "platform_can_merge": platform_can_merge,
        "upload_strategy": "single_card_variants" if platform_can_merge else "separate_cards",
        "variant_mapping_status": mapping_status,
        "upload_allowed": mapping_status != "SEPARATE_CARDS_REQUIRED",
        "category_id": decision["category_id"],
        "type_id": decision["type_id"],
        "model_name_for_merge": model_name,
        "common_product_name": str(
            (config or {}).get("merge_product_name")
            or (draft or {}).get("title")
            or "unknown"
        ),
        "common_attributes": {
            "brand": str(brand.get("value") or "unknown"),
            "model_name": str(model.get("value") or "unknown"),
            "product_type": str((config or {}).get("type", {}).get("value") or "unknown"),
        },
        "variant_attribute": primary_attribute,
        "variant_attributes": mapped_fields,
        "variants": variants,
        "mapping_requirements": {
            "difference_types": [item["difference_kind"] for item in difference_fields],
            "allowed_variant_fields": decision["allowed_variant_fields"],
            "missing_rule": (
                "unknown SKU difference requires a local Ozon variant mapping"
                if mapping_status == "SEPARATE_CARDS_REQUIRED" else None
            ),
        },
        "warnings": decision["warnings"],
    }
    return result


def evaluate_product(product_dir: Path, snapshot_dir: Optional[Path] = None, write: bool = True) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    source = load_json(product_dir / "input" / "source.json")
    category = load_json(product_dir / "output" / "ozon-category.json")
    snapshot = snapshot_dir.resolve() if snapshot_dir else latest_snapshot()
    version = load_json(snapshot / "version.json")
    category_id = int(category["category_id"])
    type_id = int(category["type_id"])
    rule = load_variant_rule(snapshot, category_id, type_id)
    decision = build_variant_decision(
        product_dir.name,
        source,
        category_id,
        type_id,
        rule,
        str(version["version"]),
    )
    errors = validate_variant_decision(decision)
    if errors:
        raise RuleDatabaseError("Generated variant decision failed schema validation: " + "; ".join(errors))
    draft_path = product_dir / "output" / "ozon-draft.json"
    config_path = product_dir / "output" / "ozon-upload-config.json"
    raw_path = product_dir / "input" / "raw-snapshot.json"
    grouping = build_grouping_result(
        product_dir.name,
        source,
        decision,
        load_json(draft_path) if draft_path.is_file() else None,
        load_json(config_path) if config_path.is_file() else None,
        load_json(raw_path) if raw_path.is_file() else None,
    )
    grouping_errors = validate_grouping_result(grouping)
    if grouping_errors:
        raise RuleDatabaseError(
            "Generated grouping result failed schema validation: " + "; ".join(grouping_errors)
        )
    platform_grouping = build_platform_grouping_result(decision)
    platform_errors = validate_platform_grouping_result(platform_grouping)
    if platform_errors:
        raise RuleDatabaseError(
            "Generated platform grouping result failed schema validation: " + "; ".join(platform_errors)
        )
    if write:
        write_json_atomic(product_dir / "output" / "variant-decision.json", decision)
        write_json_atomic(product_dir / "output" / "variant-grouping-result.json", grouping)
        write_json_atomic(product_dir / "output" / "platform-grouping-result.json", platform_grouping)
    return {"variant_decision": decision, "variant_grouping_result": grouping, "platform_grouping_result": platform_grouping}
