#!/usr/bin/env python3
"""Build the exact attribute-decision input for ozon-ecommerce-designer."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

try:
    from scripts.product_fact_merger import build_product_fact_lock, merge_product_facts, sha256_json, file_sha256
    from scripts.store_publications import load_publications
except ModuleNotFoundError:
    from product_fact_merger import build_product_fact_lock, merge_product_facts, sha256_json, file_sha256
    from store_publications import load_publications

ROOT = Path(__file__).resolve().parents[1]
BUILDER_VERSION = "attribute-fill-input-v5-category-attribute-plan"
COMPACT_ALLOWED_VALUES_THRESHOLD = 1000
COMPACT_ALLOWED_VALUES_LIMIT = 160
COMPACT_FILENAME = "attribute-fill-input.compact.json"
DESIGN_CONTEXT_FILENAME = "ecommerce-design-context.json"
BRAND_PROMPT_ALLOWED_VALUES = {"нет бренда", "без бренда", "jlc global"}


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(path)


def load_optional_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_allowed_values(values: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        value_id = item.get("dictionary_value_id", item.get("id"))
        value = item.get("value")
        if value_id is None or value in {None, ""}:
            continue
        result.append({"value": str(value), "dictionary_value_id": int(value_id)})
    return result


def stable_metadata_contract_hash(metadata: Dict[str, Any]) -> str:
    """Hash only the Ozon category attribute contract, not cache timestamps."""
    attributes: List[Dict[str, Any]] = []
    for raw in metadata.get("attributes") or []:
        if not isinstance(raw, dict):
            continue
        values = normalize_allowed_values(raw.get("allowed_values"))
        attributes.append({
            "attribute_id": int(raw.get("attribute_id") or 0),
            "complex_id": int(raw.get("complex_id") or 0),
            "attribute_name": str(raw.get("attribute_name") or ""),
            "type": str(raw.get("type") or ""),
            "required": bool(raw.get("required")),
            "is_collection": bool(raw.get("is_collection")),
            "dictionary_id": int(raw.get("dictionary_id") or 0),
            "allowed_values": sorted(
                values,
                key=lambda item: (
                    int(item.get("dictionary_value_id") or 0),
                    str(item.get("value") or ""),
                ),
            ),
        })
    return sha256_json({
        "category_id": int(metadata.get("category_id") or 0),
        "type_id": int(metadata.get("type_id") or 0),
        "attributes": sorted(
            attributes,
            key=lambda item: (
                int(item.get("attribute_id") or 0),
                int(item.get("complex_id") or 0),
                str(item.get("attribute_name") or ""),
            ),
        ),
    })


def _walk_strings(value: Any, out: List[str], *, max_items: int = 2500) -> None:
    if len(out) >= max_items:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            out.append(text)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_strings(item, out, max_items=max_items)
            if len(out) >= max_items:
                return
        return
    if isinstance(value, list):
        for item in value:
            _walk_strings(item, out, max_items=max_items)
            if len(out) >= max_items:
                return


def compact_corpus(value: Dict[str, Any]) -> Set[str]:
    strings: List[str] = []
    _walk_strings(
        {
            "selected_skus": value.get("selected_skus"),
            "sku_rows": value.get("sku_rows"),
            "merged_facts": value.get("merged_facts"),
            "measurements": value.get("measurements"),
        },
        strings,
    )
    tokens: Set[str] = set()
    for text in strings:
        lowered = text.casefold()
        tokens.add(lowered)
        for token in re.split(r"[\s,，;；/|、()（）\[\]{}<>:：\"'«»]+", lowered):
            token = token.strip()
            if len(token) >= 2:
                tokens.add(token)
    tokens.update(
        {
            "нет бренда",
            "без бренда",
            "китай",
            "унисекс",
            "мужской",
            "женский",
            "черный",
            "белый",
            "серый",
            "зеленый",
            "хаки",
            "прозрачный",
        }
    )
    return tokens


def _value_matches_corpus(value: str, corpus: Set[str]) -> bool:
    lowered = value.casefold().strip()
    if not lowered:
        return False
    if lowered in corpus:
        return True
    return any(token and (token in lowered or lowered in token) for token in corpus if len(token) >= 3)


def compact_allowed_values(
    attribute: Dict[str, Any],
    corpus: Set[str],
    *,
    threshold: int = COMPACT_ALLOWED_VALUES_THRESHOLD,
    limit: int = COMPACT_ALLOWED_VALUES_LIMIT,
) -> Dict[str, Any]:
    """Return an attribute copy suitable for connected-model input.

    The full `attribute-fill-input.json` remains authoritative for deterministic
    compilation and contract validation. This sidecar only removes huge,
    irrelevant dictionary payloads from the commercial-design prompt.
    """
    item = dict(attribute)
    values = list(attribute.get("allowed_values") or [])
    total = len(values)
    if total <= threshold:
        item["allowed_values_total"] = total
        item["allowed_values_compacted"] = False
        item["omitted_allowed_value_count"] = 0
        return item

    kept: List[Dict[str, Any]] = []
    seen: Set[int] = set()

    def keep(candidate: Dict[str, Any]) -> None:
        value_id = candidate.get("dictionary_value_id")
        if value_id is None:
            return
        int_id = int(value_id)
        if int_id in seen or len(kept) >= limit:
            return
        seen.add(int_id)
        kept.append(candidate)

    name = str(attribute.get("attribute_name") or "").casefold()
    attr_id = int(attribute.get("attribute_id") or 0)

    if attr_id == 85 or "бренд" in name or "brand" in name:
        # Brand dictionaries can contain tens of thousands of irrelevant values.
        # Project policy allows only no-brand and JLC GLOBAL as prompt options.
        # Do not keep arbitrary samples: they slow design and can make the model
        # pick a wrong brand from a huge dictionary.
        for candidate in values:
            candidate_value = str(candidate.get("value") or "").strip().casefold()
            if candidate_value in BRAND_PROMPT_ALLOWED_VALUES:
                keep(candidate)
        item["allowed_values"] = kept
        item["allowed_values_total"] = total
        item["allowed_values_compacted"] = True
        item["omitted_allowed_value_count"] = max(total - len(kept), 0)
        item["compaction_note"] = (
            "Full dictionary is available in output/attribute-fill-input.json; "
            "compact input keeps only no-brand and JLC GLOBAL brand values when present."
        )
        return item

    for candidate in values:
        if _value_matches_corpus(str(candidate.get("value") or ""), corpus):
            keep(candidate)

    if len(kept) < min(limit, 25):
        for candidate in values:
            keep(candidate)
            if len(kept) >= min(limit, 80):
                break

    item["allowed_values"] = kept
    item["allowed_values_total"] = total
    item["allowed_values_compacted"] = True
    item["omitted_allowed_value_count"] = max(total - len(kept), 0)
    item["compaction_note"] = (
        "Compact input keeps relevant/sample values for connected design. "
        "Use full output/attribute-fill-input.json for exact final validation."
    )
    return item


def build_compact_attribute_fill_input(value: Dict[str, Any]) -> Dict[str, Any]:
    corpus = compact_corpus(value)
    full_allowed_count = sum(len(item.get("allowed_values") or []) for item in value.get("ozon_attributes") or [])
    compact_attributes = [
        compact_allowed_values(attribute, corpus)
        for attribute in (value.get("ozon_attributes") or [])
    ]
    compact_value = dict(value)
    compact_value["ozon_attributes"] = compact_attributes
    compact_allowed_count = sum(len(item.get("allowed_values") or []) for item in compact_attributes)
    compact_value["compact_input"] = {
        "schema_version": "1.0.0",
        "source": "output/attribute-fill-input.json",
        "source_input_hash": value.get("input_hash"),
        "full_attribute_count": len(value.get("ozon_attributes") or []),
        "full_allowed_values_count": full_allowed_count,
        "compact_allowed_values_count": compact_allowed_count,
        "compaction_rule": (
            "Full attribute input is unchanged and remains authoritative for "
            "compiler/contract validation. Connected ecommerce_design should "
            "use this compact sidecar first and consult the full file only for "
            "a specific missing dictionary value."
        ),
    }
    return compact_value


def _fact_value(value: Any, default: Any = "unknown") -> Any:
    if isinstance(value, dict) and "canonical_value" in value:
        current = value.get("canonical_value")
        return default if current in (None, "") else current
    return default if value in (None, "") else value


def build_ecommerce_design_context(value: Dict[str, Any]) -> Dict[str, Any]:
    """Small input for the visual/copy designer; full attributes remain authoritative."""
    source = value.get("source") or {}
    merged = value.get("merged_facts") or {}
    category = value.get("category") or merged.get("category") or {}
    source_title = (
        source.get("title_cn")
        or merged.get("title_cn")
        or (merged.get("facts") or {}).get("title_cn")
        or value.get("title_cn")
    )
    if not category.get("category_name") and not category.get("category_name_ru"):
        metadata_category = {
            "category_id": value.get("category_id"),
            "type_id": value.get("type_id"),
        }
        category = {**metadata_category, **category}
    selected_skus = []
    for sku in value.get("selected_skus") or merged.get("selected_skus") or []:
        row = sku.get("sku_row") or sku
        selected_skus.append({
            "sku_id": str(sku.get("sku_id") or row.get("sku_id") or ""),
            "sku_name": str(sku.get("sku_name") or row.get("sku_name") or ""),
            "option_text": str(row.get("option_text") or ""),
            "color": _fact_value(row.get("color")),
            "specification": _fact_value(row.get("specification")),
            "image_path": str(sku.get("image_path") or row.get("image_path") or ""),
        })
    design_attributes = []
    for attribute in value.get("ozon_attributes") or []:
        if not attribute.get("required") and len(design_attributes) >= 16:
            continue
        design_attributes.append({
            "id": int(attribute.get("attribute_id") or attribute.get("id") or 0),
            "name": str(attribute.get("attribute_name") or attribute.get("name") or ""),
            "required": bool(attribute.get("required")),
            "dictionary": bool(attribute.get("dictionary_id")),
        })
        if len(design_attributes) >= 24:
            break
    return {
        "schema_version": "1.0.0",
        "product_id": value.get("product_id"),
        "collection_id": value.get("collection_id"),
        "source_kind": value.get("source_kind"),
        "source_title_cn": source_title,
        "category": {
            "category_id": category.get("category_id") or value.get("category_id"),
            "type_id": category.get("type_id") or value.get("type_id"),
            "category_name": category.get("category_name") or category.get("category_name_ru"),
            "category_path": category.get("category_path") or [],
        },
        "input_hash": value.get("input_hash"),
        "selected_skus": selected_skus[:10],
        "design_attribute_hints": design_attributes,
        "design_rule": (
            "Use this small file for ecommerce design. Full Ozon attributes are compiled later by field_completion; "
            "do not enumerate dictionaries or required fields here."
        ),
    }


def store_cluster_design_context(product_dir: Path) -> Dict[str, Any]:
    """Expose only selected-store positioning, never credentials, to the designer."""
    # Publication selection is SQLite-backed after cutover.  Reading the old
    # JSON file here lost selected stores for resumed products, so the designer
    # silently produced one master card for every store.  Always use the same
    # projection used by the uploader.
    publications = load_publications(product_dir)
    selected = {
        str(store_id)
        for store_id, record in (publications.get("stores") or {}).items()
        if isinstance(record, dict) and record.get("selected")
    }
    registry = load_optional_json(ROOT / "ozon-adapter/shops.json")
    profiles = []
    for shop in registry.get("shops") or []:
        if not isinstance(shop, dict):
            continue
        store_id = str(shop.get("id") or shop.get("name") or "")
        if store_id not in selected:
            continue
        profiles.append({
            "store_id": store_id,
            "store_profile": str(shop.get("store_profile") or "standard"),
            "business_entity": str(shop.get("business_entity") or "unknown"),
        })
    return {
        "single_entity_duplicate_rule": (
            "A different title, price or image is not evidence of a different physical product. "
            "Make a separate listing/image variant only for each selected store from a different business entity."
        ),
        "selected_stores": profiles,
    }


def aspect_ids_for(metadata: Dict[str, Any]) -> set[int]:
    path = ROOT / "ozon-adapter/metadata/live-aspect-rules" / (
        f"category-{metadata['category_id']}-type-{metadata['type_id']}.json"
    )
    if not path.is_file():
        return set()
    raw = load_json(path)
    result = set()
    for item in ((raw.get("raw_response") or {}).get("result") or raw.get("attributes") or []):
        attribute_id = item.get("attribute_id", item.get("id"))
        if attribute_id is not None and item.get("is_aspect") is True:
            result.add(int(attribute_id))
    return result


def normalize_attribute(item: Dict[str, Any], aspect_ids: set[int]) -> Dict[str, Any]:
    attribute_id = int(item["attribute_id"])
    return {
        "attribute_id": attribute_id,
        "attribute_name": str(item.get("attribute_name") or item.get("name") or ""),
        "description": str(item.get("description") or item.get("attribute_description") or ""),
        "type": str(item.get("type") or "String"),
        "required": bool(item.get("required") or item.get("is_required")),
        "is_aspect": bool(item.get("is_aspect") or attribute_id in aspect_ids),
        "is_collection": bool(item.get("is_collection")),
        "max_value_count": int(item.get("max_value_count") or item.get("max_values_count") or 1),
        "dictionary_id": item.get("dictionary_id"),
        "allowed_values": normalize_allowed_values(item.get("allowed_values")),
    }


def _attribute_value_kind(attribute: Dict[str, Any]) -> str:
    if attribute.get("allowed_values"):
        return "dictionary"
    attr_type = str(attribute.get("type") or "").casefold()
    if attr_type in {"integer", "int32", "int64"}:
        return "integer"
    if attr_type in {"decimal", "double", "float"}:
        return "decimal"
    if attr_type in {"boolean", "bool"}:
        return "boolean"
    return "text"


def _attribute_physical_dimension(attribute: Dict[str, Any]) -> str:
    name = str(attribute.get("attribute_name") or "").casefold()
    description = str(attribute.get("description") or "").casefold()
    text = f"{name} {description}"
    if any(token in text for token in ("вес", "weight", "масса")):
        return "weight"
    if any(token in text for token in ("объем", "объём", "литр", "мл", "мл.", "capacity", "volume")):
        return "capacity"
    if any(token in text for token in ("длина", "ширина", "высота", "глубина", "размер", "габарит", "см", "мм")):
        return "dimension"
    if any(token in text for token in ("количество", "штук", "шт", "единиц", "pcs")):
        return "quantity"
    if any(token in text for token in ("цвет", "color")):
        return "color"
    if any(token in text for token in ("материал", "material")):
        return "material"
    if any(token in text for token in ("бренд", "brand")):
        return "brand"
    return "text"


def _attribute_plan_priority(attribute: Dict[str, Any]) -> str:
    required = bool(attribute.get("required"))
    aspect = bool(attribute.get("is_aspect"))
    if required and aspect:
        return "required_sku_variant"
    if required:
        return "required_common"
    if aspect:
        return "sku_variant_optional"
    return "optional_common"


def _attribute_plan_handling(attribute: Dict[str, Any]) -> str:
    value_kind = _attribute_value_kind(attribute)
    physical = _attribute_physical_dimension(attribute)
    if value_kind == "dictionary":
        return "match_current_ozon_dictionary_value"
    if physical in {"weight", "capacity", "dimension", "quantity"}:
        return "derive_or_estimate_measurement_then_convert_to_ozon_unit"
    if physical in {"color", "material"}:
        return "derive_from_current_sku_or_product_facts"
    if bool(attribute.get("required")):
        return "fill_from_current_product_facts_or_mark_unknown_high_risk"
    return "fill_only_when_low_risk_or_skip_optional"


def build_category_attribute_plan(attributes: List[Dict[str, Any]]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for attribute in attributes:
        allowed_values = attribute.get("allowed_values") or []
        item = {
            "attribute_id": int(attribute["attribute_id"]),
            "attribute_name": str(attribute.get("attribute_name") or ""),
            "required": bool(attribute.get("required")),
            "is_aspect": bool(attribute.get("is_aspect")),
            "fill_scope": "sku" if attribute.get("is_aspect") else "common",
            "priority": _attribute_plan_priority(attribute),
            "value_kind": _attribute_value_kind(attribute),
            "physical_dimension": _attribute_physical_dimension(attribute),
            "recommended_handling": _attribute_plan_handling(attribute),
            "allowed_values_count": len(allowed_values),
            "must_decide": True,
        }
        if allowed_values and len(allowed_values) <= 12:
            item["dictionary_examples"] = allowed_values[:12]
        items.append(item)
    return {
        "schema_version": "1.0.0",
        "purpose": (
            "Drive ecommerce_design attribute decisions from the selected Ozon "
            "category fields before final deterministic compilation."
        ),
        "required_attribute_ids": [
            item["attribute_id"] for item in items if item["required"]
        ],
        "aspect_attribute_ids": [
            item["attribute_id"] for item in items if item["is_aspect"]
        ],
        "dictionary_attribute_ids": [
            item["attribute_id"] for item in items if item["value_kind"] == "dictionary"
        ],
        "numeric_attribute_ids": [
            item["attribute_id"] for item in items if item["value_kind"] in {"integer", "decimal"}
        ],
        "decision_rule": (
            "For every item, output a filled, estimated, unknown_high_risk, "
            "not_applicable, or skipped_optional decision in attribute_decisions. "
            "SKU/aspect fields must be decided per selected SKU."
        ),
        "items": items,
        "summary": {
            "total": len(items),
            "required": sum(1 for item in items if item["required"]),
            "aspect": sum(1 for item in items if item["is_aspect"]),
            "dictionary": sum(1 for item in items if item["value_kind"] == "dictionary"),
            "numeric": sum(1 for item in items if item["value_kind"] in {"integer", "decimal"}),
        },
    }


def _known_measurement(field: Any) -> bool:
    if not isinstance(field, dict):
        return field not in {None, "", "unknown"}
    value = field.get("canonical_value", field.get("target_value", field.get("value")))
    if isinstance(value, dict):
        if all(key in value for key in ("length_mm", "width_mm", "height_mm")):
            try:
                return all(float(value[key]) > 0 for key in ("length_mm", "width_mm", "height_mm"))
            except (TypeError, ValueError):
                return False
        return bool(value)
    if value in {None, "", "unknown"}:
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return bool(str(value).strip())


def _upload_config_measurement_source(raw: Dict[str, Any]) -> tuple[str, float, int]:
    status = str(raw.get("source_status") or "").casefold()
    if status == "confirmed_source":
        return "1688", 1.0, 2
    if status == "estimated_human_approved":
        return "human_override", 1.0, 4
    return "AI_estimated", 0.68, 7


def _upload_config_dimension_field(config: Dict[str, Any], key: str) -> Dict[str, Any] | None:
    raw = config.get(key) or {}
    values: Dict[str, float] = {}
    for axis in ("length_mm", "width_mm", "height_mm"):
        try:
            number = float(raw.get(axis))
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        values[axis] = int(number) if number.is_integer() else number
    source, confidence, precedence = _upload_config_measurement_source(raw)
    return {
        "canonical_value": values,
        "canonical_unit": "mm",
        "source": source,
        "precedence": precedence,
        "mapping_method": "upload_config_measurement_fallback",
        "confidence": confidence,
        "source_ref": f"output/ozon-upload-config.json.{key}",
        "source_refs": [
            f"output/ozon-upload-config.json.{key}",
            str(raw.get("source") or "unknown"),
        ],
    }


def _upload_config_weight_field(config: Dict[str, Any], key: str) -> Dict[str, Any] | None:
    raw = config.get(key) or {}
    try:
        value = float(raw.get("value_g"))
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    source, confidence, precedence = _upload_config_measurement_source(raw)
    return {
        "canonical_value": int(value) if value.is_integer() else value,
        "canonical_unit": "g",
        "source": source,
        "precedence": precedence,
        "mapping_method": "upload_config_measurement_fallback",
        "confidence": confidence,
        "source_ref": f"output/ozon-upload-config.json.{key}",
        "source_refs": [
            f"output/ozon-upload-config.json.{key}",
            str(raw.get("source") or "unknown"),
        ],
    }


def apply_upload_config_measurement_fallback(product_dir: Path, sku_rows: List[Dict[str, Any]]) -> None:
    config = load_optional_json(product_dir / "output/ozon-upload-config.json")
    if not config or not sku_rows:
        return
    fallbacks = {
        "product_dimensions": _upload_config_dimension_field(config, "product_dimensions"),
        "product_weight": _upload_config_weight_field(config, "product_weight"),
        "package_dimensions": _upload_config_dimension_field(config, "package_dimensions"),
        "package_weight": _upload_config_weight_field(config, "package_weight"),
    }
    # A product-level pricing profile is useful for freight estimation, but it
    # is not evidence that every SKU has those measurements.  Projecting it
    # into every SKU can create a valid-looking yet false Ozon card.  Only an
    # explicit SKU measurement (captured or per-SKU estimate) is uploadable.
    if any(
        str((config.get(key) or {}).get("source") or "").startswith("pricing_rules.")
        for key in fallbacks
    ):
        return
    for row in sku_rows:
        for key, fallback in fallbacks.items():
            if fallback and not _known_measurement(row.get(key)):
                row[key] = copy.deepcopy(fallback)


def build_attribute_fill_input(product_dir: Path) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    output = product_dir / "output"
    status = load_optional_json(product_dir / "status.json")
    run_snapshot = load_optional_json(output / "sku-run-snapshot.json")
    merged = (
        run_snapshot.get("merged_product_facts")
        if (
            run_snapshot.get("batch_id")
            and run_snapshot.get("batch_id") == status.get("batch_id")
            and isinstance(run_snapshot.get("merged_product_facts"), dict)
            and run_snapshot.get("merged_product_facts", {}).get("sku_rows")
        )
        else None
    )
    snapshot_source = "output/sku-run-snapshot.json" if merged else "output/merged-product-facts.json"
    if not merged:
        merged = merge_product_facts(product_dir)
    fact_lock_path = output / "product-fact-lock.json"
    fact_lock = load_optional_json(fact_lock_path)
    if not fact_lock:
        fact_lock = build_product_fact_lock(product_dir, merged=merged, run_snapshot=run_snapshot if snapshot_source.endswith("sku-run-snapshot.json") else None)
    metadata_path = output / "ozon-category-attributes.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"missing live Ozon attributes: {metadata_path}")
    metadata = load_json(metadata_path)
    aspect_ids = aspect_ids_for(metadata)
    attributes = [normalize_attribute(item, aspect_ids) for item in metadata.get("attributes") or []]
    category_attribute_plan = build_category_attribute_plan(attributes)
    sku_rows = [
        dict(item)
        for item in (merged.get("sku_rows") or merged.get("facts", {}).get("sku_rows") or [])
        if isinstance(item, dict)
    ]
    apply_upload_config_measurement_fallback(product_dir, sku_rows)
    dependencies = {
        "source_json_sha256": merged["dependencies"]["source_json_sha256"],
        "selected_sku_hash": merged["dependencies"]["selected_sku_hash"],
        "category_hash": merged["dependencies"]["category_hash"],
        "merged_facts_hash": merged["dependency_hash"],
        "product_fact_lock_hash": fact_lock.get("lock_hash") or "",
        "sku_run_snapshot_hash": run_snapshot.get("dependency_hash") if snapshot_source.endswith("sku-run-snapshot.json") else "",
        "sku_fact_source": snapshot_source,
        "metadata_contract_hash": stable_metadata_contract_hash(metadata),
        "builder_version": BUILDER_VERSION,
    }
    value = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "collection_id": merged["collection_id"],
        "source_kind": merged["source_kind"],
        "generated_at": now(),
        "category_id": int(metadata["category_id"]),
        "type_id": int(metadata["type_id"]),
        "category": merged.get("category") or {},
        "selected_skus": merged["selected_skus"],
        "sku_rows": sku_rows,
        "product_fact_lock": {
            "path": "output/product-fact-lock.json",
            "lock_hash": fact_lock.get("lock_hash") or "",
            "fact_source": fact_lock.get("fact_source") or snapshot_source,
            "unknown_policy": fact_lock.get("unknown_policy") or "",
            "non_inventable_claims": fact_lock.get("non_inventable_claims") or [],
        },
        "merged_facts": merged["facts"],
        "measurements": {
            "product_dimensions": (merged["facts"].get("product_dimensions")),
            "product_weight": (merged["facts"].get("product_weight")),
            "sku_measurements": {
                str(item.get("sku_id")): {
                    "product_dimensions": item.get("product_dimensions"),
                    "product_weight": item.get("product_weight"),
                    "package_dimensions": item.get("package_dimensions"),
                    "package_weight": item.get("package_weight"),
                    "capacity": item.get("capacity"),
                    "quantity": item.get("quantity"),
                }
                for item in sku_rows
            },
        },
        "ozon_attributes": attributes,
        "category_attribute_plan": category_attribute_plan,
        "dependencies": dependencies,
        "input_hash": sha256_json(dependencies),
    }
    write_json_atomic(output / "attribute-fill-input.json", value)
    write_json_atomic(output / COMPACT_FILENAME, build_compact_attribute_fill_input(value))
    context = build_ecommerce_design_context(value)
    context["store_cluster"] = store_cluster_design_context(product_dir)
    write_json_atomic(output / DESIGN_CONTEXT_FILENAME, context)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    args = parser.parse_args()
    result = build_attribute_fill_input(Path(args.product_dir))
    compact_path = Path(args.product_dir) / "output" / COMPACT_FILENAME
    compact = load_optional_json(compact_path)
    print(json.dumps({
        "product_id": result["product_id"],
        "attribute_count": len(result["ozon_attributes"]),
        "compact_attribute_count": len(compact.get("ozon_attributes") or []),
        "compact_allowed_values_count": (compact.get("compact_input") or {}).get("compact_allowed_values_count"),
        "input_hash": result["input_hash"],
        "compact_path": str(compact_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
