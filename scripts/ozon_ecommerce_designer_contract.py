#!/usr/bin/env python3
"""Validate and project the connected-Codex ecommerce design.

This module never generates commercial content and never calls Ozon.  Its only
jobs are enforcing the N+8 contract and materializing legacy files from the
already completed unified design artifact.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft202012Validator

try:
    from scripts.image_asset_boundaries import validate_product_reference
    from scripts.production_input_guard import (
        ProductionInputError,
        validate_current_product_trace_ref,
        validate_formal_product_input,
    )
except ModuleNotFoundError:
    from image_asset_boundaries import validate_product_reference
    from production_input_guard import ProductionInputError, validate_current_product_trace_ref, validate_formal_product_input

ROOT = Path(__file__).resolve().parents[1]
LAYOUT_TYPES = {
    "sku_main", "core_benefit", "structure_callout", "usage_scene",
    "sku_comparison", "purchase_notice",
}
DETERMINISTIC_LAYOUTS = {"structure_callout", "sku_comparison", "purchase_notice"}
CREATIVE_PLACEHOLDERS = {"", "unknown", "generic", "template", "default", "通用", "默认", "固定模板"}
DECISION_STEP_ORDER = [
    "product_evidence",
    "buyer_analysis",
    "selling_point_ranking",
    "image_sequence",
    "per_slot_art_direction",
    "prompt_completion",
    "pre_generation_validation",
]


def project_root_for(product_dir: Path) -> Path:
    """Resolve product-relative references without coupling tests to this checkout."""
    resolved = product_dir.resolve()
    if resolved.parent.name == "products":
        return resolved.parent.parent
    return ROOT


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def selected_skus(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(
        [item for item in source.get("skus") or [] if not item.get("excluded")],
        key=lambda item: int(item.get("selection_order") or 9999),
    )


def sku_image(sku: Dict[str, Any]) -> str:
    return str(sku.get("variant_local_image_path") or sku.get("local_image_path") or "").strip()


def creative_decision_errors(item: Dict[str, Any]) -> List[str]:
    """Reject incomplete art direction before a renderer can invent a template."""
    slot = str(item.get("slot") or "unknown")
    errors: List[str] = []
    art = item.get("art_direction") or {}
    for key in (
        "concept", "scene", "composition", "product_position", "background",
        "lighting", "typography", "iconography", "negative_space",
        "value_signal", "slot_differentiation",
    ):
        value = str(art.get(key) or "").strip()
        if value.casefold() in CREATIVE_PLACEHOLDERS:
            errors.append(f"{slot} art_direction.{key} is a generic placeholder")

    russian_text = [str(value).strip() for value in item.get("russian_text") or []]
    prompt = str(item.get("prompt") or "").strip()
    prompt_folded = prompt.casefold()
    forbidden_two_stage_markers = (
        "text-free", "without lettering", "generate no text", "do not add text",
        "no generated typography", "rendered later", "added after generation",
        "无字底图", "后置叠字",
    )
    if any(marker in prompt_folded for marker in forbidden_two_stage_markers):
        errors.append(f"{slot} prompt requests a forbidden text-free or post-overlay workflow")
    missing_prompt_copy = [text for text in russian_text if text and text not in prompt]
    if missing_prompt_copy:
        errors.append(f"{slot} prompt must include every exact Russian text item for single-pass generation")
    overlay_plan = item.get("overlay_plan") or []
    overlay_text = [str(value.get("text") or "").strip() for value in overlay_plan]
    if overlay_text != russian_text:
        errors.append(f"{slot} overlay_plan must map every russian_text item exactly and in order")
    priorities = [value.get("priority") for value in overlay_plan]
    if len(priorities) != len(set(priorities)):
        errors.append(f"{slot} overlay_plan priorities must be unique")
    for index, value in enumerate(overlay_plan, start=1):
        box = value.get("box") or []
        if len(box) == 4:
            x, y, width, height = box
            if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
                errors.append(f"{slot} overlay_plan[{index}] box must stay inside the 3:4 canvas")
    return errors


def validate_design(product_dir: Path, design: Dict[str, Any] | None = None) -> List[str]:
    design = design or load_json(product_dir / "output/ozon-ecommerce-design.json")
    source = load_json(product_dir / "input/source.json")
    project_root = project_root_for(product_dir)
    errors = [error.message for error in Draft202012Validator(
        load_json(ROOT / "templates/ozon-ecommerce-design.schema.json")
    ).iter_errors(design)]
    try:
        validate_formal_product_input(product_dir)
    except ProductionInputError as exc:
        errors.append(str(exc))
    skus = selected_skus(source)
    if not 1 <= len(skus) <= 10:
        errors.append("selected SKU count must be between 1 and 10")
    if design.get("product_id") != product_dir.name:
        errors.append("design product_id does not match product directory")
    decision_trace = design.get("decision_trace") or {}
    actual_steps = [str(item.get("name") or "") for item in decision_trace.get("steps") or []]
    if actual_steps != DECISION_STEP_ORDER:
        errors.append("ecommerce design decision steps were not completed in the required order")
    if decision_trace.get("compliance_status") != "PASS" or decision_trace.get("violations"):
        errors.append("ecommerce design decision trace is not compliant; retry ecommerce_design")
    for ref in design.get("source_refs") or []:
        try:
            validate_current_product_trace_ref(product_dir, ref)
        except ProductionInputError as exc:
            errors.append(str(exc))
    evidence_items = [*((design.get("listing") or {}).get("selling_points") or [])]
    keyword_groups = ((design.get("listing") or {}).get("keywords") or {})
    for key in ("primary", "long_tail", "scene", "excluded"):
        evidence_items.extend(keyword_groups.get(key) or [])
    for item in evidence_items:
        for ref in item.get("source_refs") or []:
            try:
                validate_current_product_trace_ref(product_dir, ref)
            except ProductionInputError as exc:
                errors.append(str(exc))
    tags = (design.get("listing") or {}).get("hashtags") or []
    if len({str(value).casefold() for value in tags}) != 30:
        errors.append("hashtags must contain exactly 30 unique values")
    paragraphs = [item for item in re.split(r"\n\s*\n", str((design.get("listing") or {}).get("description_ru") or "")) if item.strip()]
    if len(paragraphs) < 4:
        errors.append("Russian description must contain at least four paragraphs")

    sku_ids = [str(item.get("sku_id") or "") for item in skus]
    design_skus = [str(item.get("sku_id") or "") for item in design.get("sku_plan") or []]
    if design_skus != sku_ids:
        errors.append("sku_plan must match selected SKU order exactly")
    mains = design.get("main_images") or []
    if len(mains) != len(skus):
        errors.append(f"main image count must equal selected SKU count ({len(skus)})")
    if len({str(item.get("sku_id") or "") for item in mains}) != len(mains):
        errors.append("each selected SKU must have exactly one unique main image")
    if len({str(item.get("slot") or "") for item in mains}) != len(mains):
        errors.append("main image slots must be unique")
    for sku, item in zip(skus, mains):
        expected_id = str(sku.get("sku_id") or "")
        expected_image = sku_image(sku)
        if item.get("sku_id") != expected_id:
            errors.append(f"main image is not bound to SKU {expected_id}")
        if item.get("layout_type") != "sku_main":
            errors.append(f"main image {expected_id} must use sku_main layout")
        refs = list(item.get("source_references") or [])
        if refs != [expected_image]:
            errors.append(f"main image {expected_id} must reference only its own real SKU image")
        try:
            validate_product_reference(product_dir, expected_image)
        except ValueError as exc:
            errors.append(f"SKU {expected_id} real reference is invalid: {exc}")
        errors.extend(creative_decision_errors(item))

    current_collection_id = str(source.get("collection_id") or "")
    if design.get("collection_id") != current_collection_id:
        errors.append("design collection_id must match the current workbench collection")
    if design.get("source_kind") != "workbench_collection":
        errors.append("design source_kind must be workbench_collection")

    details = design.get("detail_images") or []
    if len(details) != 8:
        errors.append("shared detail image count must equal 8")
    if len({str(item.get("slot") or "") for item in details}) != len(details):
        errors.append("shared detail image slots must be unique")
    for item in details:
        if item.get("sku_id"):
            errors.append(f"shared detail {item.get('slot')} must not be SKU-scoped")
        refs = list(item.get("source_references") or [])
        for ref in refs:
            try:
                validate_product_reference(product_dir, ref)
            except ValueError as exc:
                errors.append(f"detail source reference is invalid: {exc}")
        if item.get("layout_type") in DETERMINISTIC_LAYOUTS and item.get("operation") != "compose_from_real_images":
            errors.append(f"{item.get('slot')} must use deterministic real-image composition")
        errors.extend(creative_decision_errors(item))
    detail_compositions = {
        str((item.get("art_direction") or {}).get("composition") or "").strip().casefold()
        for item in details
    }
    if len(detail_compositions) < 6:
        errors.append("eight shared details require at least six distinct product-specific compositions")
    used_layouts = {str(item.get("layout_type") or "") for item in [*mains, *details]}
    required_layouts = LAYOUT_TYPES if len(skus) > 1 else LAYOUT_TYPES - {"sku_comparison"}
    missing_layouts = sorted(required_layouts - used_layouts)
    if missing_layouts:
        errors.append("image set does not cover layout types: " + ", ".join(missing_layouts))
    comparison = [item for item in details if item.get("layout_type") == "sku_comparison"]
    if len(skus) > 1:
        if len(comparison) != 1:
            errors.append("multi-SKU products require exactly one shared SKU comparison image")
        elif comparison[0].get("source_references") != [sku_image(item) for item in skus]:
            errors.append("SKU comparison must use every selected SKU real image in order")
    return errors


def materialize(product_dir: Path, design: Dict[str, Any]) -> None:
    errors = validate_design(product_dir, design)
    if errors:
        raise ValueError("; ".join(errors))
    output = product_dir / "output"
    listing = design["listing"]
    timestamp = datetime.now().astimezone().replace(microsecond=0).isoformat()
    comparison_role = next(
        (item for item in design["detail_images"] if item["layout_type"] == "sku_comparison"),
        None,
    )
    copy_value = {
        "schema_version": "1.0.0", "product_id": product_dir.name,
        "collection_id": design["collection_id"], "source_kind": design["source_kind"],
        "title_ru": listing["seo_title_ru"], "short_title": listing["short_title_ru"],
        "description_ru": listing["description_ru"],
        "selling_points": listing["selling_points"],
        "bullets_ru": listing["selling_points"][:5],
        "keywords_ru": [item["text_ru"] for key in ("primary", "long_tail", "scene") for item in listing["keywords"][key]],
        "image_copy_ru": {
            "main_by_sku": {item["sku_id"]: item["russian_text"] for item in design["main_images"]},
            "main": design["main_images"][0]["russian_text"],
            "benefit": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "core_benefit"),
            "problem_solution": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "core_benefit"),
            "scene": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "usage_scene"),
            "feature": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "structure_callout"),
            "detail": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "structure_callout"),
            "usage": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "usage_scene"),
            # Single-SKU products have no comparison role by contract.  Keep the
            # compatibility key, but never fabricate a variant comparison.
            "comparison": comparison_role["russian_text"] if comparison_role else [],
            "disclaimer": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "purchase_notice"),
        },
        "excluded_unknown_fields": [str(item.get("attribute_name") or item.get("field_key") or "unknown") for item in design.get("attribute_plan") or [] if item.get("value") == "unknown"],
        "warnings": [],
        "source_refs": design["source_refs"],
        "processing": {"step": "russian_copy", "status": "completed", "started_at": timestamp, "finished_at": timestamp, "error": None},
    }
    write_json_atomic(output / "copy-ru.json", copy_value)
    write_json_atomic(output / "title-ru.json", {"product_id": product_dir.name, "title_ru": listing["seo_title_ru"], "short_title_ru": listing["short_title_ru"], "source_ref": f"products/{product_dir.name}/output/ozon-ecommerce-design.json"})
    write_json_atomic(output / "description-ru.json", {"product_id": product_dir.name, "description_ru": listing["description_ru"], "source_ref": f"products/{product_dir.name}/output/ozon-ecommerce-design.json"})
    write_json_atomic(output / "ozon-tags.json", {"product_id": product_dir.name, "tags": listing["hashtags"], "source_ref": f"products/{product_dir.name}/output/ozon-ecommerce-design.json"})
    keyword_groups = listing["keywords"]
    accepted = [item for key in ("primary", "long_tail", "scene") for item in keyword_groups[key]]
    keyword_research = {
        "schema_version": "1.0.0", "product_id": product_dir.name,
        "target_market": "Ozon Russia", "language": "ru", "generated_at": timestamp,
        "seed_terms": list(dict.fromkeys(item["text_ru"] for item in keyword_groups["primary"])),
        "approved_keywords": [{
            "keyword": item["text_ru"],
            "intent": item["intent"] if item["intent"] in {"transactional", "commercial", "informational"} else "commercial",
            "source": (
                "ozon_public_search" if any(str(ref).startswith("https://www.ozon.ru/") for ref in item["source_refs"])
                else "ozon_seller_metadata" if any("category" in str(ref).casefold() or "ozon" in str(ref).casefold() for ref in item["source_refs"])
                else "source_fact"
            ),
            "evidence": item["source_refs"], "volume": "unknown", "difficulty": "unknown",
        } for item in accepted],
        "excluded_keywords": [{"keyword": item["text_ru"], "reason": item["intent"]} for item in keyword_groups["excluded"]],
        "metrics_notice": "Search volume and difficulty were not available and were not fabricated.",
    }
    write_json_atomic(output / "keyword-research-ru.json", keyword_research)
    category_path = load_json(product_dir / "input/category-selection.json")
    sections = listing["description_sections"]
    primary = [item["text_ru"] for item in keyword_groups["primary"]][:8]
    secondary = [item["text_ru"] for key in ("long_tail", "scene") for item in keyword_groups[key]][:20]
    source_by_keyword = {item["text_ru"]: item["source_refs"] for item in accepted}
    source_kind = {keyword: ("usage_scene" if any(item["text_ru"] == keyword for item in keyword_groups["scene"]) else "product_type") for keyword in [*primary, *secondary]}
    image_copy = copy_value["image_copy_ru"]
    marketplace_input = {
        "product_id": product_dir.name,
        "title_ru": listing["seo_title_ru"], "short_title_ru": listing["short_title_ru"],
        "core_keyword": primary[0],
        "product_type_ru": str((design.get("product_understanding") or {}).get("product_type_ru") or primary[0]),
        "category_proposal": {
            "name_ru": str(category_path.get("category_name_ru") or category_path.get("category_path_ru", [primary[0]])[-1]),
            "path_hint_ru": " / ".join(category_path.get("category_path_ru") or [primary[0]]),
        },
        "title_evidence": design["source_refs"],
        "description_sections": sections,
        "section_evidence": [{"section": key, "source_refs": design["source_refs"]} for key in sections],
        "primary_keywords": primary, "secondary_keywords": secondary,
        "keyword_basis": [{"keyword": keyword, "source": source_kind[keyword], "source_refs": source_by_keyword[keyword]} for keyword in [*primary, *secondary]],
        "image_copy_ru": image_copy,
        "sku_names_ru": {item["sku_id"]: item["name_ru"] for item in design["sku_plan"]},
        "confirmed_functions_ru": [], "confirmed_accessories_ru": [],
        "excluded_claims": [item["text_ru"] for item in keyword_groups["excluded"]],
        "warnings": [],
    }
    write_json_atomic(output / "marketplace-content-input.json", marketplace_input)
    brief = {
        "schema_version": "1.0.0", "product_id": product_dir.name,
        "collection_id": design["collection_id"], "source_kind": design["source_kind"],
        "source_refs": design["source_refs"],
        "designer_ref": f"products/{product_dir.name}/output/ozon-ecommerce-design.json",
        "product_understanding": design["product_understanding"],
        "product_positioning": design["buyer_strategy"],
        "visual_style": design["visual_system"],
        "main_image_roles": design["main_images"], "image_roles": design["detail_images"],
        "preserve": sorted({value for item in [*design["main_images"], *design["detail_images"]] for value in item["must_preserve"]}),
        "forbidden": design["forbidden"],
        "generation_order": ["unified_ecommerce_design", "all_prompts", "sku_main_images", "eight_shared_details", "technical_qc", "manual_review"],
        "processing": {"step": "ecommerce_creative_brief", "status": "completed", "generated_at": timestamp, "error": None},
    }
    write_json_atomic(output / "ecommerce-creative-brief.json", brief)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    parser.add_argument("--materialize", action="store_true")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()
    design = load_json(product_dir / "output/ozon-ecommerce-design.json")
    errors = validate_design(product_dir, design)
    if errors:
        print(json.dumps({"status": "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    if args.materialize:
        materialize(product_dir, design)
    print(json.dumps({
        "status": "PASS", "product_id": product_dir.name,
        "selected_skus": len(design["main_images"]), "shared_details": len(design["detail_images"]),
        "total_images": len(design["main_images"]) + len(design["detail_images"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
