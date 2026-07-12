#!/usr/bin/env python3
"""Build online field differences and a no-write Ozon merge-repair payload."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))

from ozon_uploader import build_upload_payload  # noqa: E402


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, value: Any) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def result_items(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = response.get("result") or []
    return result if isinstance(result, list) else result.get("items", [])


def first_value(attributes: Dict[int, Any], attribute_id: int) -> Any:
    values = attributes.get(attribute_id) or []
    return values[0].get("value") if values else "unknown"


def build_field_diff(product_dir: Path) -> Dict[str, Any]:
    output = product_dir / "output"
    snapshot = load_json(output / "merge-read-snapshot.json")
    grouping = load_json(output / "variant-grouping-result.json")
    info_by_offer = {
        str(item["offer_id"]): item for item in snapshot["product_info"].get("items", [])
    }
    attrs_by_offer = {
        str(item["offer_id"]): item for item in result_items(snapshot["product_attributes"])
    }
    offers = []
    all_attributes: Dict[str, Dict[int, Any]] = {}
    for offer_id in [item["offer_id"] for item in grouping["variants"]]:
        info = info_by_offer[offer_id]
        attr_item = attrs_by_offer[offer_id]
        attributes = {
            int(item["id"]): item.get("values") or []
            for item in attr_item.get("attributes", [])
        }
        all_attributes[offer_id] = attributes
        offers.append({
            "offer_id": offer_id,
            "product_id": info.get("id") or attr_item.get("id"),
            "description_category_id": info.get("description_category_id"),
            "type_id": info.get("type_id"),
            "brand": first_value(attributes, 85),
            "product_name_4180": first_value(attributes, 4180),
            "model_name_9048": first_value(attributes, 9048),
            "configuration_4384": first_value(attributes, 4384),
            "attributes": {str(key): value for key, value in sorted(attributes.items())},
            "model_id": (info.get("model_info") or {}).get("model_id"),
            "is_archived": bool(info.get("is_archived")),
            "archive_status": "manually_archived",
            "visibility": info.get("visibility_details") or {},
            "moderation_status": (info.get("statuses") or {}).get("moderate_status", "unknown"),
            "validation_status": (info.get("statuses") or {}).get("validation_status", "unknown"),
            "errors": info.get("errors") or [],
        })

    differences = []
    conflicts = []

    def add_difference(field: str, attribute_id: Optional[int], classification: str, origin: str, values: Dict[str, Any]) -> None:
        if len({json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values.values()}) <= 1:
            return
        differences.append({
            "field": field,
            "attribute_id": attribute_id,
            "classification": classification,
            "origin": origin,
            "values_by_offer": values,
        })
        if classification == "public_field_conflict":
            conflicts.append(field)

    add_difference("offer_id", None, "system_generated_difference", "seller_offer_identifier", {item["offer_id"]: item["offer_id"] for item in offers})
    add_difference("product_id", None, "system_generated_difference", "ozon_product_identifier", {item["offer_id"]: item["product_id"] for item in offers})
    add_difference("model_id", None, "system_generated_difference", "ozon_group_identifier", {item["offer_id"]: item["model_id"] for item in offers})
    add_difference("product_name", 4180, "public_field_conflict", "submitted_sku_specific_name", {item["offer_id"]: item["product_name_4180"] for item in offers})
    add_difference("model_name", 9048, "public_field_conflict", "ozon_auto_rewrite_after_failed_merge", {item["offer_id"]: item["model_name_9048"] for item in offers})
    add_difference("configuration", 4384, "allowed_variant_difference", "mapped_ozon_variant", {item["offer_id"]: item["configuration_4384"] for item in offers})
    add_difference("seller_article", 9024, "system_generated_difference", "offer_id_mirror", {offer: first_value(attrs, 9024) for offer, attrs in all_attributes.items()})

    ignored_ids = {4180, 4384, 9024, 9048}
    all_ids = sorted(set().union(*(set(attrs) for attrs in all_attributes.values())))
    for attribute_id in all_ids:
        if attribute_id in ignored_ids:
            continue
        add_difference(
            f"attribute_{attribute_id}",
            attribute_id,
            "public_field_conflict",
            "ozon_stored_attribute",
            {offer: attrs.get(attribute_id) for offer, attrs in all_attributes.items()},
        )

    value = {
        "schema_version": "1.0.0",
        "fetched_at": snapshot["fetched_at"],
        "product_group_id": grouping["product_group_id"],
        "archive_status": "manually_archived",
        "offers": offers,
        "differences": differences,
        "common_field_conflicts": conflicts,
        "source_snapshot": "output/merge-read-snapshot.json",
    }
    return value


def build_repair_payload(product_dir: Path) -> Dict[str, Any]:
    output = product_dir / "output"
    payload = build_upload_payload(product_dir, mode="dry-run")
    grouping = payload["product_group"]
    items = payload["api_request_template"]["body"]["items"]
    expected_offers = [item["offer_id"] for item in grouping["variants"]]
    expected_products = [
        int(item["existing_product_id"])
        for item in payload["product_exists_check"]["offers"]
    ]
    attr_maps = [
        {attribute["id"]: attribute["values"] for attribute in item["attributes"]}
        for item in items
    ]
    checks = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    add("update_only", payload["product_exists_check"]["action"] == "update" and all(item["exists"] for item in payload["product_exists_check"]["offers"]), "All three existing offers must use UPDATE.")
    add("offer_ids_preserved", [item["offer_id"] for item in items] == expected_offers, "The three original offer_id values must be preserved.")
    add("common_name_4180", {item["name"] for item in items} == {"Электрическая точилка для ножей с заточными дисками"}, "Every item name must be the same group-level product name.")
    add("common_model_9048", {json.dumps(attrs.get(9048), ensure_ascii=False, sort_keys=True) for attrs in attr_maps} == {json.dumps([{"value": "Электрическая точилка для ножей P000005"}], ensure_ascii=False, sort_keys=True)}, "Every model attribute 9048 must use the stable group value.")
    add("variant_4384", {attrs.get(4384, [{}])[0].get("value") for attrs in attr_maps} == {"Электрическая точилка, 2 заточных диска", "Электрическая точилка, 4 заточных диска", "Электрическая точилка, 6 заточных дисков"}, "Only Комплектация must express the 2/4/6-disc difference.")
    add("common_brand", len({json.dumps(attrs.get(85), ensure_ascii=False, sort_keys=True) for attrs in attr_maps}) == 1, "Brand must be identical.")
    add("common_category_type", len({(item["description_category_id"], item["type_id"]) for item in items}) == 1, "Category and type must be identical.")
    non_variant = [{key: value for key, value in attrs.items() if key != 4384} for attrs in attr_maps]
    add("common_non_variant_attributes", len({json.dumps(value, ensure_ascii=False, sort_keys=True) for value in non_variant}) == 1, "All non-variant attributes must be identical.")
    add("no_inventory_write", all("stock" not in item and "warehouse_id" not in item for item in items), "No stock or warehouse field may be submitted.")
    add("no_create_delete_archive", payload["api_request_template"]["endpoint"] == "/v3/product/import", "Only the existing-offer import/update endpoint is present.")
    add("no_production_blockers", not payload["production_blockers"], "All existing upload gates must pass.")
    errors = [item["detail"] for item in checks if not item["passed"]]
    value = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "upload_mode": "dry-run",
        "api_writes_performed": False,
        "action": "update",
        "archive_status": "manually_archived",
        "product_group_id": grouping["product_group_id"],
        "protected_offers": expected_offers,
        "protected_product_ids": expected_products,
        "validation": {"status": "PASS" if not errors else "FAIL", "checks": checks, "errors": errors},
        "request": payload["api_request_template"],
    }
    return value


def validate(value: Dict[str, Any], schema: Path) -> List[str]:
    return [error.message for error in Draft202012Validator(load_json(schema)).iter_errors(value)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build merge field diff and repair dry run")
    parser.add_argument("product_dir", type=Path)
    args = parser.parse_args()
    product_dir = args.product_dir.resolve()
    output = product_dir / "output"
    field_diff = build_field_diff(product_dir)
    repair = build_repair_payload(product_dir)
    for name, value, schema_name in (
        ("merge-field-diff.json", field_diff, "merge-field-diff.schema.json"),
        ("merge-repair-payload.json", repair, "merge-repair-payload.schema.json"),
    ):
        errors = validate(value, ROOT / "templates" / schema_name)
        if errors:
            raise ValueError(f"{name} failed schema validation: {'; '.join(errors)}")
        write_json_atomic(output / name, value)
    print(json.dumps({
        "field_conflicts": field_diff["common_field_conflicts"],
        "dry_run_status": repair["validation"]["status"],
        "api_writes_performed": False,
    }, ensure_ascii=False))
    return 0 if repair["validation"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
