#!/usr/bin/env python3
"""Create a strict prompt packet for Codex built-in image generation.

This module intentionally does not call any model or image API.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict

try:
    from scripts.ozon_ecommerce_designer_contract import normalize_creative_prompt_item, selected_skus
    from scripts.image_asset_boundaries import validate_generated_output, validate_product_reference
    from scripts.production_input_guard import validate_formal_product_input
except ModuleNotFoundError:  # Allows direct execution as scripts/image_generator_contract.py.
    from ozon_ecommerce_designer_contract import normalize_creative_prompt_item, selected_skus
    from image_asset_boundaries import validate_generated_output, validate_product_reference
    from production_input_guard import validate_formal_product_input


def load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


IMAGE_TYPE_REQUIREMENTS = {
    "main": {
        "decision_job": "Earn the click in three seconds by showing the real product, its core value and its usage context.",
        "required_composition": "The real product is dominant; the usage context supports it; follow the designer's exact integrated ecommerce text. SKU/model/listing-title text must not become the whole visual idea or overpower the product.",
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


def _main_text_is_compact_proof(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text == "JLC GLOBAL":
        return True
    lowered = text.casefold()
    if len(text) > 26:
        return False
    if any(token in lowered for token in ("модель", "model", "sku", "артикул")):
        return False
    broad_product_terms = (
        "сканер", "держатель", "контейнер", "термос", "поднос", "органайзер",
        "светильник", "бутыл", "сумк", "коврик", "полк", "набор",
    )
    if any(token in lowered for token in broad_product_terms):
        return False
    return any(
        token in lowered
        for token in (
            "usb", "2d", "1d", "qr", "bluetooth", "wifi", "см", "мм", "мл", "л",
            "цвет", "размер", "объем", "объём", "комплект", "касс", "логист",
            "склад", "кухн", "ванн", "стол", "настен", "черн", "бел", "сер",
        )
    )


def normalize_main_image_visible_text(item: Dict[str, Any]) -> None:
    """Keep stale image plans from turning SKU mains into headline posters."""
    if item.get("image_type") != "main":
        return
    compact: list[str] = []
    for value in item.get("russian_text") or []:
        text = str(value or "").strip()
        if _main_text_is_compact_proof(text) and text not in compact and text != "JLC GLOBAL":
            compact.append(text)
        if len(compact) >= 2:
            break
    compact.append("JLC GLOBAL")
    item["russian_text"] = compact
    allowed = set(compact)
    filtered_overlay = [
        value
        for value in item.get("overlay_plan") or []
        if isinstance(value, dict) and str(value.get("text") or "").strip() in allowed
    ]
    if not any(str(value.get("text") or "").strip() == "JLC GLOBAL" for value in filtered_overlay if isinstance(value, dict)):
        filtered_overlay.append({
            "role": "brand_watermark",
            "text": "JLC GLOBAL",
            "box": [0.695, 0.925, 0.22, 0.035],
            "font_size_ratio": 0.018,
            "font_weight": "regular",
            "text_color": "#FFFFFF",
            "accent_color": "#FFFFFF",
            "background_style": "translucent",
            "background_color": "#111827",
            "accent_style": "none",
            "align": "center",
            "vertical_align": "middle",
            "priority": 99,
        })
    item["overlay_plan"] = filtered_overlay
    prompt = str(item.get("prompt") or "")
    prompt = re.sub(r'Text whitelist:[^\n]+', "", prompt)
    item["prompt"] = (
        prompt.strip()
        + "\n\nMain image compact-text override: do not render a large title/SKU/model headline. "
        + "Use only these visible strings if text is needed: "
        + " | ".join(f'"{value}"' for value in compact)
        + ". Product photo quality and factual shape are more important than text."
    ).strip()


def find_slot(plan: Dict[str, Any], slot: str) -> Dict[str, Any]:
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key, []):
            if item.get("slot") == slot:
                return item
    raise ValueError(f"Unknown image slot: {slot}")


def build_prompt_packet(product_dir: Path, slot: str) -> Dict[str, Any]:
    validate_formal_product_input(product_dir)
    plan = load_json(product_dir / "output/image-plan.json")
    source = load_json(product_dir / "input/source.json")
    item = find_slot(plan, slot)
    expected_design_ref = f"products/{product_dir.name}/output/ozon-ecommerce-design.json"

    if plan.get("ecommerce_design_ref") != expected_design_ref:
        raise ValueError("Image plan does not reference this product's ecommerce design")
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
    normalize_creative_prompt_item(item)
    normalize_main_image_visible_text(item)
    russian_text = item.get("russian_text", [])
    art_direction = item.get("art_direction") or {}
    overlay_plan = item.get("overlay_plan") or []
    overlay_text = [
        str(value.get("text") or "").strip()
        for value in overlay_plan
        if isinstance(value, dict) and str(value.get("text") or "").strip()
    ]
    allowed_text = [str(value).strip() for value in russian_text if str(value).strip()]
    if not art_direction or not overlay_text or any(text not in allowed_text for text in overlay_text):
        raise ValueError(f"Image slot {slot} has no product-specific art direction; fixed-template fallback is forbidden")
    slot_prompt = str(item.get("prompt") or item.get("prompt_brief") or "")
    generator_contract = plan.get("generator_contract") or {}
    if not generator_contract.get("must_follow_ecommerce_design"):
        raise ValueError("Image plan must follow the unified ecommerce design")
    if generator_contract.get("ecommerce_design_ref") != expected_design_ref:
        raise ValueError("Image generator contract must point to this product's ecommerce design")
    if generator_contract.get("overlay_strategy") != "single_pass_model_native_typography":
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
    image_sales_strategy = {
        "image_positioning": item.get("image_positioning") or plan.get("image_positioning") or "product-specific Ozon visual sales strategy",
        "main_image_goal": item.get("main_image_goal") or plan.get("main_image_goal") or image_type_contract["decision_job"],
        "visual_style": item.get("visual_style") or plan.get("visual_style") or "product-specific Ozon ecommerce visual style",
        "need_model": bool(item.get("need_model", plan.get("need_model", False))),
        "avoid_style": item.get("avoid_style") or plan.get("avoid_style") or "generic display-only product images",
    }

    return {
        "product_id": product_dir.name,
        "slot": slot,
        "aspect_ratio": "3:4",
        "image_sales_strategy": image_sales_strategy,
        "visual_direction": art_direction,
        "sku_identity": item.get("sku_identity") or "shared facts only",
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
            "image_positioning": image_sales_strategy["image_positioning"],
            "main_image_goal": image_sales_strategy["main_image_goal"],
            "visual_style": image_sales_strategy["visual_style"],
            "need_model": image_sales_strategy["need_model"],
            "avoid_style": image_sales_strategy["avoid_style"],
        },
        "generation_contract": {
            **image_type_contract,
            "advisory_skills_applied": plan["generator_contract"].get("advisory_skills_required", []),
            "advisory_scope": plan["generator_contract"].get("advisory_scope", "unknown"),
            "project_rules_take_precedence": True,
            "image_slot_concurrency": plan["generator_contract"].get("image_slot_concurrency", 1),
            "image_qc_same_execution": plan["generator_contract"].get("image_qc_same_execution", False),
            "conversion_logic_required": True,
            "dominant_product_area_required": True,
            "text_background_high_contrast_required": True,
            "exact_russian_text": russian_text,
            "visible_main_text_policy": (
                "for main images, render at most one or two compact source-backed proof notes plus the small JLC GLOBAL watermark; "
                "do not turn the listing title, SKU name or model into the main visual message"
            ) if item["image_type"] == "main" else "render exact slot Russian text only",
            "operation": operation,
            "background_generation_only": False,
            "product_pixels_must_come_from_reference": operation == "compose_from_real_images",
            "product_body_topology_lock_required": True,
            "composition_tool": (
                "deterministic crop, mask and layout"
                if operation == "compose_from_real_images"
                else "built-in reference-guided generation"
                if operation == "generate_from_reference"
                else "built-in reference image editing"
            ),
            "single_pass_required": True,
            "post_generation_overlay_forbidden": True,
            "brand_watermark_required": "JLC GLOBAL small corner watermark",
            "forbidden_shortcuts": [
                "plain white-background optimization only",
                "fixed category template reused across different products",
                "repeating the same composition with only a background change",
                "generic product photography without a buyer decision purpose",
                "unverified product features, parameters, accessories or certifications",
                "Chinese source text, seller/1688 watermarks, workbench product-id badges or 1688 decorations in the final image",
                "SKU/model/listing-title text used as the main visual message",
                "showing several selectable SKUs as if one order contains all of them",
            ],
            "main_images_first": True,
            "target_total_seconds": 300,
            "quality_gate": [
                "wrong product or SKU",
                "wrong color",
                "changed structure, size proportion, specification, quantity or set composition",
                "Chinese or garbage text",
                "unreadable Russian text",
            ],
        },
        "reference_product_images": item["reference_product_images"],
        "must_preserve": plan["must_preserve"],
        "must_not_change": plan["must_not_change"],
        "instruction": (
            "Create this one final 3:4 Ozon image with the built-in image tool by EDITING the reference_product_images (image-to-image). "
            "Keep the product's real structure, colour and proportions exactly as the references show — do not invent, add or remove any part, mechanism or accessory, and do not copy a different variant's structure from a mixed gallery. "
            "Clean away everything that is NOT the product: the supplier promo background, Chinese text, seller logo/watermark, 3D-render/CGI look and any frame/banner. "
            "Place that unchanged product into a clean, product-led ecommerce scene and infographic layout per slot_prompt. "
            "Use a premium commercial photo feel: believable lens depth, soft shadows, material texture, clean reflections, real environment light and restrained color grading. "
            "Text is secondary and must attach to product proof; render only the exact whitelisted Russian text, never a detached headline block. "
            "Include one subtle JLC GLOBAL corner watermark as brand ownership; do not include product-id preview badges. "
            "Save only the final image; do not create a text-free intermediate or use a local overlay script."
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
