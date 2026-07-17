#!/usr/bin/env python3
"""Build a truthful Russian Ozon content package without calling any API.

Codex supplies localized copy through the current session. This module validates
that copy, preserves source SKU data, builds platform attribute placeholders,
and keeps every stage-3.5 draft blocked from upload.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]

RULES_PATH = ROOT / "rules" / "marketplace_content_rules.json"
SCHEMAS = {
    "title-ru.json": ROOT / "templates" / "title-ru.schema.json",
    "description-ru.json": ROOT / "templates" / "description-ru.schema.json",
    "keywords-ru.json": ROOT / "templates" / "keywords-ru.schema.json",
    "attributes.json": ROOT / "templates" / "attributes.schema.json",
    "ozon-draft.json": ROOT / "templates" / "ozon-draft.schema.json",
    "copy-ru.json": ROOT / "templates" / "copy-ru.schema.json",
    "cost-analysis.json": ROOT / "templates" / "cost-analysis.schema.json",
    "pricing-result.json": ROOT / "templates" / "pricing-result.schema.json",
    "profit-analysis.json": ROOT / "templates" / "profit-analysis.schema.json",
}


ATTRIBUTE_NAMES_RU = {
    "product_type": "Тип товара",
    "brand": "Бренд",
    "material": "Материал",
    "dimensions": "Размеры",
    "weight": "Вес товара",
    "load_capacity": "Максимальная нагрузка",
    "certifications": "Сертификация",
    "functions": "Назначение",
    "package_quantity": "Количество в упаковке",
    "accessories": "Комплектация и аксессуары",
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


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def schema_errors(instance: Any, schema_path: Path) -> List[str]:
    validator = Draft202012Validator(load_json(schema_path))
    return [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path))
    ]


def is_unknown(value: Any) -> bool:
    if value in (None, "unknown", [], ["unknown"]):
        return True
    if isinstance(value, list):
        return all(item in (None, "unknown") for item in value)
    return False


def all_plan_items(plan: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for key in ("main_images", "detail_images", "disclaimer_images"):
        yield from plan.get(key, [])


def validate_content_input(content: Dict[str, Any], product_id: str, rules: Dict[str, Any]) -> None:
    if content.get("product_id") != product_id:
        raise ValueError("content input product_id does not match product directory")
    title = content["title_ru"].strip()
    core_keyword = content["core_keyword"].strip()
    title_rules = rules["title"]
    if not title_rules["minimum_length"] <= len(title) <= title_rules["maximum_length"]:
        raise ValueError("Russian title length is outside configured limits")
    keyword_index = title.casefold().find(core_keyword.casefold())
    if keyword_index < 0 or keyword_index > title_rules["core_keyword_max_start_index"]:
        raise ValueError("core keyword must appear near the start of the Russian title")

    sections = content.get("description_sections", {})
    if set(sections) != set(rules["required_description_sections"]):
        raise ValueError("description sections must exactly match marketplace_content_rules.json")
    public_text = "\n".join([
        title,
        *sections.values(),
        *content["primary_keywords"],
        *content["secondary_keywords"],
    ]).casefold()
    forbidden = [*title_rules["forbidden_patterns"], *content.get("excluded_claims", [])]
    matched = [value for value in forbidden if value.casefold() in public_text]
    if matched:
        raise ValueError("forbidden or unsupported claims in marketplace copy: " + ", ".join(matched))

    primary = content["primary_keywords"]
    secondary = content["secondary_keywords"]
    if not rules["keywords"]["primary_min"] <= len(primary) <= rules["keywords"]["primary_max"]:
        raise ValueError("primary keyword count is outside configured limits")
    if len(secondary) > rules["keywords"]["secondary_max"]:
        raise ValueError("secondary keyword count is outside configured limits")
    normalized_keywords = [value.casefold() for value in [*primary, *secondary]]
    if len(normalized_keywords) != len(set(normalized_keywords)):
        raise ValueError("keywords must be unique across primary and secondary lists")
    basis = content["keyword_basis"]
    basis_keywords = {item["keyword"].casefold() for item in basis}
    if set(normalized_keywords) != basis_keywords:
        raise ValueError("every keyword must have exactly one traceable keyword_basis entry")
    if any(item["source"] not in rules["keywords"]["allowed_sources"] for item in basis):
        raise ValueError("keyword basis contains unsupported source type")

    image_copy = content.get("image_copy_ru")
    required_roles = {
        "main", "benefit", "problem_solution", "scene", "feature",
        "detail", "usage", "comparison", "disclaimer",
    }
    if image_copy is not None:
        if not isinstance(image_copy, dict) or not required_roles.issubset(image_copy):
            raise ValueError("image_copy_ru must contain short Russian copy for every image role")
        for role in required_roles:
            phrases = image_copy.get(role)
            if not isinstance(phrases, list) or not 1 <= len(phrases) <= 3:
                raise ValueError(f"image_copy_ru.{role} must contain 1 to 3 phrases")
            if any(not isinstance(value, str) or not 2 <= len(value.strip()) <= 70 or value.strip().casefold() == "unknown" for value in phrases):
                raise ValueError(f"image_copy_ru.{role} contains missing or overlong text")
        main_by_sku = image_copy.get("main_by_sku") or {}
        if not isinstance(main_by_sku, dict):
            raise ValueError("image_copy_ru.main_by_sku must be an object")
        for sku_id, phrases in main_by_sku.items():
            if not str(sku_id).strip() or not isinstance(phrases, list) or not 1 <= len(phrases) <= 3:
                raise ValueError("image_copy_ru.main_by_sku contains an invalid SKU entry")
            if any(not isinstance(value, str) or not 2 <= len(value.strip()) <= 70 or value.strip().casefold() == "unknown" for value in phrases):
                raise ValueError("image_copy_ru.main_by_sku contains missing or overlong text")


def source_refs(product_id: str) -> List[str]:
    return [
        f"products/{product_id}/input/source.json",
        f"products/{product_id}/output/product-analysis.json",
        f"products/{product_id}/output/product-positioning.json",
        f"products/{product_id}/output/style-profile.json",
    ]


def build_title(product_id: str, content: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "source_refs": source_refs(product_id)[:3],
        "title_ru": content["title_ru"],
        "core_keyword": content["core_keyword"],
        "evidence": content["title_evidence"],
        "excluded_claims": content.get("excluded_claims", []),
        "warnings": content.get("warnings", []),
    }
    return result


def build_description(product_id: str, content: Dict[str, Any], unknown_fields: List[str]) -> Dict[str, Any]:
    sections = content["description_sections"]
    description = "\n\n".join(sections[key] for key in (
        "product_value", "usage_scenarios", "core_advantages", "usage_method", "notices"
    ))
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "source_refs": source_refs(product_id),
        "description_ru": description,
        "sections": sections,
        "section_evidence": content["section_evidence"],
        "unknown_fields": unknown_fields,
        "warnings": content.get("warnings", []),
    }


def build_keywords(product_id: str, content: Dict[str, Any]) -> Dict[str, Any]:
    keyword_basis = [{
        "keyword": item["keyword"],
        "source": item["source"],
        "evidence": list(item.get("evidence") or item.get("source_refs") or []),
    } for item in content["keyword_basis"]]
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "source_refs": source_refs(product_id)[:3],
        "primary_keywords": content["primary_keywords"],
        "secondary_keywords": content["secondary_keywords"],
        "keyword_basis": keyword_basis,
        "excluded_keywords": content.get("excluded_claims", []),
        "warnings": content.get("warnings", []),
    }


def unknown_fields_from_analysis(analysis: Dict[str, Any]) -> List[str]:
    fields = [item["field"] for item in analysis.get("missing_information", [])]
    aliases = {"materials": "material", "certifications": "certifications"}
    normalized = [aliases.get(field, field) for field in fields]
    return list(dict.fromkeys(normalized))


def attribute_value(
    field: str,
    facts: Dict[str, Any],
    content: Dict[str, Any],
) -> tuple[Any, str, str, List[str]]:
    if field == "product_type":
        return content["product_type_ru"], "analysis", "needs_ozon_mapping", ["product-analysis.product_type"]
    if field == "functions":
        return content["confirmed_functions_ru"], "analysis", "needs_ozon_mapping", ["product-analysis.facts.functions"]
    if field == "accessories" and content.get("confirmed_accessories_ru"):
        return content["confirmed_accessories_ru"], "source", "needs_ozon_mapping", ["source.skus[*].option_values"]

    fact_key = {
        "material": "materials",
        "certifications": "certifications",
    }.get(field, field)
    value = facts.get(fact_key, "unknown")
    if is_unknown(value):
        return "unknown", "unknown", "unknown", [f"product-analysis.facts.{fact_key}=unknown"]
    if isinstance(value, dict):
        if field == "dimensions" and value.get("selected_sku"):
            value = str(value["selected_sku"])
        else:
            return "unknown", "unknown", "unknown", [f"product-analysis.facts.{fact_key}=structured_unknown"]
    return value, "analysis", "needs_ozon_mapping", [f"product-analysis.facts.{fact_key}"]


def build_attributes(product_id: str, analysis: Dict[str, Any], content: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    facts = analysis["facts"]
    attributes = []
    unknown_fields = []
    for field in rules["attribute_fields"]:
        value, source_type, status, refs = attribute_value(field, facts, content)
        if status == "unknown":
            unknown_fields.append(field)
        attributes.append({
            "field_key": field,
            "name_ru": ATTRIBUTE_NAMES_RU[field],
            "ozon_attribute_id": "unknown",
            "complex_id": "unknown",
            "value": value,
            "source_type": source_type,
            "source_refs": refs,
            "status": status,
        })
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "source_refs": source_refs(product_id),
        "category_proposal": {
            "name_ru": content["category_proposal"]["name_ru"],
            "path_hint_ru": content["category_proposal"]["path_hint_ru"],
            "description_category_id": "unknown",
            "type_id": "unknown",
            "mapping_status": "needs_ozon_mapping",
        },
        "attributes": attributes,
        "unknown_fields": unknown_fields,
        "warnings": [
            "Ozon category and attribute IDs are not mapped in stage 3.5.",
            *content.get("warnings", []),
        ],
    }


def values_for_draft(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [{"value": item} for item in value]
    return [{"value": value}]


def draft_attributes(attributes: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{
        "field_key": item["field_key"],
        "attribute_id": item["ozon_attribute_id"],
        "complex_id": item["complex_id"],
        "values": values_for_draft(item["value"]),
        "source": item["source_type"],
        "status": item["status"],
    } for item in attributes["attributes"]]


def ozon_draft_variant_kind(value: Any) -> str:
    """Keep seller-only SKU labels internal when Ozon requires separate cards."""
    normalized = str(value or "not_applicable")
    allowed = {"color", "size_or_measurement", "configuration", "mixed_supported", "not_applicable"}
    return normalized if normalized in allowed else "not_applicable"


def build_images(product_dir: Path, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    qc_path = product_dir / "output" / "image-qc-report.json"
    checked_slots = set()
    qc_status = "not_checked"
    if qc_path.is_file():
        report = load_json(qc_path)
        checked_slots = {item["slot"] for item in report["images_checked"]}
        qc_status = {"pass": "pass", "revise": "review_required", "reject": "fail"}[report["decision"]]

    images = []
    for item in all_plan_items(plan):
        path = ROOT / item["output_path"]
        role = "main" if item["image_type"] == "main" else "disclaimer" if item["image_type"] == "disclaimer" else "detail"
        exists = path.is_file()
        images.append({
            "slot": item["slot"],
            "role": role,
            "path": item["output_path"],
            "source_image_ids": item["reference_image_ids"],
            # Keep missing planned slots in the draft so the workbench can
            # show exactly what is absent instead of silently shrinking the
            # package and allowing a partial upload.
            "qc_status": "missing" if not exists else qc_status if item["slot"] in checked_slots else "not_checked",
            "variant_scope": item.get("variant_scope", "shared"),
            "source_sku_id": item.get("source_sku_id", "all"),
            "variant_kind": ozon_draft_variant_kind(item.get("variant_kind")),
            "variant_value": item.get("variant_value", "shared"),
        })
    return images


def build_skus(source: Dict[str, Any], content: Dict[str, Any], pricing_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    translations = content["sku_names_ru"]
    source_ids = {str(item["sku_id"]) for item in source["skus"]}
    if set(translations) != source_ids:
        raise ValueError("sku_names_ru must contain every selected source sku_id exactly once")

    skus = []
    pricing_by_sku = {str(item["sku_id"]): item for item in pricing_result["sku_pricing"]}
    for sku in source["skus"]:
        sku_id = str(sku["sku_id"])
        pricing = pricing_by_sku[sku_id]
        skus.append({
            "source_sku_id": sku_id,
            "source_sku_name": sku["sku_name"],
            "display_name_ru": translations[sku_id],
            "option_values": sku["option_values"],
            "offer_id": f"{source['product_id']}-{sku_id}",
            "purchase_price_cny": sku["purchase_price"],
            "purchase_price_source": sku["price_source"],
            "sale_price_rub": str(pricing["selling_price_rub"]) if pricing["selling_price_rub"] is not None else None,
            "sale_price": str(pricing["selling_price_cny"]) if pricing["selling_price_cny"] is not None else None,
            "sale_currency_code": "CNY" if pricing["selling_price_cny"] is not None else "unknown",
            "stock": None,
            "source_image_url": sku["image_url"],
            "local_image_path": sku["local_image_path"],
            "sku_image_missing": sku["sku_image_missing"],
            "availability": sku["availability"],
            "attributes": [{
                "field_key": "sku_configuration",
                "attribute_id": "unknown",
                "complex_id": "unknown",
                "values": [{"value": translations[sku_id]}],
                "source": "source",
                "status": "needs_ozon_mapping",
            }],
            "source_data": sku["source_data"],
        })
    return skus


def preflight(
    product_dir: Path,
    source: Dict[str, Any],
    images: List[Dict[str, Any]],
    unknown_fields: List[str],
    pricing_result: Dict[str, Any],
    checked_at: str,
) -> Dict[str, Any]:
    errors = [
        "Ozon description_category_id and type_id are unknown.",
        "Ozon required attribute IDs are unknown.",
    ]
    if pricing_result["recommendation"] == "REJECT":
        errors.append("Pricing Engine rejected at least one SKU; upload is forbidden.")
    if any(item["selling_price_cny"] is None for item in pricing_result["sku_pricing"]):
        errors.append("At least one SKU has no calculated sale price.")
    qc_path = product_dir / "output" / "qc-report.json"
    qc_status = None
    if not qc_path.is_file():
        errors.append("Product qc-report.json is missing.")
    else:
        qc_status = load_json(qc_path).get("status", "unknown")
        if qc_status == "fail":
            errors.append("Product qc-report.json has failed status.")

    warnings = [f"Unknown product fields: {', '.join(unknown_fields) or 'none' }."]
    warnings.append("Inventory is intentionally not managed by this pipeline; stock and warehouse fields are excluded from upload requests.")
    if qc_status not in (None, "pass"):
        warnings.append(f"Product QC status is {qc_status}; automatic correction or a non-blocking warning is required.")
    if any(sku["sku_image_missing"] for sku in source["skus"]):
        warnings.append("At least one selected SKU has no source-specific image; no image was guessed or assigned.")
    if images and any(image["qc_status"] != "pass" for image in images):
        warnings.append("At least one generated image has not passed image QC.")
    if not images:
        warnings.append("No generated stage-3.5-ready image is available in the current image plan.")
    return {"status": "failed", "errors": errors, "warnings": warnings, "checked_at": checked_at}


def build_ozon_draft(
    product_dir: Path,
    source: Dict[str, Any],
    title: Dict[str, Any],
    description: Dict[str, Any],
    keywords: Dict[str, Any],
    attributes: Dict[str, Any],
    plan: Dict[str, Any],
    content: Dict[str, Any],
    pricing_result: Dict[str, Any],
    profit_analysis: Dict[str, Any],
    checked_at: str,
) -> Dict[str, Any]:
    product_id = source["product_id"]
    images = build_images(product_dir, plan)
    calculated_prices = [
        item["selling_price_cny"] for item in pricing_result["sku_pricing"]
        if item["selling_price_cny"] is not None
    ]
    profit_warnings = sorted({
        issue for item in profit_analysis["sku_analysis"] for issue in item["issues"]
    })
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "offer_id": f"{product_id}-draft",
        "description_category_id": "unknown",
        "type_id": "unknown",
        "category": {
            "category_id": "unknown",
            "category_name": attributes["category_proposal"]["name_ru"],
            "confidence": 0,
            "match_status": "pending_metadata_match",
            "metadata_source": "stage35_category_proposal",
        },
        "title": title["title_ru"],
        "description": description["description_ru"],
        "keywords": [*keywords["primary_keywords"], *keywords["secondary_keywords"]],
        "warning": [
            "Stage 3.5 draft only: upload_allowed=false.",
            "Do not upload before human approval and real Ozon category/attribute mapping.",
            *content.get("warnings", []),
        ],
        "attributes": draft_attributes(attributes),
        "attribute_warnings": [
            "Stage 3.6 Ozon category and attribute matching has not been applied.",
            "All Ozon category and attribute IDs remain unknown.",
        ],
        "price": {
            "price": str(min(calculated_prices)) if calculated_prices else None,
            "old_price": None,
            "currency_code": "CNY" if calculated_prices else "unknown",
            "vat": "unknown"
        },
        "currency": "CNY" if calculated_prices else "unknown",
        "pricing_source": f"products/{product_id}/output/pricing-result.json",
        "profit_warning": profit_warnings,
        "stock": {"quantity": None, "warehouse_id": "unknown"},
        "images": images,
        "skus": build_skus(source, content, pricing_result),
        "upload_allowed": False,
        "preflight": preflight(
            product_dir, source, images, attributes["unknown_fields"], pricing_result, checked_at
        ),
        "source_refs": [
            f"products/{product_id}/input/source.json",
            f"products/{product_id}/output/product-analysis.json",
            f"products/{product_id}/output/product-positioning.json",
            f"products/{product_id}/output/style-profile.json",
            f"products/{product_id}/output/title-ru.json",
            f"products/{product_id}/output/description-ru.json",
            f"products/{product_id}/output/keywords-ru.json",
            f"products/{product_id}/output/attributes.json",
            f"products/{product_id}/output/image-plan.json",
            f"products/{product_id}/output/qc-report.json",
            f"products/{product_id}/output/cost-analysis.json",
            f"products/{product_id}/output/pricing-result.json",
            f"products/{product_id}/output/profit-analysis.json",
        ],
    }


def build_copy_compatibility(
    product_id: str,
    title: Dict[str, Any],
    description: Dict[str, Any],
    keywords: Dict[str, Any],
    content: Dict[str, Any],
    checked_at: str,
) -> Dict[str, Any]:
    bullets = [
        {"text_ru": description["sections"][key], "evidence": next(
            item["source_refs"] for item in description["section_evidence"] if item["section"] == key
        )}
        for key in ("product_value", "usage_scenarios", "core_advantages")
    ]
    result = {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "source_refs": description["source_refs"],
        "title_ru": title["title_ru"],
        "short_title": content["short_title_ru"],
        "bullets_ru": bullets,
        "selling_points": bullets,
        "description_ru": description["description_ru"],
        "description": description["description_ru"],
        "keywords_ru": [*keywords["primary_keywords"], *keywords["secondary_keywords"]],
        "keywords": [*keywords["primary_keywords"], *keywords["secondary_keywords"]],
        "usage_scenarios": content["usage_scenarios_ru"],
        "warning": content.get("warnings", []),
        "excluded_unknown_fields": description["unknown_fields"],
        "warnings": content.get("warnings", []),
        "processing": {
            "step": "russian_copy",
            "status": "completed",
            "started_at": checked_at,
            "finished_at": checked_at,
            "error": None,
        },
    }
    if content.get("image_copy_ru"):
        result["image_copy_ru"] = content["image_copy_ru"]
    return result


def build_package(product_dir: Path, content: Dict[str, Any], checked_at: str | None = None) -> Dict[str, Dict[str, Any]]:
    product_dir = product_dir.resolve()
    rules = load_json(RULES_PATH)
    source = load_json(product_dir / "input" / "source.json")
    analysis = load_json(product_dir / "output" / "product-analysis.json")
    plan_path = product_dir / "output" / "image-plan.json"
    plan = load_json(plan_path) if plan_path.is_file() else {
        "main_images": [], "detail_images": [], "disclaimer_images": [],
    }
    product_id = product_dir.name
    validate_content_input(content, product_id, rules)
    timestamp = checked_at or datetime.now().astimezone().replace(microsecond=0).isoformat()

    unknown_fields = unknown_fields_from_analysis(analysis)
    title = build_title(product_id, content)
    description = build_description(product_id, content, unknown_fields)
    keywords = build_keywords(product_id, content)
    attributes = build_attributes(product_id, analysis, content, rules)
    pricing_package = {}
    for filename in ("cost-analysis.json", "pricing-result.json", "profit-analysis.json"):
        path = product_dir / "output" / filename
        if not path.is_file():
            raise ValueError(
                f"{filename} is missing; the measurements/pricing stage must finish before marketplace content"
            )
        pricing_package[filename] = load_json(path)
    draft = build_ozon_draft(
        product_dir,
        source,
        title,
        description,
        keywords,
        attributes,
        plan,
        content,
        pricing_package["pricing-result.json"],
        pricing_package["profit-analysis.json"],
        timestamp,
    )
    compatibility = build_copy_compatibility(
        product_id, title, description, keywords, content, timestamp
    )
    package = {
        "title-ru.json": title,
        "description-ru.json": description,
        "keywords-ru.json": keywords,
        "attributes.json": attributes,
        "ozon-draft.json": draft,
        "copy-ru.json": compatibility,
        **pricing_package,
    }
    for filename, value in package.items():
        errors = schema_errors(value, SCHEMAS[filename])
        if errors:
            raise ValueError(f"{filename} failed schema validation: " + "; ".join(errors))
    return package


def validate_package(product_dir: Path) -> List[str]:
    product_dir = product_dir.resolve()
    output_dir = product_dir / "output"
    errors = []
    values = {}
    for filename, schema_path in SCHEMAS.items():
        path = output_dir / filename
        if not path.is_file():
            errors.append(f"{display_path(path)}: missing file")
            continue
        value = load_json(path)
        values[filename] = value
        errors.extend(f"{display_path(path)}:{error}" for error in schema_errors(value, schema_path))
    if errors:
        return errors

    source = load_json(product_dir / "input" / "source.json")
    content_input_path = product_dir / "logs" / "marketplace-content-input.json"
    if content_input_path.is_file():
        content_input = load_json(content_input_path)
        try:
            validate_content_input(content_input, product_dir.name, load_json(RULES_PATH))
        except ValueError as error:
            errors.append(f"{display_path(content_input_path)}:{error}")
    else:
        content_input = None
    title = values["title-ru.json"]
    description = values["description-ru.json"]
    keywords = values["keywords-ru.json"]
    attributes = values["attributes.json"]
    draft = values["ozon-draft.json"]
    pricing_result = values["pricing-result.json"]
    status = load_json(product_dir / "status.json")
    historical_uploaded_draft = status.get("status") == "UPLOADED"
    if draft["title"] != title["title_ru"]:
        errors.append("ozon-draft.json:title does not match title-ru.json")
    if content_input and title["title_ru"] != content_input["title_ru"]:
        errors.append("title-ru.json:title_ru does not match traceable content input")
    if draft["description"] != description["description_ru"]:
        errors.append("ozon-draft.json:description does not match description-ru.json")
    if draft["keywords"] != [*keywords["primary_keywords"], *keywords["secondary_keywords"]]:
        errors.append("ozon-draft.json:keywords do not match keywords-ru.json")
    if not historical_uploaded_draft and draft.get("pricing_source") != f"products/{product_dir.name}/output/pricing-result.json":
        errors.append("ozon-draft.json:pricing_source does not reference pricing-result.json")
    if draft["upload_allowed"] is not False or draft["preflight"]["status"] != "failed":
        errors.append("ozon-draft.json:stage 3.5 must remain blocked from upload")
    category_source = draft.get("category", {}).get("metadata_source")
    if category_source == "ozon_seller_api":
        if not isinstance(draft["description_category_id"], int) or not isinstance(draft["type_id"], int):
            errors.append("ozon-draft.json:live Ozon metadata requires numeric category and type IDs")
    elif draft["description_category_id"] != "unknown" or draft["type_id"] != "unknown":
        errors.append("ozon-draft.json:unmapped Ozon category IDs must remain unknown")
    if any(item["ozon_attribute_id"] != "unknown" for item in attributes["attributes"]):
        errors.append("attributes.json:unmapped Ozon attribute IDs must remain unknown")

    source_skus = {str(item["sku_id"]): item for item in source["skus"]}
    draft_skus = {str(item["source_sku_id"]): item for item in draft["skus"]}
    pricing_skus = {str(item["sku_id"]): item for item in pricing_result["sku_pricing"]}
    if set(source_skus) != set(draft_skus):
        errors.append("ozon-draft.json:SKU IDs do not exactly match selected source SKUs")
    else:
        for sku_id, source_sku in source_skus.items():
            draft_sku = draft_skus[sku_id]
            for source_key, draft_key in (
                ("sku_name", "source_sku_name"),
                ("option_values", "option_values"),
                ("purchase_price", "purchase_price_cny"),
                ("price_source", "purchase_price_source"),
                ("image_url", "source_image_url"),
                ("local_image_path", "local_image_path"),
                ("sku_image_missing", "sku_image_missing"),
                ("availability", "availability"),
                ("source_data", "source_data"),
            ):
                if source_sku[source_key] != draft_sku[draft_key]:
                    errors.append(f"ozon-draft.json:SKU {sku_id} changed source field {source_key}")
            if not historical_uploaded_draft:
                pricing_sku = pricing_skus[sku_id]
                expected_rub = str(pricing_sku["selling_price_rub"]) if pricing_sku["selling_price_rub"] is not None else None
                expected_cny = str(pricing_sku["selling_price_cny"]) if pricing_sku["selling_price_cny"] is not None else None
                if draft_sku["sale_price_rub"] != expected_rub:
                    errors.append(f"ozon-draft.json:SKU {sku_id} RUB price does not match pricing-result.json")
                if draft_sku.get("sale_price") != expected_cny:
                    errors.append(f"ozon-draft.json:SKU {sku_id} CNY price does not match pricing-result.json")
            if draft_sku["stock"] is not None:
                errors.append(f"ozon-draft.json:SKU {sku_id} stock must remain null")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Build truthful Ozon marketplace content files.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--content-input", help="Codex-localized content input JSON")
    parser.add_argument("--write", action="store_true", help="Write generated files atomically")
    parser.add_argument("--verify", action="store_true", help="Validate existing stage-3.5 output files")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()

    if args.verify:
        errors = validate_package(product_dir)
        if errors:
            print("FAILED")
            for error in errors:
                print(f"- {error}")
            return 1
        print(f"PASS {product_dir}")
        print("upload_allowed=false")
        return 0

    if not args.content_input:
        parser.error("--content-input is required unless --verify is used")
    package = build_package(product_dir, load_json(Path(args.content_input)))
    if args.write:
        for filename, value in package.items():
            write_json_atomic(product_dir / "output" / filename, value)
        print(product_dir / "output")
    else:
        print(json.dumps(package, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
