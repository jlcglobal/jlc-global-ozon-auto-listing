#!/usr/bin/env python3
"""Create conservative, local-only Ozon variant-rule audit artifacts.

This command never calls Ozon. It only reads a previously cached official
category-attribute response and the imported legacy rule snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "variant-compatibility-checker"))

from ozon_adapter.variant_rules import aspect_rule_path, load_cached_aspect_rule  # noqa: E402
from variant_compatibility_checker.service import (  # noqa: E402
    build_grouping_result,
    build_variant_decision,
    canonical_source_url,
    source_product_id,
)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def legacy_variant_ids() -> set[int]:
    path = ROOT / "ozon-adapter" / "metadata" / "ozon-rules-2026-07-10" / "variants.json"
    if not path.is_file():
        return set()
    result: set[int] = set()
    for entry in load_json(path):
        category_id = entry.get("descriptionCategoryId", entry.get("categoryId"))
        if str(category_id) != "17027907" or str(entry.get("typeId")) != "94462":
            continue
        for attribute in entry.get("attributes", entry.get("variantAttributes", [])):
            raw_id = attribute.get("attributeId", attribute.get("id")) if isinstance(attribute, dict) else attribute
            try:
                result.add(int(raw_id))
            except (TypeError, ValueError):
                pass
    return result


def standardize_cache(rule: Dict[str, Any]) -> None:
    path = aspect_rule_path(rule["category_id"], rule["type_id"])
    cached = load_json(path)
    cached["normalized_variant_rule"] = rule
    write_json_atomic(path, cached)


def select_attributes(attributes: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    wanted = {4384, 9048, 10096, 10097}
    by_id = {item["attribute_id"]: item for item in attributes}
    legacy_ids = legacy_variant_ids()
    result = []
    for attribute_id in (4384, 9048, 10096, 10097):
        item = by_id.get(attribute_id)
        if item is None:
            result.append({
                "attribute_id": attribute_id,
                "attribute_name": "unknown",
                "is_aspect": None,
                "is_required": None,
                "dictionary_id": None,
                "allowed_for_variant_merge": False,
                "evidence_source": "official_metadata_missing_attribute",
                "reason": "Attribute is absent from the cached official category metadata.",
            })
            continue
        is_aspect = item["is_aspect"]
        reason = "Official is_aspect=true; field may distinguish variants in one Ozon card." if is_aspect is True else (
            "The imported legacy list contained this attribute but official is_aspect=false; it cannot be used for card merge."
            if attribute_id in legacy_ids else "Official is_aspect=false; it cannot be used for card merge."
        )
        result.append({
            "attribute_id": attribute_id,
            "attribute_name": item["attribute_name"],
            "is_aspect": is_aspect,
            "is_required": item["is_required"],
            "dictionary_id": item["dictionary_id"],
            "allowed_for_variant_merge": is_aspect is True,
            "evidence_source": "cached_official_ozon_category_attributes",
            "reason": reason,
        })
    return result


def audit_product(product_dir: Path) -> Dict[str, Any]:
    source = load_json(product_dir / "input" / "source.json")
    category = load_json(product_dir / "output" / "ozon-category.json")
    category_id, type_id = int(category["category_id"]), int(category["type_id"])
    rule = load_cached_aspect_rule(category_id, type_id)
    if rule is None:
        raise RuntimeError("No cached official is_aspect metadata exists for this category.")
    standardize_cache(rule)
    selected = select_attributes(rule["attributes"])
    allowed = [item for item in selected if item["allowed_for_variant_merge"]]
    incorrectly = [
        {"attribute_id": item["attribute_id"], "attribute_name": item["attribute_name"], "reason": item["reason"]}
        for item in selected
        if item["attribute_id"] == 4384 and item["is_aspect"] is not True
    ]
    audit = {
        "category_id": category_id,
        "type_id": type_id,
        "attributes": selected,
        "confirmed_variant_fields": allowed,
        "incorrectly_classified_fields": incorrectly,
        "rule_data_complete": not rule["variant_rule_data_incomplete"],
    }

    compatibility_rule = {
        "attributes": [
            {"attributeId": str(item["attribute_id"]), "nameRu": item["attribute_name"],
             "required": item["is_required"], "isAspect": item["is_aspect"] is True}
            for item in rule["attributes"]
        ],
        "rule_data_complete": not rule["variant_rule_data_incomplete"],
        "source": rule["source"],
    }
    decision = build_variant_decision(product_dir.name, source, category_id, type_id, compatibility_rule, rule["rules_version"])
    grouping = build_grouping_result(product_dir.name, source, decision)
    corrected = {
        "product_id": product_dir.name,
        "internal_product_group": len(source.get("skus", [])) > 1,
        "internal_group_count": 1,
        "source_sku_count": len(source.get("skus", [])),
        "platform": "ozon",
        "platform_can_merge": decision["platform_can_merge"],
        "reason": "Selected Ozon category does not support Комплектация as an aspect attribute; the selected SKU difference is configuration, not color.",
        "allowed_variant_fields": decision["allowed_variant_fields"],
        "upload_strategy": "single_card_variants" if decision["platform_can_merge"] else "separate_cards",
        "variant_rule_data_incomplete": decision["variant_rule_data_incomplete"],
        "forbidden_coercions": ["Do not map configuration to color.", "Do not invent a color name.", "Do not select a semantically incorrect category solely to force a merge."],
    }
    remap = {
        "product_id": product_dir.name,
        "status": "NO_SAFE_PROPOSAL",
        "minimum_confidence": 0.90,
        "candidates": [],
        "dry_run_only": True,
        "reason": "No alternative category is proposed from the currently cached official aspect metadata. A broad metadata search was intentionally not inferred from names or performed as repeated live calls.",
    }
    report = {
        "category_id": category_id,
        "type_id": type_id,
        "legacy_snapshot_variant_rule_data_incomplete": True,
        "official_metadata_cache": {
            "path": str(aspect_rule_path(category_id, type_id).relative_to(ROOT)),
            "source": rule["source"], "fetched_at": rule["fetched_at"], "rules_version": rule["rules_version"],
        },
        "removed_from_allowed_variant_fields": incorrectly,
        "allowed_variant_fields": decision["allowed_variant_fields"],
        "ozon_write_operations": 0,
        "summary": "Variant eligibility now derives only from official is_aspect=true fields. 4384 Комплектация is excluded; source SKUs remain one internal group but require separate Ozon cards in this category.",
    }
    output = product_dir / "output"
    write_json_atomic(output / "category-variant-rule-audit.json", audit)
    write_json_atomic(output / "corrected-variant-decision.json", corrected)
    write_json_atomic(output / "category-remap-proposal.json", remap)
    write_json_atomic(output / "local-rule-fix-report.json", report)
    write_json_atomic(output / "variant-decision.json", decision)
    write_json_atomic(output / "variant-grouping-result.json", grouping)
    return {"audit": audit, "decision": corrected, "grouping": grouping, "report": report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir", type=Path)
    args = parser.parse_args()
    result = audit_product(args.product_dir.resolve())
    print(json.dumps(result["decision"], ensure_ascii=False, indent=2))
