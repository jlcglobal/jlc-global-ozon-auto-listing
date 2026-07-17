#!/usr/bin/env python3
"""Validate and materialize an isolated connected-Codex ecommerce test design.

This is the manual-test counterpart to ``ozon_ecommerce_designer_contract``.
It accepts only ``test-data/manual-input/P9xxxxx`` and writes only to the
matching ``test-data/manual-output/P9xxxxx`` directory.  It never generates
commercial content, reads generated images as evidence, or calls Ozon.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

try:
    from scripts.offline_acceptance import audit
except ModuleNotFoundError:
    from offline_acceptance import audit


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def paths(case_id: str) -> tuple[Path, Path]:
    if not case_id.startswith("P9") or len(case_id) != 7 or not case_id[1:].isdigit():
        raise ValueError("manual ecommerce contract requires a reserved P9xxxxx test identity")
    manual_input = ROOT / "test-data" / "manual-input" / case_id
    manual_output = ROOT / "test-data" / "manual-output" / case_id
    return manual_input, manual_output


def relaxed_schema(filename: str, source: dict[str, Any]) -> dict[str, Any]:
    schema = load(ROOT / "templates" / filename)
    schema["properties"]["collection_id"] = {"const": source["collection_id"]}
    schema["properties"]["source_kind"] = {"const": "manual_test"}
    return schema


def validate_design(case_id: str) -> tuple[dict[str, Any], list[str]]:
    manual_input, manual_output = paths(case_id)
    boundary = audit(case_id)
    errors = list(boundary["errors"])
    source = load(manual_input / "source.json")
    gold = load(manual_input / "gold-standard.json")
    guidance = load(manual_input / "operator-guidance.json")
    design_path = manual_output / "ozon-ecommerce-design.json"
    if not design_path.is_file():
        return {}, [*errors, "ozon-ecommerce-design.json is missing"]
    design = load(design_path)
    errors.extend(
        item.message
        for item in Draft202012Validator(
            relaxed_schema("ozon-ecommerce-design.schema.json", source)
        ).iter_errors(design)
    )
    if design.get("product_id") != case_id:
        errors.append("design product_id does not match the test identity")
    if design.get("collection_id") != source.get("collection_id"):
        errors.append("design collection_id does not match manual input")
    if design.get("source_kind") != "manual_test":
        errors.append("design source_kind must be manual_test")

    expected_skus = [item["sku_id"] for item in gold["skus"]]
    actual_skus = [item.get("sku_id") for item in design.get("main_images") or []]
    if actual_skus != expected_skus:
        errors.append("main images must follow the three gold SKU identities in order")
    if len(design.get("detail_images") or []) != 8:
        errors.append("manual ecommerce design must contain exactly eight shared details")

    input_prefix = f"test-data/manual-input/{case_id}/sku-images/"
    for role in [*(design.get("main_images") or []), *(design.get("detail_images") or [])]:
        prompt = str(role.get("prompt") or "")
        prompt_folded = prompt.casefold()
        forbidden_two_stage_markers = (
            "text-free", "without lettering", "generate no text", "do not add text",
            "no generated typography", "rendered later", "added deterministically",
            "无字底图", "后置叠字",
        )
        if any(marker in prompt_folded for marker in forbidden_two_stage_markers):
            errors.append(f"{role.get('slot')} prompt requests a forbidden two-stage image workflow")
        if any(str(text) not in prompt for text in role.get("russian_text") or []):
            errors.append(f"{role.get('slot')} prompt is missing exact Russian copy for single-pass generation")
        for reference in role.get("source_references") or []:
            if not str(reference).startswith(input_prefix):
                errors.append(f"image reference escapes the manual SKU input boundary: {reference}")
                continue
            if not (ROOT / reference).is_file():
                errors.append(f"manual image reference is missing: {reference}")

    for index, (expected, actual) in enumerate(
        zip(guidance["image_detail_roles"], design.get("detail_images") or []), start=1
    ):
        if expected["source_references"] != actual.get("source_references"):
            errors.append(f"detail {index} does not use its confirmed source references")
        actual_copy = {str(value).casefold() for value in actual.get("russian_text") or []}
        for line in expected["russian_text"]:
            if line.casefold() not in actual_copy:
                errors.append(f"detail {index} is missing confirmed Russian copy: {line}")

    for item in gold["skus"]:
        source_name = Path(item["source_image"]).name
        image_path = manual_input / "sku-images" / source_name
        if sha256(image_path) != item["sha256"]:
            errors.append(f"manual SKU hash changed: {source_name}")

    serialized = json.dumps(design, ensure_ascii=False).casefold()
    for unsupported in ("кухонной полке", "сухих продуктов"):
        if unsupported in serialized:
            errors.append(f"unconfirmed scenario remains in design: {unsupported}")
    return design, errors


def planned_image(role: dict[str, Any], index: int, case_id: str, main: bool) -> dict[str, Any]:
    image_type_by_layout = {
        "sku_main": "main",
        "core_benefit": "benefit",
        "structure_callout": "detail",
        "usage_scene": "scene",
        "sku_comparison": "comparison",
        "purchase_notice": "disclaimer",
    }
    image_type = image_type_by_layout[role["layout_type"]]
    source_sku_id = str(role.get("sku_id") or "all")
    filename = f"{source_sku_id}.png" if main else f"detail-{index:03d}.png"
    bucket = "variant-main" if main else "detail"
    output_path = f"test-data/manual-output/{case_id}/generated-images/quality-test-20260717/{bucket}/{filename}"
    return {
        "type": "main" if main else "detail",
        "slot": role["slot"],
        "image_type": image_type,
        "requested_image_type": image_type,
        "fallback_reason": "unknown",
        "layout_type": role["layout_type"],
        "overlay_modules": role["overlay_modules"],
        "design_rationale": role["design_rationale"],
        "art_direction": role["art_direction"],
        "overlay_plan": role["overlay_plan"],
        "purchase_reason": role["commercial_purpose"],
        "visual_goal": role["commercial_purpose"],
        "scene_description": role["prompt"],
        "style_direction": role["art_direction"]["typography"],
        "purpose": role["commercial_purpose"],
        "buyer_question": role["buyer_question"],
        "selling_goal": role["commercial_purpose"],
        "scene": role["prompt"],
        "russian_text": role["russian_text"],
        "visual_direction": "；".join([
            role["art_direction"]["composition"],
            role["art_direction"]["value_signal"],
            role["art_direction"]["slot_differentiation"],
        ]),
        "reference_product_images": role["source_references"],
        "reference_images": role["source_references"],
        "reference_image_ids": [Path(value).stem for value in role["source_references"]],
        "variant_scope": "sku" if main else "shared",
        "shared_across_variants": not main,
        "source_sku_id": source_sku_id,
        "variant_kind": "size_or_measurement" if main else "not_applicable",
        "variant_value": role["russian_text"][1] if main else "shared",
        "operation": role["operation"],
        "source_text_policy": "single_pass_scene_product_exact_russian",
        "prompt": role["prompt"],
        "prompt_brief": f"3:4 · {role['layout_type']} · {role['commercial_purpose']}",
        "output_path": output_path,
        "status": "planned",
        "failure_reason": "unknown",
    }


def build_plan(case_id: str, design: dict[str, Any], source: dict[str, Any], timestamp: str) -> dict[str, Any]:
    manual_output = ROOT / "test-data" / "manual-output" / case_id
    main_images = [planned_image(item, index, case_id, True) for index, item in enumerate(design["main_images"], 1)]
    detail_images = [planned_image(item, index, case_id, False) for index, item in enumerate(design["detail_images"], 1)]
    references = []
    for sku in source["skus"]:
        path = str(sku["variant_local_image_path"])
        references.append({
            "id": Path(path).stem,
            "path": path,
            "role": "sku",
            "usable": True,
            "notes": "Manual-test original SKU reference; generated outputs are never eligible inputs.",
        })
    plan = {
        "schema_version": "1.0.0",
        "product_id": case_id,
        "collection_id": source["collection_id"],
        "source_kind": "manual_test",
        "source_refs": design["source_refs"],
        "style_profile_ref": f"test-data/manual-output/{case_id}/style-profile.json",
        "creative_brief_ref": f"test-data/manual-output/{case_id}/ecommerce-creative-brief.json",
        "ecommerce_design_ref": f"test-data/manual-output/{case_id}/ozon-ecommerce-design.json",
        "style_family": "fresh_fridge_organization",
        "image_structure_rule_ref": "manual-test:N-sku-mains-plus-8-shared-details",
        "image_set_structure": ["one_main_per_sku", "exactly_eight_shared_details"],
        "variant_image_strategy": {
            "mode": "sku_specific_main_shared_details",
            "variant_kinds": ["size_or_measurement"],
            "variant_main_count": len(main_images),
            "shared_detail_count": 8,
            "shared_disclaimer_count": 0,
        },
        "creative_direction": design["visual_system"],
        "listing_context": {
            "ready": True,
            "offline_acceptance_fixture": True,
            "title_ru": design["listing"]["seo_title_ru"],
            "description_ru": design["listing"]["description_ru"],
            "tags": design["listing"]["hashtags"],
            "attributes": design["attribute_plan"],
            "sku_variants": design["sku_plan"],
            "upload_forbidden": True,
        },
        "buyer_objections": list(design["buyer_strategy"].get("purchase_objections") or []),
        "generator_contract": {
            "must_follow_style_profile": True,
            "style_profile_ref": f"test-data/manual-output/{case_id}/style-profile.json",
            "allowed_structure": ["one_main_per_sku", "exactly_eight_shared_details"],
            "aspect_ratio": "3:4",
            "deviation_requires_review": True,
            "advisory_skills_required": [],
            "advisory_scope": "none",
            "project_rules_take_precedence": True,
            "image_slot_concurrency": 1,
            "image_qc_same_execution": True,
            "product_pixel_lock_required": False,
            "composition_tool": "built_in_image_editor_single_pass",
            "source_preflight_ref": f"test-data/manual-output/{case_id}/image-source-preflight.json",
            "generation_strategy": "product_specific_visual_story",
            "deterministic_image_types": ["comparison", "detail", "disclaimer"],
            "ai_reference_edit_image_types": ["main", "benefit", "scene"],
            "raw_1688_image_direct_upload_forbidden": True,
            "final_chinese_text_forbidden": True,
            "plain_white_background_forbidden": True,
            "main_images_first": True,
            "target_total_seconds": 300,
            "quality_gate": "hard_failures_only",
            "typography_strategy": "model_native_exact_russian_single_pass",
            "prompt_first_required": True,
            "empty_placeholder_panels_forbidden": True,
            "exact_shared_detail_count": 8,
            "creative_brief_required": True,
            "ecommerce_design_required": True,
            "fixed_template_fallback_forbidden": True,
            "overlay_strategy": "single_pass_model_native_typography",
        },
        "reference_images": references,
        "buyer_analysis": {
            "who_buys": [design["buyer_strategy"]["target_buyer"]],
            "why_buy": list(design["buyer_strategy"].get("purchase_motivations") or []),
            "main_pain_point": design["buyer_strategy"]["purchase_objections"][0],
            "strongest_selling_point": design["listing"]["selling_points"][0]["text_ru"],
            "selling_points": [item["text_ru"] for item in design["listing"]["selling_points"]],
            "proof_strategy": list(design["buyer_strategy"].get("decision_sequence") or []),
        },
        "main_images": main_images,
        "detail_images": detail_images,
        "disclaimer_images": [],
        "must_preserve": sorted({value for item in [*design["main_images"], *design["detail_images"]] for value in item["must_preserve"]}),
        "must_not_change": ["SKU proportions", "transparent material", "lid", "front handle", "single-item quantity"],
        "forbidden_content": design["forbidden"],
        "risks": [
            {"area": "product_fidelity", "level": "critical", "message": "Reject any changed SKU proportions, lid, handle, transparency or accessory count."},
            {"area": "ecommerce_layout", "level": "critical", "message": "A scene photo with only two text lines is not a completed ecommerce main image."},
        ],
        "processing": {"step": "image_plan", "status": "completed", "started_at": timestamp, "finished_at": timestamp, "error": None},
    }
    schema = relaxed_schema("image-plan.schema.json", source)
    errors = [item.message for item in Draft202012Validator(schema).iter_errors(plan)]
    if errors:
        raise ValueError("invalid manual image plan: " + "; ".join(errors))
    for item in [*main_images, *detail_images]:
        if not item["prompt"] or len(item["prompt"]) < 120:
            raise ValueError(f"planned image prompt is incomplete: {item['slot']}")
        if not item["output_path"].startswith(f"test-data/manual-output/{case_id}/generated-images/"):
            raise ValueError(f"planned image output escapes manual-output: {item['slot']}")
    return plan


def materialize(case_id: str, design: dict[str, Any]) -> dict[str, Any]:
    manual_input, manual_output = paths(case_id)
    source = load(manual_input / "source.json")
    timestamp = datetime.now().astimezone().replace(microsecond=0).isoformat()
    listing = design["listing"]
    write_atomic(manual_output / "title-ru.json", {
        "product_id": case_id, "source_kind": "manual_test",
        "title_ru": listing["seo_title_ru"], "short_title_ru": listing["short_title_ru"],
        "source_ref": f"test-data/manual-output/{case_id}/ozon-ecommerce-design.json",
    })
    write_atomic(manual_output / "description-ru.json", {
        "product_id": case_id, "source_kind": "manual_test",
        "description_ru": listing["description_ru"],
        "source_ref": f"test-data/manual-output/{case_id}/ozon-ecommerce-design.json",
    })
    write_atomic(manual_output / "ozon-tags.json", {
        "product_id": case_id, "source_kind": "manual_test", "tags": listing["hashtags"],
        "source_ref": f"test-data/manual-output/{case_id}/ozon-ecommerce-design.json",
    })
    write_atomic(manual_output / "copy-ru.json", {
        "schema_version": "1.0.0", "product_id": case_id,
        "collection_id": source["collection_id"], "source_kind": "manual_test",
        "title_ru": listing["seo_title_ru"], "short_title": listing["short_title_ru"],
        "description_ru": listing["description_ru"],
        "selling_points": listing["selling_points"],
        "keywords_ru": [item["text_ru"] for key in ("primary", "long_tail", "scene") for item in listing["keywords"][key]],
        "hashtags": listing["hashtags"], "source_refs": design["source_refs"],
        "processing": {"step": "russian_copy", "status": "completed", "finished_at": timestamp, "error": None},
    })
    write_atomic(manual_output / "ozon-attributes-final.json", {
        "schema_version": "1.0.0", "product_id": case_id, "source_kind": "manual_test",
        "category": load(manual_input / "category-selection.json"),
        "attributes": design["attribute_plan"], "sku_plan": design["sku_plan"],
        "unknown_high_risk_facts": design["product_understanding"]["unknown_high_risk_facts"],
    })
    write_atomic(manual_output / "ecommerce-creative-brief.json", {
        "schema_version": "1.0.0", "product_id": case_id,
        "collection_id": source["collection_id"], "source_kind": "manual_test",
        "source_refs": design["source_refs"],
        "designer_ref": f"test-data/manual-output/{case_id}/ozon-ecommerce-design.json",
        "product_understanding": design["product_understanding"],
        "product_positioning": design["buyer_strategy"],
        "visual_style": design["visual_system"],
        "main_image_roles": design["main_images"], "image_roles": design["detail_images"],
        "preserve": sorted({value for item in [*design["main_images"], *design["detail_images"]] for value in item["must_preserve"]}),
        "forbidden": design["forbidden"],
        "generation_order": ["unified_ecommerce_design", "all_prompts", "one_sku_sample", "single_pass_final_image", "hard_qc", "human_review"],
        "processing": {"step": "ecommerce_creative_brief", "status": "completed", "generated_at": timestamp, "error": None},
    })
    plan = build_plan(case_id, design, source, timestamp)
    write_atomic(manual_output / "image-plan.json", plan)
    return {
        "status": "PASS", "case_id": case_id, "source_kind": "manual_test",
        "main_images": len(plan["main_images"]), "shared_details": len(plan["detail_images"]),
        "total_images": len(plan["main_images"]) + len(plan["detail_images"]),
        "ozon_write_calls": 0, "ozon_read_calls": 0, "inventory_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", default="P900002")
    parser.add_argument("--materialize", action="store_true")
    args = parser.parse_args()
    design, errors = validate_design(args.case_id)
    if errors:
        print(json.dumps({"status": "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    result = materialize(args.case_id, design) if args.materialize else {
        "status": "PASS", "case_id": args.case_id,
        "main_images": len(design["main_images"]), "shared_details": len(design["detail_images"]),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
