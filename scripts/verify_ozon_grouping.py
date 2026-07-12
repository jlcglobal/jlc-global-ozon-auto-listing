#!/usr/bin/env python3
"""Poll Ozon product info until an expected offer group is resolved."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))

from ozon_adapter import OzonConfig  # noqa: E402
from ozon_uploader import OzonWriteClient  # noqa: E402


SCHEMA = ROOT / "templates" / "grouping-verification.schema.json"
SHOPS = ROOT / "ozon-adapter" / "shops.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, value: Any) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def response_items(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = response.get("items")
    if not isinstance(items, list):
        result = response.get("result") or []
        items = result if isinstance(result, list) else result.get("items", [])
    return [item for item in items if isinstance(item, dict)]


def build_verification(
    grouping: Dict[str, Any],
    result: Dict[str, Any],
    response: Dict[str, Any],
    attribute_response: Dict[str, Any],
    timed_out: bool,
) -> Dict[str, Any]:
    expected_products = {
        item["offer_id"]: item["product_id"] for item in result.get("items", [])
    }
    expected_variants = {item["offer_id"]: item for item in grouping["variants"]}
    live_by_offer = {str(item.get("offer_id")): item for item in response_items(response)}
    attributes_by_offer = {
        str(item.get("offer_id")): item for item in response_items(attribute_response)
    }
    offers = []
    errors: List[str] = []
    warnings: List[str] = []
    model_ids = []
    product_names = set()
    final_success = True
    for offer_id, expected in expected_variants.items():
        live = live_by_offer.get(offer_id, {})
        attribute_item = attributes_by_offer.get(offer_id, {})
        accepted_attributes = {
            int(attribute.get("id")): attribute.get("values") or []
            for attribute in attribute_item.get("attributes", [])
            if attribute.get("id") is not None
        }
        statuses = live.get("statuses") or {}
        if live.get("name"):
            product_names.add(str(live["name"]))
        model_id = (live.get("model_info") or {}).get("model_id")
        if model_id:
            model_ids.append(model_id)
        update_status = str(statuses.get("validation_status") or statuses.get("status") or "pending")
        moderation = str(statuses.get("moderate_status") or "pending")
        if update_status != "success" or moderation != "approved":
            final_success = False
        product_id = live.get("id") or live.get("product_id") or "unknown"
        expected_product_id = expected_products.get(offer_id, "unknown")
        if product_id != expected_product_id:
            errors.append(
                f"{offer_id}: product_id changed from {expected_product_id} to {product_id}"
            )
        for item_error in live.get("errors", []):
            code = str(item_error.get("code") or "unknown")
            level = str(item_error.get("level") or "unknown")
            message = f"{offer_id}: {code} ({level})"
            if level == "ERROR_LEVEL_WARNING":
                warnings.append(message)
            else:
                errors.append(message)
        variant_values = expected.get("variant_attribute_values") or []
        expected_variant_value = str(variant_values[0]["value"] if variant_values else "unknown")
        accepted_model_values = accepted_attributes.get(9048, [])
        accepted_variant_values = accepted_attributes.get(4384, [])
        model_value = str(
            accepted_model_values[0].get("value")
            if accepted_model_values else "unknown"
        )
        variant_value = str(
            accepted_variant_values[0].get("value")
            if accepted_variant_values else "unknown"
        )
        if model_value != grouping["model_name_for_merge"]:
            warnings.append(
                f"{offer_id}: Ozon stored model attribute 9048 as '{model_value}', expected '{grouping['model_name_for_merge']}'"
            )
        if variant_value != expected_variant_value:
            warnings.append(
                f"{offer_id}: Ozon stored variant attribute 4384 as '{variant_value}', expected '{expected_variant_value}'"
            )
        offers.append({
            "offer_id": offer_id,
            "ozon_product_id": product_id,
            "expected_ozon_product_id": expected_product_id,
            "model_id": model_id,
            "model_attribute_id": 9048,
            "model_value": model_value,
            "expected_model_value": grouping["model_name_for_merge"],
            "variant_attribute_id": 4384,
            "variant_value": variant_value,
            "expected_variant_value": expected_variant_value,
            "update_status": update_status,
            "moderation_status": moderation,
        })

    unique_models = {value for value in model_ids if value}
    if len(product_names) > 1:
        warnings.append(
            "Ozon stored different product-name attribute 4180 values across the three offers."
        )
    if errors:
        grouping_status = "failed"
    elif len(offers) == grouping["variant_count"] and final_success and len(unique_models) == 1:
        grouping_status = "grouped"
    elif timed_out and len(live_by_offer) == len(offers):
        grouping_status = "not_grouped"
        warnings.append("Ozon processing completed or timed out without one shared model_id.")
    else:
        grouping_status = "pending"
    grouped_card_id = next(iter(unique_models)) if grouping_status == "grouped" else None
    return {
        "schema_version": "1.0.0",
        "product_group_id": grouping["product_group_id"],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "archive_status": "manually_archived",
        "expected_group_count": 1,
        "offer_count": len(offers),
        "offers": offers,
        "grouping_status": grouping_status,
        "grouped_card_id": grouped_card_id,
        "grouped_card_url": None,
        "timed_out": timed_out,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "last_api_response": response,
        "last_attribute_response": attribute_response,
    }


def verify(product_dir: Path, client: OzonWriteClient, timeout_seconds: int) -> Dict[str, Any]:
    output = product_dir / "output"
    grouping = load_json(output / "variant-grouping-result.json")
    result = load_json(output / "ozon-result.json")
    offer_ids = [item["offer_id"] for item in grouping["variants"]]
    deadline = time.monotonic() + timeout_seconds
    last_response: Dict[str, Any] = {}
    last_attribute_response: Dict[str, Any] = {}
    verification: Dict[str, Any] = {}
    while True:
        last_response = client.get_products_info(offer_ids)
        last_attribute_response = client.get_product_attributes(offer_ids)
        timed_out = time.monotonic() >= deadline
        verification = build_verification(
            grouping, result, last_response, last_attribute_response, timed_out
        )
        write_json_atomic(output / "grouping-verification.json", verification)
        if verification["grouping_status"] in {"grouped", "failed", "not_grouped"}:
            break
        if timed_out:
            break
        time.sleep(5)
    errors = list(Draft202012Validator(load_json(SCHEMA)).iter_errors(verification))
    if errors:
        raise ValueError("grouping-verification.json failed schema validation: " + "; ".join(error.message for error in errors))
    return verification


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Ozon offer grouping")
    parser.add_argument("product_dir", type=Path)
    parser.add_argument("--shop", default="zhonglian1")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    config = OzonConfig.from_shop(args.shop, SHOPS)
    result = verify(args.product_dir.resolve(), OzonWriteClient(config), args.timeout)
    print(json.dumps({
        "grouping_status": result["grouping_status"],
        "grouped_card_id": result["grouped_card_id"],
        "offer_count": result["offer_count"],
        "errors": result["errors"],
        "warnings": result["warnings"],
    }, ensure_ascii=False))
    return 0 if result["grouping_status"] == "grouped" else 1


if __name__ == "__main__":
    raise SystemExit(main())
