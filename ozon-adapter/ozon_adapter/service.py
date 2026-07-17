"""Fetch and map live Ozon marketplace metadata without write API operations."""

from __future__ import annotations

import copy
import difflib
import json
import os
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from jsonschema import Draft202012Validator

from .client import OzonApiError, OzonReadOnlyClient


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
SCHEMAS = {
    "ozon-category-tree.json": TEMPLATES / "ozon-category-tree.schema.json",
    "ozon-category-attributes.json": TEMPLATES / "ozon-category-attributes.schema.json",
    "ozon-category.json": TEMPLATES / "ozon-category.schema.json",
    "ozon-attributes.json": TEMPLATES / "ozon-attributes.schema.json",
    "ozon-preflight.json": TEMPLATES / "ozon-preflight.schema.json",
    "ozon-draft.json": TEMPLATES / "ozon-draft.schema.json",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def schema_errors(value: Any, schema_path: Path) -> List[str]:
    validator = Draft202012Validator(load_json(schema_path))
    errors = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cache_hours() -> float:
    value = float(os.environ.get("OZON_METADATA_CACHE_HOURS", "24"))
    return max(0.0, value)


def _cached_response(path: Path | None, loader: Any, max_age_hours: float) -> Dict[str, Any]:
    if path is not None and path.is_file():
        age_seconds = max(0.0, time.time() - path.stat().st_mtime)
        if age_seconds <= max_age_hours * 3600:
            cached = load_json(path)
            if isinstance(cached, dict):
                return cached
    response = loader()
    if path is not None:
        write_json_atomic(path, response)
    return response


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[^0-9a-zа-яё]+", " ", text)
    return " ".join(text.split())


def response_items(response: Dict[str, Any], endpoint: str) -> List[Dict[str, Any]]:
    result = response.get("result", [])
    if isinstance(result, dict):
        result = result.get("items", result.get("attributes", result.get("categories", [])))
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise OzonApiError(endpoint, "result must be an array of objects")
    return result


def _positive_int(value: Any) -> Optional[int]:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def flatten_category_tree(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    roots = response_items(response, OzonReadOnlyClient.CATEGORY_TREE_ENDPOINT)
    flattened: List[Dict[str, Any]] = []

    def visit(
        node: Dict[str, Any],
        parent_id: Optional[int],
        path: List[str],
        inherited_category_id: Optional[int] = None,
        inherited_category_name: Optional[str] = None,
        inherited_parent_id: Optional[int] = None,
    ) -> None:
        category_id = _positive_int(
            node.get("description_category_id", node.get("category_id", node.get("id")))
        )
        name = str(node.get("category_name", node.get("name", ""))).strip()
        type_id = _positive_int(node.get("type_id"))
        type_name = str(node.get("type_name", "")).strip()
        children = node.get("children") or []
        if not isinstance(children, list):
            raise OzonApiError(
                OzonReadOnlyClient.CATEGORY_TREE_ENDPOINT,
                "category children must be an array",
            )
        if type_id is not None and type_name and category_id is None:
            if inherited_category_id is None or not inherited_category_name:
                raise OzonApiError(
                    OzonReadOnlyClient.CATEGORY_TREE_ENDPOINT,
                    "type leaf has no parent description category",
                )
            flattened.append({
                "category_id": inherited_category_id,
                "category_name": inherited_category_name,
                "parent_id": inherited_parent_id,
                "type_id": type_id,
                "type_name": type_name,
                "disabled": bool(node.get("disabled", False)),
                "is_leaf": True,
                "path": [*path, type_name],
            })
            return

        current_path = [*path, name] if name else path
        if category_id is not None and name:
            flattened.append({
                "category_id": category_id,
                "category_name": name,
                "parent_id": parent_id,
                "type_id": type_id,
                "type_name": type_name or None,
                "disabled": bool(node.get("disabled", False)),
                "is_leaf": not children,
                "path": current_path,
            })
            next_parent = category_id
        else:
            next_parent = parent_id
        for child in children:
            if isinstance(child, dict):
                visit(
                    child,
                    next_parent,
                    current_path,
                    category_id if category_id is not None else inherited_category_id,
                    name if category_id is not None and name else inherited_category_name,
                    parent_id if category_id is not None else inherited_parent_id,
                )

    for root in roots:
        visit(root, None, [])
    if not flattened:
        raise OzonApiError(OzonReadOnlyClient.CATEGORY_TREE_ENDPOINT, "category tree is empty")
    return flattened


def category_similarity(candidate: str, actual: str) -> float:
    left = normalize_text(candidate)
    right = normalize_text(actual)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.9
    stopwords = {"для", "и", "в", "на", "с", "по"}

    def stem(token: str) -> str:
        for suffix in ("ого", "ему", "ами", "ями", "ов", "ев", "ей", "ы", "и", "а", "я"):
            if token.endswith(suffix) and len(token) > len(suffix) + 3:
                return token[:-len(suffix)]
        return token

    left_tokens = {stem(token) for token in left.split() if token not in stopwords}
    right_tokens = {stem(token) for token in right.split() if token not in stopwords}
    intersection = len(left_tokens & right_tokens)
    jaccard = intersection / len(left_tokens | right_tokens)
    overlap = intersection / min(len(left_tokens), len(right_tokens))
    return max(jaccard, overlap)


def rank_categories(offline_category: Dict[str, Any], categories: List[Dict[str, Any]]) -> List[Tuple[float, Dict[str, Any], str]]:
    names = [offline_category.get("category_name", "")]
    names.extend(item.get("category_name", "") for item in offline_category.get("alternatives", []))
    ranked = []
    for category in categories:
        if category["disabled"] or category["type_id"] is None:
            continue
        display_name = category.get("type_name") or category["category_name"]
        comparisons = []
        for index, name in enumerate(names):
            exact = normalize_text(name) == normalize_text(display_name)
            similarity = 1.20 if exact else min(0.99, category_similarity(name, display_name))
            comparisons.append((max(0.0, similarity - index * 0.05), name))
        score, matched_name = max(comparisons, default=(0.0, ""), key=lambda item: item[0])
        category_path = normalize_text(" ".join(category.get("path") or []))
        normalized_names = " ".join(normalize_text(name) for name in names)
        if (
            "повод" in normalized_names
            and "собак" in normalized_names
            and normalize_text(display_name) == "поводок"
            and "товары для животных" in category_path
        ):
            score, matched_name = 1.10, "Поводки для собак -> Поводок"
        if (
            "хран" in normalized_names
            and any(token in normalized_names for token in ("сум", "органайз", "кофр"))
            and normalize_text(display_name) == "кофр для хранения вещей"
            and "хранение вещей" in category_path
        ):
            score, matched_name = 1.10, "Сумка для хранения -> Кофр для хранения вещей"
        ranked.append((score, category, matched_name))
    ranked.sort(key=lambda item: (item[0], item[1]["is_leaf"]), reverse=True)
    return ranked


def select_live_category(
    product_id: str,
    offline_category: Dict[str, Any],
    categories: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    locked_category_id = _positive_int(offline_category.get("category_id"))
    locked_type_id = _positive_int(offline_category.get("type_id"))
    if locked_category_id is not None and locked_type_id is not None:
        selected = next(
            (
                item for item in categories
                if item.get("category_id") == locked_category_id
                and item.get("type_id") == locked_type_id
                and item.get("disabled") is not True
            ),
            None,
        )
        if selected is None:
            raise ValueError(
                f"{product_id}: user-selected category_id/type_id is no longer an active Ozon category leaf; "
                "automatic rematching is forbidden"
            )
        selected_name = selected.get("type_name") or selected["category_name"]
        category_output = copy.deepcopy(offline_category)
        category_output.update({
            "schema_version": "2.0.0",
            "category_id": locked_category_id,
            "category_name": selected_name,
            "type_id": locked_type_id,
            "parent_id": selected.get("parent_id"),
            "category_path": selected["path"],
            "confidence": 1.0,
            "match_status": "api_confirmed",
            "metadata_source": "ozon_seller_api",
            "alternatives": [],
            "rationale": "The exact category_id/type_id selected by the user during 1688 collection was found in the current Ozon tree; no rematch was performed.",
            "warnings": list(dict.fromkeys([
                *offline_category.get("warnings", []),
                "Runtime category rematching was disabled because the collector stored a final user choice.",
            ])),
        })
        category_output.setdefault("evidence", []).append({
            "field": "ozon_category_tree_exact_locked_pair",
            "matched_signals": [str(locked_category_id), str(locked_type_id), selected_name],
            "source_refs": [f"products/{product_id}/input/category-selection.json"],
        })
        return category_output, selected
    ranked = rank_categories(offline_category, categories)
    if not ranked or ranked[0][0] < 0.65:
        requested_names = [str(offline_category.get("category_name") or "unknown")]
        requested_names.extend(
            str(item.get("category_name") or "unknown")
            for item in offline_category.get("alternatives", [])
        )
        raise ValueError(
            f"{product_id}: Ozon did not expose a reliable category/type for this shop "
            f"matching {', '.join(requested_names)}; automatic upload is blocked"
        )
    score, selected, matched_name = ranked[0]
    selected_name = selected.get("type_name") or selected["category_name"]
    match_status = "api_confirmed" if score >= 1.10 else "api_match_needs_review"
    confidence = round(min(0.99, (float(offline_category.get("confidence", 0)) + score) / 2), 2)
    alternatives = []
    for alternative_score, category, _ in ranked[1:4]:
        alternatives.append({
            "category_id": category["category_id"],
            "category_name": category.get("type_name") or category["category_name"],
            "confidence": round(min(0.99, alternative_score), 2),
            "reason": "Ozon category-tree name similarity is lower than the selected category.",
        })
    category_output = copy.deepcopy(offline_category)
    category_output.update({
        "schema_version": "2.0.0",
        "category_id": selected["category_id"],
        "category_name": selected_name,
        "type_id": selected["type_id"],
        "parent_id": selected["parent_id"],
        "category_path": selected["path"],
        "confidence": confidence,
        "match_status": match_status,
        "metadata_source": "ozon_seller_api",
        "alternatives": alternatives,
        "rationale": (
            f"Live Ozon category tree matched '{matched_name}' to "
            f"'{selected_name}' with similarity {score:.2f}."
        ),
        "warnings": [
            "Category metadata was fetched with read-only Ozon Seller API endpoints.",
            *([] if score >= 1.10 else ["Category name was not an exact match; automatic upload remains blocked."]),
        ],
    })
    category_output.setdefault("evidence", []).append({
        "field": "ozon_category_tree",
        "matched_signals": [matched_name, selected_name],
        "source_refs": [f"products/{product_id}/output/ozon-category-tree.json"],
    })
    return category_output, selected


def normalize_attribute(
    item: Dict[str, Any],
    category_id: int,
    type_id: int,
    client: OzonReadOnlyClient,
    cache_root: Path | None = None,
    max_age_hours: float = 24,
) -> Dict[str, Any]:
    attribute_id = _positive_int(item.get("id", item.get("attribute_id")))
    name = str(item.get("name", item.get("attribute_name", ""))).strip()
    if attribute_id is None or not name:
        raise OzonApiError(
            OzonReadOnlyClient.CATEGORY_ATTRIBUTES_ENDPOINT,
            "attribute ID or name is missing",
        )
    dictionary_id = _positive_int(item.get("dictionary_id"))
    allowed_values = []
    values_truncated = False
    if dictionary_id is not None:
        values_path = (
            cache_root / f"category-{category_id}-type-{type_id}" / f"attribute-{attribute_id}-values.json"
            if cache_root is not None else None
        )
        page = _cached_response(
            values_path,
            lambda: client.get_attribute_values(category_id, type_id, attribute_id),
            max_age_hours,
        )
        values_truncated = page["truncated"]
        for value in page["values"]:
            value_id = _positive_int(value.get("id"))
            text = str(value.get("value", value.get("name", ""))).strip()
            if value_id is not None and text:
                allowed_values.append({"id": value_id, "value": text})
    return {
        "attribute_id": attribute_id,
        "attribute_name": name,
        "required": bool(item.get("is_required", item.get("required", False))),
        "type": str(item.get("type", "unknown")) or "unknown",
        "dictionary_id": dictionary_id,
        "complex_id": _positive_int(item.get("complex_id", item.get("attribute_complex_id"))),
        "is_collection": bool(item.get("is_collection", False)),
        "allowed_values": allowed_values,
        "values_truncated": values_truncated,
    }


def build_category_attributes(
    product_id: str,
    category: Dict[str, Any],
    response: Dict[str, Any],
    client: OzonReadOnlyClient,
    fetched_at: str,
    cache_root: Path | None = None,
    max_age_hours: float = 24,
) -> Dict[str, Any]:
    category_id = int(category["category_id"])
    type_id = int(category["type_id"])
    items = response_items(response, OzonReadOnlyClient.CATEGORY_ATTRIBUTES_ENDPOINT)
    attributes = [
        normalize_attribute(item, category_id, type_id, client, cache_root, max_age_hours)
        for item in items
    ]
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "fetched_at": fetched_at,
        "api_endpoint": OzonReadOnlyClient.CATEGORY_ATTRIBUTES_ENDPOINT,
        "category_id": category_id,
        "category_name": category["category_name"],
        "type_id": type_id,
        "attributes": attributes,
        "warnings": [
            f"Allowed values were truncated for attribute {item['attribute_id']}."
            for item in attributes
            if item["values_truncated"]
        ],
    }


def validate_near_synonym_compatibility(
    product_id: str,
    offline_category: Dict[str, Any],
    category: Dict[str, Any],
    category_attributes: Dict[str, Any],
) -> None:
    primary_name = normalize_text(offline_category.get("category_name"))
    selected_name = normalize_text(category.get("category_name"))
    if not primary_name or primary_name == selected_name:
        return

    profiles_path = ROOT / "rules" / "ozon_category_profiles.json"
    profiles = load_json(profiles_path)
    profile = next(
        (
            item
            for item in profiles.get("profiles", {}).values()
            if normalize_text(item.get("category_name")) == primary_name
        ),
        None,
    )
    compatible = next(
        (
            item
            for item in (profile or {}).get("compatible_live_types", [])
            if normalize_text(item.get("name_ru")) == selected_name
        ),
        None,
    )
    if compatible is None:
        raise ValueError(
            f"{product_id}: near-synonym Ozon category '{category.get('category_name')}' "
            "has no approved semantic compatibility rule; automatic upload is blocked"
        )

    attribute_names = {
        normalize_text(item.get("attribute_name"))
        for item in category_attributes.get("attributes", [])
    }
    required = {
        normalize_text(name) for name in compatible.get("required_attribute_names", [])
    }
    forbidden = {
        normalize_text(name) for name in compatible.get("forbidden_attribute_names", [])
    }
    missing = sorted(required - attribute_names)
    conflicts = sorted(forbidden & attribute_names)
    if missing or conflicts:
        details = []
        if missing:
            details.append("missing compatible attributes: " + ", ".join(missing))
        if conflicts:
            details.append("conflicting attributes: " + ", ".join(conflicts))
        raise ValueError(
            f"{product_id}: near-synonym Ozon category '{category.get('category_name')}' "
            f"failed attribute compatibility ({'; '.join(details)}); automatic upload is blocked"
        )

    category["match_status"] = "api_confirmed"
    category["confidence"] = max(0.9, float(category.get("confidence") or 0))
    category["rationale"] += (
        " Near-synonym acceptance passed the stored semantic and live-attribute "
        "compatibility policy."
    )
    category.setdefault("evidence", []).append({
        "field": "near_synonym_attribute_compatibility",
        "matched_signals": sorted(required),
        "source_refs": [f"products/{product_id}/output/ozon-category-attributes.json"],
    })
    category["warnings"] = list(dict.fromkeys([
        *category.get("warnings", []),
        "A near-synonym category was accepted only after live attribute compatibility passed.",
    ]))


def _is_unknown(value: Any) -> bool:
    return value in (None, "unknown", [], ["unknown"])


def _semantic_candidates(semantic_attributes: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    candidates: Dict[str, Dict[str, Any]] = {}
    if isinstance(semantic_attributes.get("attributes"), list):
        for item in semantic_attributes["attributes"]:
            candidates[normalize_text(item["name_ru"])] = {
                "field_key": item["field_key"],
                "value": item["value"],
                "source_refs": item.get("source_refs", ["unknown"]),
            }
        return candidates
    for required in semantic_attributes.get("required_attributes", []):
        key = required["field_key"]
        mapped = semantic_attributes.get("mapped_attributes", {}).get(key)
        missing = next(
            (item for item in semantic_attributes.get("missing_attributes", []) if item["field_key"] == key),
            None,
        )
        candidates[normalize_text(required["name_ru"])] = {
            "field_key": key,
            "value": mapped["value"] if mapped else "unknown",
            "source_refs": mapped["source_refs"] if mapped else (missing or {}).get("source_refs", ["unknown"]),
        }
    return candidates


def _allowed_value_status(value: Any, allowed_values: List[Dict[str, Any]]) -> str:
    if _is_unknown(value):
        return "unknown"
    if not allowed_values:
        return "valid"
    values = value if isinstance(value, list) else [value]
    if any(isinstance(item, (dict, list)) for item in values):
        return "invalid"
    allowed = {normalize_text(item["value"]) for item in allowed_values}
    return "valid" if all(normalize_text(item) in allowed for item in values) else "invalid"


_VALUE_SYNONYMS = {
    "不锈钢": "нержавеющая сталь",
    "stainless steel": "нержавеющая сталь",
    "inox": "нержавеющая сталь",
    "塑料": "пластик",
    "塑胶": "пластик",
    "plastic": "пластик",
    "玻璃": "стекло",
    "glass": "стекло",
    "铝": "алюминий",
    "алюминий": "алюминий",
}


def _value_variants(value: Any) -> List[str]:
    """Return clean multilingual variants without provenance annotations."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    # Source notes are useful in analysis but must never enter an Ozon value.
    text = re.split(r"(?:；|;|\n)\s*(?:来源|source|основан|confidence|置信度)", text, maxsplit=1, flags=re.I)[0]
    parts = re.split(r"\s*/\s*|\s*[,，|]\s*", text)
    variants = []
    for part in [text, *parts]:
        cleaned = normalize_text(part)
        if cleaned and cleaned not in variants:
            variants.append(cleaned)
        # Look up synonyms before normalize_text removes non-Latin scripts.
        # For example, Chinese "不锈钢" must resolve to Russian
        # "нержавеющая сталь" before dictionary matching.
        synonym = _VALUE_SYNONYMS.get(str(part).strip().casefold()) or _VALUE_SYNONYMS.get(cleaned)
        if synonym and normalize_text(synonym) not in variants:
            variants.append(normalize_text(synonym))
    return variants


def _canonical_allowed_value(value: Any, allowed_values: List[Dict[str, Any]]) -> Any:
    """Map Chinese/Russian/annotated values to one official dictionary value."""
    if _is_unknown(value) or not allowed_values:
        return value
    values = value if isinstance(value, list) else [value]
    result = []
    for raw in values:
        variants = _value_variants(raw)
        if not variants:
            # An untranslated/empty candidate must not crash the whole batch.
            # It remains unknown and is handled by the normal required/optional
            # attribute validation below.
            return "unknown"
        exact = next(
            (item for item in allowed_values
             if any(normalize_text(item["value"]) == variant for variant in variants)),
            None,
        )
        if exact is None:
            # Conservative fuzzy matching: only accept a strong token overlap or
            # a known synonym, never send the annotated/raw value to Ozon.
            best = None
            best_score = 0.0
            for item in allowed_values:
                target = normalize_text(item["value"])
                score = max(difflib.SequenceMatcher(None, variant, target).ratio() for variant in variants)
                if score > best_score:
                    best, best_score = item, score
            if best is not None and best_score >= 0.62:
                exact = best
        if exact is None:
            return "unknown"
        result.append(exact["value"])
    return result if isinstance(value, list) else result[0]


def build_live_attribute_mapping(
    product_id: str,
    category: Dict[str, Any],
    metadata: Dict[str, Any],
    semantic_attributes: Dict[str, Any],
) -> Dict[str, Any]:
    candidates = _semantic_candidates(semantic_attributes)
    mapped = []
    for attribute in metadata["attributes"]:
        candidate = candidates.get(normalize_text(attribute["attribute_name"]))
        exact_type_values = [
            item
            for item in attribute["allowed_values"]
            if normalize_text(item["value"]) == normalize_text(category["category_name"])
        ]
        is_single_type_value = (
            normalize_text(attribute["attribute_name"]) in {"тип", "тип товара"}
            and len(exact_type_values) == 1
        )
        if is_single_type_value:
            value = exact_type_values[0]["value"]
            source = (
                f"products/{product_id}/output/ozon-category.json, "
                "product-analysis.product_type"
            )
            confidence = 0.99
            field_key = "product_type"
        elif candidate is None:
            value = "unknown"
            source = "unknown"
            confidence = 0.0
            field_key = "unknown"
        else:
            value = candidate["value"]
            source = ", ".join(candidate["source_refs"])
            confidence = 0.95 if not _is_unknown(value) else 0.0
            field_key = candidate["field_key"]
        if attribute["allowed_values"] and not _is_unknown(value):
            value = _canonical_allowed_value(value, attribute["allowed_values"])
        status = _allowed_value_status(value, attribute["allowed_values"])
        mapped.append({
            "attribute_id": attribute["attribute_id"],
            "attribute_name": attribute["attribute_name"],
            "field_key": field_key,
            "value": value,
            "source": source,
            "confidence": confidence,
            "required": attribute["required"],
            "type": attribute["type"],
            "complex_id": attribute["complex_id"],
            "allowed_values": attribute["allowed_values"],
            "validation_status": status,
        })
    missing = [item for item in mapped if item["required"] and item["validation_status"] == "unknown"]
    # Optional dictionary fields that cannot be matched are omitted rather than
    # blocking the product. Required fields are expected to be resolved by the
    # same canonicalization step; only an actually invalid value remains a hard
    # validation error.
    for item in mapped:
        if not item["required"] and item["validation_status"] in {"invalid", "unknown"}:
            item["value"] = "unknown"
            item["validation_status"] = "unknown"
    invalid = [item for item in mapped if item["validation_status"] == "invalid"]
    return {
        "schema_version": "2.0.0",
        "product_id": product_id,
        "category_ref": f"products/{product_id}/output/ozon-category.json",
        "category_id": category["category_id"],
        "type_id": category["type_id"],
        "metadata_source": "ozon_seller_api",
        "attributes": mapped,
        "missing_required_attributes": [
            {"attribute_id": item["attribute_id"], "attribute_name": item["attribute_name"]}
            for item in missing
        ],
        "invalid_values": [
            {
                "attribute_id": item["attribute_id"],
                "attribute_name": item["attribute_name"],
                "value": item["value"],
                "reason": "Value is not present in the Ozon allowed-values dictionary.",
            }
            for item in invalid
        ],
        "summary": {
            "required_count": sum(1 for item in mapped if item["required"]),
            "mapped_count": sum(1 for item in mapped if item["validation_status"] == "valid"),
            "missing_count": len(missing),
            "invalid_count": len(invalid),
            "unknown_count": sum(1 for item in mapped if item["validation_status"] == "unknown"),
        },
        "warnings": [
            "Only exact Russian attribute-name matches were mapped; unmatched fields remain unknown.",
            "No material, dimensions, weight, certification, function, brand or accessory value was inferred.",
        ],
    }


def build_preflight(product_id: str, attributes: Dict[str, Any], checked_at: str) -> Dict[str, Any]:
    missing = attributes["missing_required_attributes"]
    invalid = attributes["invalid_values"]
    status = "failed" if missing or invalid else "blocked_read_only"
    warnings = [
        "Stage 4.1 is read-only; upload remains disabled even when marketplace metadata is complete.",
        "Human approval, RUB sale price, stock and warehouse checks remain outside this metadata preflight.",
    ]
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "checked_at": checked_at,
        "metadata_source": "ozon_seller_api",
        "status": status,
        "upload_allowed": False,
        "missing_required_attributes": missing,
        "invalid_values": invalid,
        "warnings": warnings,
    }


def _draft_source(source: str) -> str:
    if source == "unknown":
        return "unknown"
    if source.startswith("source."):
        return "source"
    return "analysis"


def update_draft(
    product_id: str,
    draft: Dict[str, Any],
    category: Dict[str, Any],
    attributes: Dict[str, Any],
    preflight: Dict[str, Any],
) -> Dict[str, Any]:
    result = copy.deepcopy(draft)
    result["description_category_id"] = category["category_id"]
    result["type_id"] = category["type_id"]
    result["category"] = {
        "category_id": category["category_id"],
        "category_name": category["category_name"],
        "confidence": category["confidence"],
        "match_status": category["match_status"],
        "metadata_source": category["metadata_source"],
    }
    result["attributes"] = [{
        "field_key": item["field_key"],
        "attribute_id": item["attribute_id"],
        "complex_id": item["complex_id"] if item["complex_id"] is not None else "unknown",
        "values": [{"value": item["value"]}],
        "source": _draft_source(item["source"]),
        "status": "confirmed" if item["validation_status"] == "valid" else "unknown",
    } for item in attributes["attributes"]]
    result["attribute_warnings"] = [
        *attributes["warnings"],
        *[f"Missing required Ozon attribute: {item['attribute_name']}" for item in attributes["missing_required_attributes"]],
        *[f"Invalid Ozon attribute value: {item['attribute_name']}" for item in attributes["invalid_values"]],
    ]
    existing_errors = [
        error
        for error in result["preflight"].get("errors", [])
        if "category_id is unknown" not in error
        and "attribute IDs are unknown" not in error
        and "description_category_id and type_id are unknown" not in error
        and "required attribute IDs are unknown" not in error
    ]
    existing_errors.extend(
        f"Missing required Ozon attribute: {item['attribute_name']}"
        for item in preflight["missing_required_attributes"]
    )
    existing_errors.extend(
        f"Invalid Ozon value for attribute: {item['attribute_name']}"
        for item in preflight["invalid_values"]
    )
    result["preflight"].update({
        "status": "failed",
        "errors": list(dict.fromkeys(existing_errors)),
        "warnings": list(dict.fromkeys([*result["preflight"].get("warnings", []), *preflight["warnings"]])),
        "checked_at": preflight["checked_at"],
        "metadata_source": "ozon_seller_api",
        "missing_required_attributes": preflight["missing_required_attributes"],
        "invalid_values": preflight["invalid_values"],
    })
    result["upload_allowed"] = False
    for ref in (
        f"products/{product_id}/output/ozon-category-tree.json",
        f"products/{product_id}/output/ozon-category-attributes.json",
        f"products/{product_id}/output/ozon-preflight.json",
    ):
        if ref not in result["source_refs"]:
            result["source_refs"].append(ref)
    return result


def build_live_metadata_package(
    product_dir: Path,
    client: OzonReadOnlyClient,
    fetched_at: Optional[str] = None,
    cache_aspect_rules: bool = False,
    metadata_cache_root: Path | None = None,
) -> Dict[str, Dict[str, Any]]:
    product_dir = product_dir.resolve()
    product_id = product_dir.name
    fetched_at = fetched_at or utc_timestamp()
    output = product_dir / "output"
    offline_category = load_json(output / "ozon-category.json")
    semantic_path = output / "attributes.json"
    if not semantic_path.is_file():
        semantic_path = output / "ozon-attributes.json"
    semantic_attributes = load_json(semantic_path)
    draft_path = output / "ozon-draft.json"
    draft = load_json(draft_path) if draft_path.is_file() else None

    category_seed = copy.deepcopy(offline_category)
    stable_category_name = semantic_attributes.get("category_proposal", {}).get("name_ru")
    if str(offline_category.get("category_name") or "").strip().casefold() not in {"", "unknown"}:
        stable_category_name = None
    if stable_category_name:
        category_seed["category_name"] = stable_category_name
        category_seed["alternatives"] = []
        category_seed["evidence"] = [
            item
            for item in category_seed.get("evidence", [])
            if item.get("field") != "ozon_category_tree"
        ]
        if category_seed.get("metadata_source") == "ozon_seller_api":
            category_seed["confidence"] = 0.82

    max_age_hours = _cache_hours()
    tree_response = _cached_response(
        metadata_cache_root / "category-tree.json" if metadata_cache_root else None,
        client.get_category_tree,
        max_age_hours,
    )
    categories = flatten_category_tree(tree_response)
    tree = {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "fetched_at": fetched_at,
        "api_endpoint": OzonReadOnlyClient.CATEGORY_TREE_ENDPOINT,
        "categories": categories,
    }
    category, selected = select_live_category(product_id, category_seed, categories)
    category_cache_dir = (
        metadata_cache_root / f"category-{selected['category_id']}-type-{selected['type_id']}"
        if metadata_cache_root else None
    )
    attribute_response = _cached_response(
        category_cache_dir / "attributes.json" if category_cache_dir else None,
        lambda: client.get_category_attributes(selected["category_id"], selected["type_id"]),
        max_age_hours,
    )
    if cache_aspect_rules:
        aspect_path = ROOT / "ozon-adapter" / "metadata" / "live-aspect-rules" / (
            f"category-{selected['category_id']}-type-{selected['type_id']}.json"
        )
        write_json_atomic(aspect_path, {
            "schema_version": "1.0.0",
            "category_id": selected["category_id"],
            "type_id": selected["type_id"],
            "source": "ozon_seller_api",
            "fetched_at": fetched_at,
            "raw_response": attribute_response,
        })
    category_attributes = build_category_attributes(
        product_id, category, attribute_response, client, fetched_at,
        metadata_cache_root, max_age_hours,
    )
    validate_near_synonym_compatibility(
        product_id, offline_category, category, category_attributes
    )
    attributes = build_live_attribute_mapping(
        product_id, category, category_attributes, semantic_attributes
    )
    preflight = build_preflight(product_id, attributes, fetched_at)
    package = {
        "ozon-category-tree.json": tree,
        "ozon-category-attributes.json": category_attributes,
        "ozon-category.json": category,
        "ozon-attributes.json": attributes,
        "ozon-preflight.json": preflight,
    }
    if draft is not None:
        package["ozon-draft.json"] = update_draft(
            product_id, draft, category, attributes, preflight
        )
    for filename, value in package.items():
        errors = schema_errors(value, SCHEMAS[filename])
        if errors:
            raise ValueError(f"{filename} failed schema validation: " + "; ".join(errors))
    return package


def fetch_and_write_product_metadata(
    product_dir: Path,
    client: OzonReadOnlyClient,
) -> Dict[str, Dict[str, Any]]:
    cache_root = (
        ROOT / "ozon-adapter" / "metadata" / "live-category-cache" / client.config.shop_name
    )
    package = build_live_metadata_package(
        product_dir,
        client,
        cache_aspect_rules=True,
        metadata_cache_root=cache_root,
    )
    output = product_dir.resolve() / "output"
    for filename, value in package.items():
        write_json_atomic(output / filename, value)
    return package


def remap_cached_product_metadata(product_dir: Path) -> Dict[str, Dict[str, Any]]:
    product_dir = product_dir.resolve()
    product_id = product_dir.name
    output = product_dir / "output"
    category = load_json(output / "ozon-category.json")
    category_attributes = load_json(output / "ozon-category-attributes.json")
    semantic_path = output / "attributes.json"
    if semantic_path.is_file():
        semantic_attributes = load_json(semantic_path)
    else:
        # Newer products keep the already-mapped package only. Reuse it as the
        # semantic input so a normalization-rule upgrade can repair cached
        # products without another network request.
        cached = load_json(output / "ozon-attributes.json")
        semantic_attributes = {
            "attributes": [
                {
                    "field_key": item.get("field_key", "unknown"),
                    "name_ru": item.get("attribute_name", "unknown"),
                    "value": item.get("value", "unknown"),
                    "source_refs": [item.get("source", "unknown")],
                }
                for item in cached.get("attributes", [])
            ]
        }
    draft_path = output / "ozon-draft.json"
    draft = load_json(draft_path) if draft_path.is_file() else None
    if category.get("metadata_source") != "ozon_seller_api":
        raise ValueError(f"{product_id}: cached category is not backed by Ozon Seller API")
    attributes = build_live_attribute_mapping(
        product_id, category, category_attributes, semantic_attributes
    )
    preflight = build_preflight(product_id, attributes, utc_timestamp())
    package = {
        "ozon-attributes.json": attributes,
        "ozon-preflight.json": preflight,
    }
    if draft is not None:
        package["ozon-draft.json"] = update_draft(product_id, draft, category, attributes, preflight)
    for filename, value in package.items():
        errors = schema_errors(value, SCHEMAS[filename])
        if errors:
            raise ValueError(f"{filename} failed schema validation: " + "; ".join(errors))
    for filename, value in package.items():
        write_json_atomic(output / filename, value)
    return package


def validate_live_metadata_package(product_dir: Path) -> List[str]:
    output = product_dir.resolve() / "output"
    errors = []
    # Phase A ends with category/attribute metadata and must be verifiable
    # before the phase-B marketplace draft exists.  Validate the draft only
    # when it is present; requiring it here made a valid category cache look
    # broken and blocked the batch before text generation.
    metadata_schemas = {
        filename: schema_path
        for filename, schema_path in SCHEMAS.items()
        if filename != "ozon-draft.json"
    }
    for filename, schema_path in metadata_schemas.items():
        path = output / filename
        if not path.is_file():
            errors.append(f"{path}: missing file")
            continue
        errors.extend(f"{filename}:{error}" for error in schema_errors(load_json(path), schema_path))
    if errors:
        return errors
    category = load_json(output / "ozon-category.json")
    attributes = load_json(output / "ozon-attributes.json")
    preflight = load_json(output / "ozon-preflight.json")
    draft_path = output / "ozon-draft.json"
    draft = load_json(draft_path) if draft_path.is_file() else None
    if category["metadata_source"] != "ozon_seller_api" or not isinstance(category["category_id"], int):
        errors.append("category is not backed by live Ozon metadata")
    if attributes["category_id"] != category["category_id"]:
        errors.append("attribute category does not match selected category")
    if draft is not None and draft["description_category_id"] != category["category_id"]:
        errors.append("draft category does not match selected category")
    if preflight["upload_allowed"] is not False or (draft is not None and draft["upload_allowed"] is not False):
        errors.append("stage 4.1 must never enable upload")
    return errors
