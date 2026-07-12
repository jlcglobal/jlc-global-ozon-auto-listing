#!/usr/bin/env python3
"""Persist category-scoped workbench corrections without external AI services."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


LEARNING_VERSION = "1.0.0"
DEFAULT_THRESHOLD = 2
NON_LEARNING_FIELDS = {
    "review_mode", "review_depth", "auto_advance", "selected_shop",
    "selected_store_ids", "auto_advance", "notes", "image_order",
}


def load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def category_identity(product_dir: Path) -> Dict[str, Any]:
    selection = load(product_dir / "input/category-selection.json")
    category_id = selection.get("category_id")
    type_id = selection.get("type_id")
    return {
        "key": f"{category_id}:{type_id}",
        "category_id": category_id,
        "type_id": type_id,
        "category_name_zh": selection.get("category_name_zh") or "unknown",
        "category_path_zh": selection.get("category_path_zh") or [],
    }


def _copy_base(product_dir: Path) -> Dict[str, Any]:
    copy = load(product_dir / "output/copy-ru.json")
    tags = load(product_dir / "output/ozon-tags.json")
    attrs = load(product_dir / "output/ozon-attributes.json")
    pricing = load(product_dir / "output/pricing-result.json")
    return {
        "title_ru": copy.get("title_ru"),
        "short_title": copy.get("short_title"),
        "description_ru": copy.get("description_ru") or copy.get("description"),
        "bullets_ru": copy.get("bullets_ru"),
        "tags": tags.get("tags") or copy.get("keywords_ru") or copy.get("keywords"),
        "attributes": {
            str(item.get("attribute_id")): item.get("value")
            for item in attrs.get("attributes") or []
            if item.get("attribute_id") is not None
        },
        "sku_overrides": {
            str(item.get("sku_id")): {
                "selling_price_cny": item.get("selling_price_cny"),
                "selling_price_rub": item.get("selling_price_rub"),
            }
            for item in pricing.get("sku_pricing") or []
            if item.get("sku_id") is not None
        },
    }


def _changes(product_dir: Path, payload: Mapping[str, Any]) -> Iterable[Dict[str, Any]]:
    base = _copy_base(product_dir)
    for field, after in payload.items():
        if field in NON_LEARNING_FIELDS or field == "image_prompts":
            continue
        before = base.get(field)
        if field == "attributes" and isinstance(after, Mapping):
            for attribute_id, value in after.items():
                previous = (before or {}).get(str(attribute_id))
                if previous != value:
                    yield {"rule_key": f"attribute:{attribute_id}", "field": "attributes", "item_key": str(attribute_id), "before": previous, "after": value}
            continue
        if field == "sku_overrides" and isinstance(after, Mapping):
            for sku_id, values in after.items():
                if not isinstance(values, Mapping):
                    continue
                previous = (before or {}).get(str(sku_id), {})
                for value_key, value in values.items():
                    if previous.get(value_key) != value:
                        yield {"rule_key": f"sku:{value_key}", "field": "sku_overrides", "item_key": str(sku_id), "before": previous.get(value_key), "after": value}
            continue
        if before != after:
            yield {"rule_key": field, "field": field, "item_key": None, "before": before, "after": after}


def record_workbench_edits(
    root: Path,
    product_dir: Path,
    payload: Mapping[str, Any],
    saved_at: str,
    threshold: int = DEFAULT_THRESHOLD,
) -> Dict[str, Any]:
    identity = category_identity(product_dir)
    path = root / "cache/workbench-learning.json"
    data = load(path, {"schema_version": LEARNING_VERSION, "categories": {}})
    category = data.setdefault("categories", {}).setdefault(identity["key"], {**identity, "rules": {}})
    changed_rules: List[str] = []
    for change in _changes(product_dir, payload):
        rule = category.setdefault("rules", {}).setdefault(change["rule_key"], {
            "field": change["field"], "item_key": change["item_key"],
            "product_ids": [], "examples": [], "occurrences": 0,
            "threshold": threshold, "active": False,
        })
        product_ids = list(rule.get("product_ids") or [])
        if product_dir.name not in product_ids:
            product_ids.append(product_dir.name)
        examples = [item for item in rule.get("examples") or [] if item.get("product_id") != product_dir.name]
        examples.append({
            "product_id": product_dir.name, "saved_at": saved_at,
            "before": change["before"], "after": change["after"],
        })
        rule.update({
            "product_ids": product_ids[-20:], "examples": examples[-5:],
            "occurrences": len(set(product_ids)), "threshold": threshold,
            "active": len(set(product_ids)) >= threshold,
            "updated_at": saved_at,
        })
        last_two = [item.get("after") for item in rule["examples"][-2:]]
        rule["suggested_value"] = last_two[-1] if len(last_two) == 2 and last_two[0] == last_two[1] else None
        changed_rules.append(change["rule_key"])
    data.update({"schema_version": LEARNING_VERSION, "updated_at": saved_at})
    if changed_rules:
        write(path, data)
    return {"recorded": changed_rules, "category_key": identity["key"]}


def record_image_feedback(
    root: Path,
    product_dir: Path,
    item: Mapping[str, Any],
    action: str,
    saved_at: str,
    prompt: str = "",
    threshold: int = DEFAULT_THRESHOLD,
) -> Dict[str, Any]:
    """Learn visual preferences from concrete keep/regenerate/replace/reorder/delete actions."""
    profile = load(product_dir / "output/style-profile.json")
    analysis = load(product_dir / "output/product-analysis.json")
    style_family = str(profile.get("style_family") or "unknown")
    image_type = str(item.get("image_type") or item.get("type") or "unknown")
    product_type = str(analysis.get("product_type") or "unknown")
    key = f"{style_family}:{image_type}"
    path = root / "cache/image-feedback.json"
    data = load(path, {"schema_version": LEARNING_VERSION, "groups": {}, "events": []})
    group = data.setdefault("groups", {}).setdefault(key, {
        "style_family": style_family,
        "image_type": image_type,
        "actions": {},
        "product_ids": [],
        "examples": [],
        "threshold": threshold,
        "active": False,
    })
    actions = group.setdefault("actions", {})
    actions[action] = int(actions.get(action) or 0) + 1
    product_ids = list(group.get("product_ids") or [])
    if product_dir.name not in product_ids:
        product_ids.append(product_dir.name)
    example = {
        "product_id": product_dir.name,
        "product_type": product_type,
        "slot": str(item.get("slot") or "unknown"),
        "action": action,
        "prompt": prompt or str(item.get("prompt") or ""),
        "visual_direction": str(item.get("visual_direction") or "unknown"),
        "saved_at": saved_at,
    }
    examples = [*(group.get("examples") or []), example][-20:]
    group.update({
        "product_ids": product_ids[-50:],
        "examples": examples,
        "threshold": threshold,
        "active": len(set(product_ids)) >= threshold,
        "updated_at": saved_at,
    })
    data.setdefault("events", []).append({"group_key": key, **example})
    data["events"] = data["events"][-500:]
    data["updated_at"] = saved_at
    write(path, data)
    return {"recorded": True, "group_key": key, "active": group["active"]}


def materialize_active_experience(root: Path, product_dir: Path, created_at: str) -> Dict[str, Any]:
    identity = category_identity(product_dir)
    data = load(root / "cache/workbench-learning.json", {"categories": {}})
    category = (data.get("categories") or {}).get(identity["key"], {})
    rules = [
        {"rule_key": key, **value}
        for key, value in (category.get("rules") or {}).items()
        if value.get("active") is True
    ]
    image_data = load(root / "cache/image-feedback.json", {"groups": {}})
    current_style = str(load(product_dir / "output/style-profile.json").get("style_family") or "unknown")
    active_image_preferences = [
        {"group_key": key, **value}
        for key, value in (image_data.get("groups") or {}).items()
        if value.get("active") is True and str(value.get("style_family") or "unknown") == current_style
    ]
    payload = {
        "schema_version": LEARNING_VERSION,
        "product_id": product_dir.name,
        **identity,
        "created_at": created_at,
        "activation_threshold": DEFAULT_THRESHOLD,
        "active_rules": rules,
        "active_image_preferences": active_image_preferences,
        "instruction": "These are category-scoped examples from human corrections. Apply only when compatible with current source facts and Ozon metadata; never override truth or required-field validation.",
    }
    target = product_dir / "input/learned-experience.json"
    if rules or active_image_preferences:
        write(target, payload)
    else:
        target.unlink(missing_ok=True)
    return payload
