#!/usr/bin/env python3
"""Match offline Ozon category and attribute metadata without calling Ozon API."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
PROFILES_PATH = ROOT / "rules" / "ozon_category_profiles.json"
OZON_RULES_PATH = ROOT / "rules" / "ozon-rules.md"
CATEGORY_SCHEMA_PATH = ROOT / "templates" / "ozon-category.schema.json"
ATTRIBUTES_SCHEMA_PATH = ROOT / "templates" / "ozon-attributes.schema.json"
DRAFT_SCHEMA_PATH = ROOT / "templates" / "ozon-draft.schema.json"


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
    return [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(
            Draft202012Validator(load_json(schema_path)).iter_errors(value),
            key=lambda item: list(item.path),
        )
    ]


def flatten_text(values: Iterable[Any]) -> str:
    text = []
    for value in values:
        if isinstance(value, dict):
            text.extend(str(child) for child in value.values())
        elif isinstance(value, list):
            text.extend(str(child) for child in value)
        elif value not in (None, "unknown"):
            text.append(str(value))
    return " ".join(text).casefold()


def evidence_fields(analysis: Dict[str, Any], positioning: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    visual_texts = [item.get("text", "") for item in analysis.get("selling_points", [])]
    visual_texts.extend(item.get("text", "") for item in analysis.get("competitive_advantages", []))
    return {
        "product_type": {
            "text": flatten_text([analysis.get("product_type"), analysis.get("facts", {}).get("title_cn")]),
            "source_refs": ["product-analysis.product_type", "product-analysis.facts.title_cn"],
        },
        "category": {
            "text": flatten_text([analysis.get("category"), analysis.get("facts", {}).get("category_cn")]),
            "source_refs": ["product-analysis.category", "product-analysis.facts.category_cn"],
        },
        "usage": {
            "text": flatten_text([
                analysis.get("usage_scenarios", []),
                positioning.get("market_positioning"),
                positioning.get("target_customer"),
            ]),
            "source_refs": [
                "product-analysis.usage_scenarios",
                "product-positioning.market_positioning",
                "product-positioning.target_customer",
            ],
        },
        "purchase_motivation": {
            "text": flatten_text([
                positioning.get("purchase_motivation"),
                positioning.get("customer_pain_points", []),
                positioning.get("core_sales_angle"),
            ]),
            "source_refs": [
                "product-positioning.purchase_motivation",
                "product-positioning.customer_pain_points",
                "product-positioning.core_sales_angle",
            ],
        },
        "visual_evidence": {
            "text": flatten_text([visual_texts, positioning.get("recommended_visual_direction")]),
            "source_refs": [
                "product-analysis.selling_points",
                "product-analysis.competitive_advantages",
                "product-positioning.recommended_visual_direction",
            ],
        },
    }


def score_profile(
    profile: Dict[str, Any],
    fields: Dict[str, Dict[str, Any]],
    weights: Dict[str, float],
) -> Tuple[float, List[Dict[str, Any]]]:
    raw_score = 0.0
    evidence = []
    product_type_signals = profile["signals"]["product_type"]
    product_type_text = fields["product_type"]["text"]
    product_type_matches = [signal for signal in product_type_signals if signal.casefold() in product_type_text]
    for field, weight in weights.items():
        signals = profile["signals"][field]
        text = fields[field]["text"]
        matches = [signal for signal in signals if signal.casefold() in text]
        target_matches = min(2, len(signals))
        field_score = min(len(matches) / target_matches, 1.0) if target_matches else 0
        raw_score += weight * field_score
        evidence.append({
            "field": field,
            "matched_signals": matches,
            "source_refs": fields[field]["source_refs"],
        })
    # A broad department word such as "厨房用品" must never select a
    # category whose actual product type is unsupported by the source facts.
    if not product_type_matches:
        raw_score = 0.0
        for item in evidence:
            if item["field"] == "product_type":
                item["matched_signals"] = []
                break
    return raw_score, evidence


def build_category_match(
    product_id: str,
    analysis: Dict[str, Any],
    positioning: Dict[str, Any],
    profiles: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    fields = evidence_fields(analysis, positioning)
    scored = []
    for profile_key, profile in profiles["profiles"].items():
        raw_score, evidence = score_profile(profile, fields, profiles["field_weights"])
        scored.append((raw_score, profile_key, profile, evidence))
    scored.sort(key=lambda item: item[0], reverse=True)
    raw_score, profile_key, profile, evidence = scored[0]
    confidence = round(raw_score * profiles["confidence_cap_without_live_ozon_metadata"], 2)
    minimum = profiles["minimum_semantic_match"]
    product_type_evidence = next(
        (item for item in evidence if item.get("field") == "product_type"),
        {},
    )
    product_type_supported = bool(product_type_evidence.get("matched_signals"))

    if raw_score < minimum and not product_type_supported:
        category_name = "unknown"
        match_status = "unresolved"
        rationale = "现有商品事实、使用场景和购买动机不足以形成可靠的离线类目建议。"
    elif raw_score < minimum:
        # Keep a product-type-grounded candidate for the live Ozon tree to
        # resolve through exact or near-synonym type names. Broad category
        # words alone never reach this branch because of the hard gate above.
        category_name = profile["category_name"]
        match_status = "needs_review"
        rationale = (
            f"商品类型已匹配到 {category_name}，但离线证据不足；"
            "将继续在当前Ozon真实类目树中搜索近义类型并校验属性兼容性。"
        )
    else:
        category_name = profile["category_name"]
        match_status = "semantic_match_needs_ozon_id" if raw_score >= 0.7 else "needs_review"
        rationale = (
            f"根据商品类型、类目事实、使用场景、购买动机和图片证据，"
            f"离线语义最接近 {category_name}；未调用Ozon API，因此真实类目ID仍未知。"
        )

    alternatives = []
    for index, item in enumerate(profile["alternatives"]):
        alternatives.append({
            "category_id": item["category_id"],
            "category_name": item["category_name"],
            "confidence": round(max(0.05, confidence - 0.22 - index * 0.1), 2),
            "reason": "相关使用场景存在交集，但商品类型与购买目的匹配度低于首选类目。",
        })
    return ({
        "schema_version": "1.0.0",
        "product_id": product_id,
        "category_id": "unknown",
        "category_name": category_name,
        "confidence": confidence,
        "match_status": match_status,
        "metadata_source": "offline_semantic_profiles",
        "alternatives": alternatives,
        "rationale": rationale,
        "evidence": evidence,
        "warnings": [
            "Ozon Seller API was not called; category_id is intentionally unknown.",
            "Confidence measures semantic fit only and does not confirm a live Ozon category tree node.",
        ],
    }, profile_key)


def is_unknown(value: Any) -> bool:
    if value in (None, "unknown", [], ["unknown"]):
        return True
    if isinstance(value, list):
        return all(item in (None, "unknown") for item in value)
    return False


def resolve_value(
    value_source: str,
    source: Dict[str, Any],
    analysis: Dict[str, Any],
) -> Tuple[Any, List[str], str]:
    facts = analysis.get("facts", {})
    if value_source == "analysis.product_type":
        return analysis.get("product_type", "unknown"), ["product-analysis.product_type"], "mapped_semantic"
    if value_source == "analysis.usage_scenarios":
        return analysis.get("usage_scenarios", []), ["product-analysis.usage_scenarios"], "mapped_semantic"
    if value_source == "source.skus":
        values = [{
            "sku_id": sku["sku_id"],
            "sku_name": sku["sku_name"],
            "option_values": sku["option_values"],
        } for sku in source.get("skus", [])]
        return values, ["source.skus"], "sku_dependent"
    if value_source.startswith("facts."):
        field = value_source.split(".", 1)[1]
        return facts.get(field, "unknown"), [f"product-analysis.facts.{field}"], "mapped_semantic"
    return "unknown", ["no reliable source field"], "mapped_semantic"


def missing_reason(field_key: str, analysis: Dict[str, Any], value_source: str) -> str:
    aliases = {
        "material": "materials",
        "max_load": "load_capacity",
        "roll_dimensions": "dimensions",
    }
    target = aliases.get(field_key, field_key)
    for item in analysis.get("missing_information", []):
        if item.get("field") == target:
            return item.get("reason", "unknown")
    if value_source == "unknown":
        return "当前来源未提供可靠字段，禁止根据商品类型或图片推测。"
    return "当前来源值为unknown，必须由人工或后续可靠元数据补充。"


def build_attribute_match(
    product_id: str,
    category: Dict[str, Any],
    profile: Dict[str, Any],
    source: Dict[str, Any],
    analysis: Dict[str, Any],
) -> Dict[str, Any]:
    required = []
    mapped = {}
    missing = []
    unknown = []
    for item in profile["required_attributes"]:
        field_key = item["field_key"]
        required.append({
            "field_key": field_key,
            "name_ru": item["name_ru"],
            "ozon_attribute_id": "unknown",
            "requirement_status": "candidate_requires_ozon_confirmation",
        })
        value, refs, mapping_status = resolve_value(item["value_source"], source, analysis)
        if is_unknown(value):
            unknown.append(field_key)
            missing.append({
                "field_key": field_key,
                "name_ru": item["name_ru"],
                "value": "unknown",
                "reason": missing_reason(field_key, analysis, item["value_source"]),
                "source_refs": refs,
            })
        else:
            mapped[field_key] = {
                "name_ru": item["name_ru"],
                "ozon_attribute_id": "unknown",
                "value": value,
                "source_refs": refs,
                "mapping_status": mapping_status,
            }
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "category_ref": f"products/{product_id}/output/ozon-category.json",
        "category_id": category["category_id"],
        "required_attributes": required,
        "mapped_attributes": mapped,
        "missing_attributes": missing,
        "unknown_attributes": unknown,
        "summary": {
            "required_count": len(required),
            "mapped_count": len(mapped),
            "missing_count": len(missing),
            "unknown_count": len(unknown),
        },
        "warnings": [
            "Required attributes are offline candidates and must be confirmed against the live Ozon category metadata.",
            "Ozon attribute IDs are intentionally unknown because Ozon Seller API was not called.",
            "Unknown material, dimensions, weight, certifications and functions were not inferred.",
        ],
    }


def draft_values(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [{"value": item} for item in value]
    return [{"value": value}]


def update_draft(
    product_id: str,
    draft: Dict[str, Any],
    category: Dict[str, Any],
    attributes: Dict[str, Any],
) -> Dict[str, Any]:
    mapped = attributes["mapped_attributes"]
    missing = {item["field_key"]: item for item in attributes["missing_attributes"]}
    draft["description_category_id"] = category["category_id"]
    draft["type_id"] = category.get("type_id", "unknown")
    draft["category"] = {
        "category_id": category["category_id"],
        "category_name": category["category_name"],
        "confidence": category["confidence"],
        "match_status": category["match_status"],
        "metadata_source": category["metadata_source"],
    }
    draft_attributes = []
    for required in attributes["required_attributes"]:
        field_key = required["field_key"]
        if field_key in mapped:
            item = mapped[field_key]
            source_type = "source" if item["mapping_status"] == "sku_dependent" else "analysis"
            values = draft_values(item["value"])
            status = "needs_ozon_mapping"
        else:
            source_type = "unknown"
            values = [{"value": missing[field_key]["value"]}]
            status = "unknown"
        draft_attributes.append({
            "field_key": field_key,
            "attribute_id": "unknown",
            "complex_id": "unknown",
            "values": values,
            "source": source_type,
            "status": status,
        })
    draft["attributes"] = draft_attributes
    draft["attribute_warnings"] = [
        *attributes["warnings"],
        *[f"{item['field_key']}: {item['reason']}" for item in attributes["missing_attributes"]],
    ]
    draft["upload_allowed"] = False
    draft["preflight"]["status"] = "failed"
    for error in (
        "Ozon category_id is unknown because live metadata was not requested.",
        "Ozon attribute IDs are unknown because live metadata was not requested.",
    ):
        if error not in draft["preflight"]["errors"]:
            draft["preflight"]["errors"].append(error)
    for ref in (
        f"products/{product_id}/output/ozon-category.json",
        f"products/{product_id}/output/ozon-attributes.json",
    ):
        if ref not in draft["source_refs"]:
            draft["source_refs"].append(ref)
    return draft


def build_metadata_package(product_dir: Path) -> Dict[str, Dict[str, Any]]:
    product_dir = product_dir.resolve()
    product_id = product_dir.name
    source = load_json(product_dir / "input" / "source.json")
    analysis = load_json(product_dir / "output" / "product-analysis.json")
    # Category matching belongs to phase A, while product positioning is a
    # phase-B content artifact.  Use it as optional evidence when resuming an
    # older product, but never require it for a new product's category match.
    positioning_path = product_dir / "output" / "product-positioning.json"
    positioning = load_json(positioning_path) if positioning_path.is_file() else {}
    if not OZON_RULES_PATH.is_file():
        raise ValueError(f"Missing Ozon rules: {OZON_RULES_PATH}")
    profiles = load_json(PROFILES_PATH)
    category, profile_key = build_category_match(product_id, analysis, positioning, profiles)
    selection_path = product_dir / "input" / "category-selection.json"
    if selection_path.is_file():
        selection = load_json(selection_path)
        category.update({
            "schema_version": "2.0.0",
            "category_id": int(selection["category_id"]),
            "type_id": int(selection["type_id"]),
            "parent_id": None,
            "category_name": str(selection["category_name_ru"]),
            "category_path": list(selection["category_path"]),
            "confidence": 1.0,
            "match_status": "api_confirmed",
            "metadata_source": "ozon_seller_api",
            "alternatives": [],
            "rationale": "用户已在1688采集阶段从当前Ozon类目树中确认最终类目；运行任务禁止重新猜测或替换。",
            "warnings": [
                "Category was locked by the user during 1688 collection.",
                "Runtime category rematching is disabled for this product.",
            ],
        })
        category.setdefault("evidence", []).append({
            "field": "collector_user_final_choice",
            "matched_signals": [str(selection["category_id"]), str(selection["type_id"])],
            "source_refs": [f"products/{product_id}/input/category-selection.json"],
        })
    attributes = build_attribute_match(
        product_id, category, profiles["profiles"][profile_key], source, analysis
    )
    package = {
        "ozon-category.json": category,
        "ozon-attributes.json": attributes,
    }
    # New products do not have an Ozon draft until the phase-B marketplace
    # content step.  Older/resumed products may already have one, in which
    # case keep it synchronized with the refreshed category metadata.
    draft_path = product_dir / "output" / "ozon-draft.json"
    if draft_path.is_file():
        package["ozon-draft.json"] = update_draft(
            product_id, load_json(draft_path), category, attributes
        )
    schema_map = {
        "ozon-category.json": CATEGORY_SCHEMA_PATH,
        "ozon-attributes.json": ATTRIBUTES_SCHEMA_PATH,
        "ozon-draft.json": DRAFT_SCHEMA_PATH,
    }
    for filename, value in package.items():
        errors = schema_errors(value, schema_map[filename])
        if errors:
            raise ValueError(f"{filename} failed schema validation: " + "; ".join(errors))
    return package


def validate_metadata_package(product_dir: Path) -> List[str]:
    product_dir = product_dir.resolve()
    output = product_dir / "output"
    paths = {
        "category": output / "ozon-category.json",
        "attributes": output / "ozon-attributes.json",
        "draft": output / "ozon-draft.json",
    }
    for path in paths.values():
        if not path.is_file():
            return [f"{path}: missing file"]
    category = load_json(paths["category"])
    attributes = load_json(paths["attributes"])
    draft = load_json(paths["draft"])
    errors = []
    for value, schema_path in (
        (category, CATEGORY_SCHEMA_PATH),
        (attributes, ATTRIBUTES_SCHEMA_PATH),
        (draft, DRAFT_SCHEMA_PATH),
    ):
        errors.extend(schema_errors(value, schema_path))

    if any(value["product_id"] != product_dir.name for value in (category, attributes, draft)):
        errors.append("product_id mismatch in metadata package")
    if attributes.get("schema_version") == "2.0.0":
        if category.get("metadata_source") != "ozon_seller_api":
            errors.append("live attributes require an Ozon Seller API category")
        if not isinstance(category.get("category_id"), int):
            errors.append("live category must contain a numeric category_id")
        if draft["upload_allowed"] is not False or draft["preflight"]["status"] != "failed":
            errors.append("live metadata must not enable upload")
        return errors
    if category["category_id"] != "unknown":
        errors.append("offline category matcher must not invent a category_id")
    if category["confidence"] > load_json(PROFILES_PATH)["confidence_cap_without_live_ozon_metadata"]:
        errors.append("offline category confidence exceeds configured cap")
    summary = attributes["summary"]
    if summary["required_count"] != len(attributes["required_attributes"]):
        errors.append("required attribute count mismatch")
    if summary["mapped_count"] != len(attributes["mapped_attributes"]):
        errors.append("mapped attribute count mismatch")
    if summary["missing_count"] != len(attributes["missing_attributes"]):
        errors.append("missing attribute count mismatch")
    if summary["unknown_count"] != len(attributes["unknown_attributes"]):
        errors.append("unknown attribute count mismatch")
    if draft["category"]["category_name"] != category["category_name"]:
        errors.append("ozon-draft category does not match ozon-category.json")
    if draft["category"]["confidence"] != category["confidence"]:
        errors.append("ozon-draft category confidence mismatch")
    if draft["upload_allowed"] is not False or draft["preflight"]["status"] != "failed":
        errors.append("metadata matching must not enable upload")
    if draft["description_category_id"] != "unknown" or draft["type_id"] != "unknown":
        errors.append("offline metadata must keep Ozon IDs unknown")
    if any(item["attribute_id"] != "unknown" for item in draft["attributes"]):
        errors.append("offline metadata must keep attribute IDs unknown")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Match offline Ozon category and attributes.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--write", action="store_true", help="Write metadata outputs and update ozon-draft.json")
    parser.add_argument("--verify", action="store_true", help="Validate existing metadata outputs")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()

    if args.verify:
        errors = validate_metadata_package(product_dir)
        if errors:
            print("FAILED")
            for error in errors:
                print(f"- {error}")
            return 1
        print(f"PASS {product_dir}")
        print("upload_allowed=false")
        return 0

    package = build_metadata_package(product_dir)
    if args.write:
        for filename, value in package.items():
            write_json_atomic(product_dir / "output" / filename, value)
        print(product_dir / "output")
    else:
        print(json.dumps(package, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
