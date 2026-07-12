"""Build conservative Ozon card-merge rules from cached official metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[2]
ASPECT_RULE_ROOT = ROOT / "ozon-adapter" / "metadata" / "live-aspect-rules"


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _response_items(value: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = value.get("raw_response", value).get("result", [])
    return result if isinstance(result, list) else result.get("items", [])


def aspect_rule_path(category_id: int, type_id: int) -> Path:
    return ASPECT_RULE_ROOT / f"category-{category_id}-type-{type_id}.json"


def normalize_aspect_rule(value: Dict[str, Any]) -> Dict[str, Any]:
    attributes = []
    for item in _response_items(value):
        attribute_id = _positive_int(item.get("id", item.get("attribute_id")))
        name = str(item.get("name", item.get("attribute_name", ""))).strip()
        if attribute_id is None or not name:
            continue
        attributes.append({
            "attribute_id": attribute_id,
            "attribute_name": name,
            "is_aspect": item.get("is_aspect") if isinstance(item.get("is_aspect"), bool) else None,
            "is_required": bool(item.get("is_required", item.get("required", False))),
            "dictionary_id": _positive_int(item.get("dictionary_id")),
        })
    category_id = _positive_int(value.get("category_id"))
    type_id = _positive_int(value.get("type_id"))
    if category_id is None or type_id is None:
        raise ValueError("Cached aspect metadata is missing category_id or type_id")
    return {
        "category_id": category_id,
        "type_id": type_id,
        "source": value.get("source", "ozon_seller_api"),
        "fetched_at": value.get("fetched_at", "unknown"),
        "rules_version": "official-is_aspect-v1",
        "variant_rule_data_incomplete": any(item["is_aspect"] is None for item in attributes),
        "attributes": attributes,
        "allowed_variant_fields": [item for item in attributes if item["is_aspect"] is True],
    }


def load_cached_aspect_rule(category_id: int, type_id: int) -> Dict[str, Any] | None:
    path = aspect_rule_path(category_id, type_id)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return normalize_aspect_rule(json.load(handle))
