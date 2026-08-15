#!/usr/bin/env python3
"""Build the compact offline rule cache used during 1688 collection."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
METADATA_DIR = ROOT / "ozon-adapter/metadata/ozon-rules-2026-07-10"
EXTENSION_CACHE_PATH = ROOT / "collector/edge-extension/category-rules-cache.json"


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def build_cache(metadata_dir: Path = METADATA_DIR) -> Dict[str, Any]:
    categories = load_json(metadata_dir / "categories.json", [])
    attribute_rows = load_json(metadata_dir / "attributes.json", [])
    variant_rows = load_json(metadata_dir / "variants.json", [])
    version = load_json(metadata_dir / "version.json", {})
    category_index = {
        (positive_int(item.get("categoryId")), positive_int(item.get("typeId"))): item
        for item in categories
    }
    variant_index = {
        (positive_int(item.get("categoryId")), positive_int(item.get("typeId"))): {
            positive_int(attribute.get("attributeId"))
            for attribute in item.get("attributes") or []
            if positive_int(attribute.get("attributeId"))
        }
        for item in variant_rows
    }
    rules_by_key: Dict[str, Any] = {}
    for row in attribute_rows:
        category_id = positive_int(row.get("categoryId"))
        type_id = positive_int(row.get("typeId"))
        category = category_index.get((category_id, type_id))
        if not category_id or not type_id or not category:
            continue
        aspect_ids = variant_index.get((category_id, type_id), set())
        attributes = []
        seen = set()
        for raw in row.get("attributes") or []:
            attribute_id = positive_int(raw.get("attributeId"))
            required = bool(raw.get("required", False))
            is_aspect = attribute_id in aspect_ids
            if not attribute_id or attribute_id in seen or (not required and not is_aspect):
                continue
            seen.add(attribute_id)
            attributes.append({
                "attribute_id": attribute_id,
                "attribute_name": str(raw.get("nameRu") or "unknown"),
                "required": required,
                "is_aspect": is_aspect,
                "type": "unknown",
                "dictionary_id": None,
                "is_collection": False,
                "allowed_values": [],
                "allowed_values_status": "not_available_in_bulk_archive",
            })
        if not attributes:
            continue
        category_path = category.get("categoryPath") or []
        if isinstance(category_path, str):
            category_path = [part.strip() for part in category_path.split("/") if part.strip()]
        snapshot_payload = {
            "category_id": category_id,
            "type_id": type_id,
            "category_path": [str(part) for part in category_path],
            "attributes": attributes,
        }
        digest = hashlib.sha256(
            json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        rules_by_key[f"{category_id}:{type_id}"] = {
            "schema_version": "1.0.0",
            **snapshot_payload,
            "category_name_ru": str(category.get("nameRu") or category.get("title") or "unknown"),
            "category_name_zh": "unknown",
            "category_path_zh": ["unknown"],
            "shop_id": "offline",
            "rules_source": "official_bulk_offline_cache",
            "rules_snapshot_hash": digest,
            "required_attribute_ids": [item["attribute_id"] for item in attributes if item["required"]],
            "aspect_attribute_ids": [item["attribute_id"] for item in attributes if item["is_aspect"]],
            "captured_at": str(version.get("updatedAt") or "unknown"),
            "cache_hit": True,
            "offline_fallback": True,
            "dictionary_values_complete": False,
            "warnings": ["官方批量规则不含完整字典值；缺失值保持unknown并在后续本地缓存可用时补全"],
            "ozon_read_api_calls": 0,
            "ozon_write_api_calls": 0,
            "inventory_api_calls": 0,
        }
    return {
        "schema_version": "1.0.0",
        "cache_version": str(version.get("version") or version.get("updatedAt") or "unknown"),
        "category_count": len(rules_by_key),
        "rules_by_key": rules_by_key,
    }


def atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)
    path.chmod(0o644)


def write_caches(cache: Dict[str, Any]) -> None:
    atomic_write_json(EXTENSION_CACHE_PATH, cache)


if __name__ == "__main__":
    result = build_cache()
    write_caches(result)
    print(f"cached_categories={result['category_count']}")
    print(EXTENSION_CACHE_PATH)
