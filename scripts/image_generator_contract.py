#!/usr/bin/env python3
"""Create a strict prompt packet for Codex built-in image generation.

This module intentionally does not call any model or image API.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

try:
    from scripts.style_selector import load_json
    from scripts.ozon_ecommerce_designer_contract import selected_skus
    from scripts.image_asset_boundaries import validate_generated_output, validate_product_reference
    from scripts.production_input_guard import validate_formal_product_input
except ModuleNotFoundError:  # Allows direct execution as scripts/image_generator_contract.py.
    from style_selector import load_json
    from ozon_ecommerce_designer_contract import selected_skus
    from image_asset_boundaries import validate_generated_output, validate_product_reference
    from production_input_guard import validate_formal_product_input


IMAGE_TYPE_REQUIREMENTS = {
    "main": {
        "decision_job": "Earn the click in three seconds by showing the real product, its core value and its usage context.",
        "required_composition": "The real product is dominant; the usage context supports it; the core Russian sales message is immediately readable.",
    },
    "benefit": {
        "decision_job": "Turn one verified product capability or use into one clear buyer benefit.",
        "required_composition": "One benefit only, supported by the real product or a truthful usage action; avoid a feature collage.",
    },
    "feature": {
        "decision_job": "Prove one visible, source-backed reason to buy this exact product.",
        "required_composition": "Show one verified feature with product-specific close-up evidence; do not build a generic icon collage.",
    },
    "scene": {
        "decision_job": "Answer how and where the buyer will use the product.",
        "required_composition": "Show a plausible real-life use environment derived from product positioning, with the real product visibly in use.",
    },
    "usage": {
        "decision_job": "Show one truthful use action so the buyer immediately understands the product.",
        "required_composition": "Use a clear real-life action derived from source facts; never invent functions or accessories.",
    },
    "problem_solution": {
        "decision_job": "Make the pre-purchase problem and the verified solution path understandable at a glance.",
        "required_composition": "Use a clear problem-to-solution visual sequence without claiming unverified performance.",
    },
    "detail": {
        "decision_job": "Build trust with visible, verified construction and operation details.",
        "required_composition": "Use close-up evidence from the references; do not invent material, interfaces, controls or accessories.",
    },
    "size_spec": {
        "decision_job": "Help the buyer judge fit using product-body dimensions, with estimates clearly marked as approximate.",
        "required_composition": "Render product-body measurement guides. Estimated values must say 'Примерные размеры'; package dimensions are forbidden.",
    },
    "comparison": {
        "decision_job": "Help the buyer choose among verified SKU differences.",
        "required_composition": "Compare only source-backed SKU attributes and preserve each SKU's exact product identity.",
    },
    "disclaimer": {
        "decision_job": "Reduce returns by explaining confirmed limitations and information the buyer must verify.",
        "required_composition": "Use a calm, readable information layout; do not turn unknowns into negative product claims.",
    },
}


def find_slot(plan: Dict[str, Any], slot: str) -> Dict[str, Any]:
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key, []):
            if item.get("slot") == slot:
                return item
    raise ValueError(f"Unknown image slot: {slot}")


def build_prompt_packet(product_dir: Path, slot: str) -> Dict[str, Any]:
    validate_formal_product_input(product_dir)
    profile = load_json(product_dir / "output/style-profile.json")
    plan = load_json(product_dir / "output/image-plan.json")
    source = load_json(product_dir / "input/source.json")
    positioning_path = product_dir / "output/product-positioning.json"
    positioning = load_json(positioning_path) if positioning_path.is_file() else {}
    item = find_slot(plan, slot)
    expected_ref = f"products/{product_dir.name}/output/style-profile.json"

    if plan["style_profile_ref"] != expected_ref:
        raise ValueError("Image plan does not reference this product's style-profile.json")
    if plan["style_family"] != profile["style_family"]:
        raise ValueError("Image plan style family does not match style-profile.json")
    if item["image_type"] not in plan.get("image_set_structure", []):
        raise ValueError("Image type is not allowed by the selected image plan")
    if len(plan.get("detail_images") or []) != 8:
        raise ValueError("Image plan must contain exactly 8 shared detail images")
    sku_count = len(selected_skus(source))
    main_images = plan.get("main_images") or []
    if len(main_images) != sku_count:
        raise ValueError(
            f"Image plan must contain one main image per selected SKU: expected {sku_count}, got {len(main_images)}"
        )
    selected_ids = [str(item.get("sku_id") or "") for item in selected_skus(source)]
    planned_ids = [str(item.get("source_sku_id") or "") for item in main_images]
    if planned_ids != selected_ids:
        raise ValueError("Main images must match the selected SKU order exactly")
    if len(main_images) + len(plan.get("detail_images") or []) != sku_count + 8:
        raise ValueError("Image plan total must equal selected SKU count plus 8 shared details")
    if item["status"] == "needs_review" or item["operation"] == "needs_human_input":
        raise ValueError(f"Image slot {slot} is blocked: {item['failure_reason']}")
    if not item["reference_product_images"]:
        raise ValueError("A final product image requires real product references")
    for value in item["reference_product_images"]:
        validate_product_reference(product_dir, value)
    validate_generated_output(product_dir, item.get("output_path"))
    russian_text = item.get("russian_text", [])
    if not russian_text or any(str(value).strip().lower() == "unknown" for value in russian_text):
        raise ValueError(f"Image slot {slot} is blocked: Russian image text is unknown")
    art_direction = item.get("art_direction") or {}
    overlay_plan = item.get("overlay_plan") or []
    if not art_direction or not overlay_plan:
        raise ValueError(f"Image slot {slot} has no product-specific art direction; fixed-template fallback is forbidden")
    slot_prompt = str(item.get("prompt") or item.get("prompt_brief") or "")
    prompt_folded = slot_prompt.casefold()
    forbidden_two_stage_markers = (
        "text-free", "without lettering", "generate no text", "do not add text",
        "no generated typography", "rendered later", "added after generation",
        "无字底图", "后置叠字",
    )
    if any(marker in prompt_folded for marker in forbidden_two_stage_markers):
        raise ValueError(f"Image slot {slot} requests a forbidden text-free or post-overlay workflow")
    if any(str(text).strip() not in slot_prompt for text in russian_text):
        raise ValueError(f"Image slot {slot} prompt must include every exact Russian text item")
    if [str(value.get("text") or "").strip() for value in overlay_plan] != [str(value).strip() for value in russian_text]:
        raise ValueError(f"Image slot {slot} overlay plan does not match its exact Russian text")
    if not plan["generator_contract"].get("fixed_template_fallback_forbidden"):
        raise ValueError("Image plan must explicitly forbid fixed-template fallback")
    if plan["generator_contract"].get("overlay_strategy") != "single_pass_model_native_typography":
        raise ValueError("Image plan must use single-pass model-native typography")

    image_type_contract = IMAGE_TYPE_REQUIREMENTS[item["image_type"]]
    operation = str(item.get("operation") or "needs_human_input")
    measurement_annotation = item.get("measurement_annotation")
    if item["image_type"] == "size_spec":
        if not measurement_annotation:
            raise ValueError("Size image requires product-body dimensions from the measurement module")
        if measurement_annotation.get("source_field") != "cost-analysis.product_dimensions":
            raise ValueError("Size image must not use package dimensions")
        if measurement_annotation.get("estimated") and not any(
            "Примерные размеры" in str(value) for value in russian_text
        ):
            raise ValueError("Estimated size image must be labelled 'Примерные размеры'")

    return {
        "product_id": product_dir.name,
        "slot": slot,
        "aspect_ratio": "3:4",
        "style_family": profile["style_family"],
        "style_direction": art_direction,
        "creative_direction": profile.get("creative_direction") or plan.get("creative_direction") or {},
        "ecommerce_creative_brief": load_json(product_dir / "output/ecommerce-creative-brief.json") if (product_dir / "output/ecommerce-creative-brief.json").is_file() else {},
        "learned_image_preferences": plan.get("learned_image_preferences") or [],
        "final_listing_context": plan.get("listing_context") or {},
        "product_positioning": {
            "market_positioning": positioning.get("market_positioning", "unknown"),
            "target_customer": positioning.get("target_customer", "unknown"),
            "purchase_motivation": positioning.get("purchase_motivation", "unknown"),
            "customer_pain_points": positioning.get("customer_pain_points", ["unknown"]),
            "core_sales_angle": positioning.get("core_sales_angle", "unknown"),
            "emotional_trigger": positioning.get("emotional_trigger", "unknown"),
            "competitive_advantage": positioning.get("competitive_advantage", "unknown"),
        },
        "image_intent": {
            "image_type": item["image_type"],
            "buyer_question": item["buyer_question"],
            "purchase_reason": item["purchase_reason"],
            "visual_goal": item["visual_goal"],
            "scene_description": item["scene_description"],
            "selling_goal": item["selling_goal"],
            "scene": item["scene"],
            "russian_text": item["russian_text"],
            "design_rationale": item["design_rationale"],
            "art_direction": art_direction,
            "overlay_plan": overlay_plan,
            "measurement_annotation": measurement_annotation,
            "style_direction": item["style_direction"],
            "visual_direction": item["visual_direction"],
        },
        "buyer_objections": plan.get("buyer_objections", ["unknown"]),
        "generation_contract": {
            **image_type_contract,
            "advisory_skills_applied": plan["generator_contract"].get("advisory_skills_required", []),
            "advisory_scope": plan["generator_contract"].get("advisory_scope", "unknown"),
            "project_rules_take_precedence": True,
            "image_slot_concurrency": plan["generator_contract"].get("image_slot_concurrency", 1),
            "image_qc_same_execution": plan["generator_contract"].get("image_qc_same_execution", False),
            "conversion_logic_required": True,
            "exact_russian_text": russian_text,
            "operation": operation,
            "background_generation_only": False,
            "product_pixels_must_come_from_reference": operation == "compose_from_real_images",
            "composition_tool": (
                "deterministic crop, mask and layout"
                if operation == "compose_from_real_images"
                else "built-in reference image editing"
            ),
            "single_pass_required": True,
            "post_generation_overlay_forbidden": True,
            "fixed_template_fallback_forbidden": True,
            "forbidden_shortcuts": [
                "plain white-background optimization only",
                "fixed category template reused across different products",
                "repeating the same composition with only a background change",
                "generic product photography without a buyer decision purpose",
                "unverified product features, parameters, accessories or certifications",
                "Chinese source text, seller watermarks, or 1688 decorations in the final image",
                "showing several selectable SKUs as if one order contains all of them",
            ],
            "main_images_first": True,
            "target_total_seconds": 300,
            "quality_gate": [
                "wrong product or SKU",
                "wrong color",
                "invented accessory or function",
                "obvious deformation",
                "Chinese or garbage text",
                "unreadable Russian text",
                "large blank placeholder box or empty bordered panel",
            ],
        },
        "reference_product_images": item["reference_product_images"],
        "required_visual_signals": profile["generator_constraints"]["required_visual_signals"],
        "forbidden_visual_signals": profile["generator_constraints"]["forbidden_visual_signals"],
        "truthfulness_guardrails": profile["generator_constraints"]["truthfulness_guardrails"],
        "must_preserve": plan["must_preserve"],
        "must_not_change": plan["must_not_change"],
        "instruction": (
            "Use deterministic crop, mask and layout from the real references; AI must not redraw the product. "
            if operation == "compose_from_real_images"
            else "Use the built-in image editor with the supplied real product references. Preserve product identity, color, structure, proportions, markings and accessories while creating the requested scene. "
        ) + (
            "Reject low-resolution thumbnails instead of enlarging them. In one built-in image-model call, create the final product scene and render every exact verified Russian text line from this slot's overlay_plan. Do not create a text-free intermediate and do not call any post-generation overlay script. "
            "Use natural negative space instead of a blank rounded rectangle, empty text box, placeholder card, bordered panel or decorative empty frame. "
            "Do not add a default header, badge, benefit rail, palette or card layout. Reject missing, garbled, misspelled or unreadable Russian, Chinese text and seller watermarks, and never imply that all selectable SKU variants are included in one order. Product type, color, visible structure, accessory count and believable overall proportions must stay correct; pixel-for-pixel identity is not required."
        ),
        "slot_prompt": slot_prompt,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Codex image generation prompt packet.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("slot", help="Image plan slot, for example main-001")
    args = parser.parse_args()
    try:
        packet = build_prompt_packet(Path(args.product_dir).resolve(), args.slot)
    except ValueError as error:
        print(f"BLOCKED: {error}")
        return 2
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
