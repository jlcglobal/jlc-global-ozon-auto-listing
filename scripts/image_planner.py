#!/usr/bin/env python3
"""Build a style-constrained image plan without calling any AI API."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    from scripts.style_selector import ROOT, load_json, write_json_atomic
except ModuleNotFoundError:  # Allows direct execution as scripts/image_planner.py.
    from style_selector import ROOT, load_json, write_json_atomic


STRUCTURES_PATH = ROOT / "rules" / "image_structure_rules.json"


def purchase_reason_for(
    image_type: str,
    positioning: Dict[str, Any],
    objections: List[str],
) -> str:
    """Give every slot a distinct job in the buyer decision path."""
    motivation = positioning.get("purchase_motivation", "unknown")
    sales_angle = positioning.get("core_sales_angle", "unknown")
    advantage = positioning.get("competitive_advantage", "unknown")
    pain_points = positioning.get("customer_pain_points", [])
    main_pain = next((value for value in pain_points if value != "unknown"), "unknown")
    first_objection = next((value for value in objections if value != "unknown"), "unknown")
    reasons = {
        "main": motivation,
        "benefit": sales_angle,
        "feature": f"用一个可见且有来源的产品特点证明购买理由：{advantage}",
        "scene": f"让目标用户在真实环境中理解购买后的使用方式：{motivation}",
        "usage": "展示一个真实、容易理解的使用动作，降低买家理解成本",
        "problem_solution": f"回应购买前问题“{main_pain}”，并用已确认产品用途说明解决路径",
        "detail": f"用真实产品结构证明购买理由：{advantage}",
        "size_spec": "用商品本体尺寸帮助买家判断是否适配；估算值必须明确标注为约数",
        "comparison": "帮助买家根据已确认的SKU差异选择正确版本，避免误购",
        "disclaimer": f"购买前说明限制与未确认信息，降低退货风险：{first_objection}",
    }
    return reasons[image_type]


def sku_preflight_map(preflight: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("source_sku_id") or ""): item
        for item in preflight.get("sku_references") or []
    }


def usable_reference_images(source: Dict[str, Any], preflight: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    references = []
    for role, collection in (("main", source.get("main_images", [])), ("detail", source.get("detail_images", []))):
        for item in collection:
            if item.get("download_status") != "downloaded":
                continue
            path = str(item.get("local_path", "unknown"))
            url = str(item.get("original_url", "unknown"))
            if path == "unknown" or url.lower().endswith(".svg"):
                continue
            references.append({
                "id": item["id"],
                "path": path,
                "role": role,
                "usable": True,
                "notes": "真实采集商品图；生成前仍需检查Logo、文字和SKU一致性。",
            })
    for index, sku in enumerate(source.get("skus", []), start=1):
        check = sku_preflight_map(preflight or {}).get(str(sku.get("sku_id") or ""), {})
        path = str(check.get("preferred_reference_path") or sku.get("local_image_path", "unknown"))
        if sku.get("sku_image_missing") or path == "unknown":
            continue
        ready = check.get("status", "ready") == "ready"
        references.append({
            "id": f"sku-{index:03d}",
            "path": path,
            "role": "sku",
            "usable": ready,
            "notes": (
                f"仅关联真实SKU {sku.get('sku_id', 'unknown')}；生图前清晰度检查通过。"
                if ready else str(check.get("reason") or "SKU参考图清晰度不足。")
            ),
        })
    return references


def facts_are_unknown(analysis: Dict[str, Any], fields: List[str]) -> bool:
    facts = analysis.get("facts", {})
    for field in fields:
        value = facts.get(field)
        if value not in (None, "unknown", [], ["unknown"]):
            return False
    return True


def _display_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{float(value):g}"


def product_dimension_annotation(product_dir: Path) -> Dict[str, Any] | None:
    """Return buyer-facing product dimensions, never package dimensions."""
    path = product_dir / "output" / "cost-analysis.json"
    if not path.is_file():
        return None
    dimensions = load_json(path).get("product_dimensions") or {}
    try:
        values = {key: float(dimensions[key]) for key in ("length", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    if any(value <= 0 for value in values.values()) or dimensions.get("unit") != "cm":
        return None
    estimated = bool(dimensions.get("estimated")) or dimensions.get("source") == "estimated"
    numbers = " × ".join(_display_number(values[key]) for key in ("length", "width", "height"))
    label = "Примерные размеры" if estimated else "Размеры"
    return {
        **values,
        "unit": "cm",
        "estimated": estimated,
        "confidence": int(dimensions.get("confidence") or 0),
        "source": str(dimensions.get("source") or "unknown"),
        "source_ref": str(dimensions.get("source_ref") or "unknown"),
        "source_field": "cost-analysis.product_dimensions",
        "label_ru": f"{label} (Д × Ш × В): {numbers} см",
    }


def unknown_field_matches(analysis: Dict[str, Any], keywords: List[str]) -> bool:
    fields = [str(item.get("field", "")).lower() for item in analysis.get("unknowns", []) if isinstance(item, dict)]
    return any(keyword.lower() in field for field in fields for keyword in keywords)


def buyer_objections(analysis: Dict[str, Any], positioning: Dict[str, Any]) -> List[str]:
    objections = []
    for item in analysis.get("missing_information", []):
        if isinstance(item, dict):
            objections.append(f"{item.get('field', 'unknown')}: {item.get('reason', 'unknown')}")
    for item in analysis.get("risks", []):
        if isinstance(item, dict) and item.get("blocking"):
            objections.append(str(item.get("message", "unknown")))
    objections.extend(positioning.get("unknowns", []))
    return list(dict.fromkeys(value for value in objections if value and value != "unknown")) or ["unknown"]


def _copy_texts(copy: Dict[str, Any], key: str) -> List[str]:
    values = []
    for item in copy.get(key) or []:
        value = item.get("text_ru") if isinstance(item, dict) else item
        text = str(value or "").strip()
        if text and text.lower() != "unknown":
            values.append(text)
    return values


def russian_image_text(
    image_type: str,
    copy: Dict[str, Any],
    dimension: Dict[str, Any] | None,
    used: set[str] | None = None,
) -> List[str]:
    if image_type == "size_spec" and dimension:
        return [dimension["label_ru"]]
    used = used if used is not None else set()
    bullets = _copy_texts(copy, "bullets_ru")
    selling = _copy_texts(copy, "selling_points")
    scenarios = _copy_texts(copy, "usage_scenarios")
    warnings = _copy_texts(copy, "warning")
    keywords = [str(value).strip() for value in copy.get("keywords_ru") or [] if str(value).strip()]
    verified_short_claims: List[str] = []
    combined_bullets = " ".join(bullets).lower()
    if "влаг" in combined_bullets and "насеком" in combined_bullets:
        verified_short_claims.append("Защита от влаги и насекомых")
    if "гермет" in combined_bullets:
        verified_short_claims.append("Герметичное хранение")
    candidates = {
        "main": [*(selling[:1]), *(bullets[:1]), copy.get("short_title")],
        "benefit": [*(keywords[1:2]), *(selling[:1]), *(bullets[:1])],
        "feature": [*(verified_short_claims[:1]), *(selling[1:2]), *(keywords[4:5]), *(bullets[1:2])],
        "scene": [*(scenarios[:1]), *(bullets[:1])],
        "usage": [*(bullets[:1]), *(scenarios[1:2]), *(keywords[5:6])],
        "problem_solution": [*(verified_short_claims[1:2]), *(keywords[-1:]), *(bullets[1:2]), *(selling[:1])],
        "detail": [*(keywords[:1]), *(selling[1:2]), *(bullets[2:3])],
        "comparison": [*(bullets[2:3]), *(selling[1:2])],
        "disclaimer": [*(warnings[:1]), *(bullets[:1])],
    }.get(image_type, [copy.get("short_title")])
    normalized = [str(value).strip() for value in candidates if value and str(value).strip().lower() != "unknown"]
    if image_type == "main":
        text = next((value for value in normalized if len(value) <= 100), None)
    else:
        text = next((value for value in normalized if value not in used and len(value) <= 100), None)
    if text is None:
        text = next((value for value in normalized if value not in used), "unknown")
    if text != "unknown":
        text = text[0].upper() + text[1:] if text else text
        used.add(text)
    return [text]


def output_path(
    product_id: str,
    image_type: str,
    counters: Dict[str, int],
    source_sku_id: str | None = None,
) -> str:
    if image_type == "main":
        counters["main"] += 1
        if source_sku_id:
            safe_sku = re.sub(r"[^A-Za-z0-9._-]+", "-", source_sku_id).strip("-") or f"sku-{counters['main']:03d}"
            return f"products/{product_id}/output/generated-images/stage3.4/variant-main/{safe_sku}.png"
        return f"products/{product_id}/output/generated-images/stage3.4/main/main-{counters['main']:03d}.png"
    if image_type == "disclaimer":
        counters["disclaimer"] += 1
        return f"products/{product_id}/output/generated-images/stage3.4/disclaimer/disclaimer-{counters['disclaimer']:03d}.png"
    counters["detail"] += 1
    return f"products/{product_id}/output/generated-images/stage3.4/detail/detail-{counters['detail']:03d}.png"


def variant_main_specs(
    product_dir: Path,
    source: Dict[str, Any],
    preflight: Dict[str, Any] | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Plan one SKU-specific main for supported color, size, or quantity differences."""
    skus = sorted(source.get("skus") or [], key=lambda item: int(item.get("selection_order") or 9999))
    decision_path = product_dir / "output/variant-decision.json"
    decision = load_json(decision_path) if decision_path.is_file() else {}
    supported_kinds = {"color", "size_or_measurement", "configuration"}
    differences = [
        item for item in decision.get("detected_difference_fields") or []
        if item.get("difference_kind") in supported_kinds
    ]
    detected_differences = list(decision.get("detected_difference_fields") or [])
    fallback_seller_specs = bool(skus) and (not differences or bool(detected_differences))
    if not skus:
        return [], {
            "mode": "single_shared_main",
            "variant_kinds": [],
            "variant_main_count": 1,
            "shared_detail_count": 0,
            "shared_disclaimer_count": 0,
        }

    effective_differences = differences or detected_differences
    differing_fields = {str(item.get("source_field") or "") for item in effective_differences}
    kinds = (
        list(dict.fromkeys(str(item["difference_kind"]) for item in differences))
        if differences else ["seller_specification"]
    )
    checks = sku_preflight_map(preflight or {})
    specs = []
    for sku in skus:
        sku_id = str(sku.get("sku_id") or "unknown")
        check = checks.get(sku_id, {})
        values = [
            str(option.get("value_cn") or "unknown")
            for option in sku.get("option_values") or []
            if str(option.get("name_cn") or "") in differing_fields
        ]
        reference = str(check.get("preferred_reference_path") or sku.get("variant_local_image_path") or sku.get("local_image_path") or "unknown")
        specs.append({
            "source_sku_id": sku_id,
            "sku_name": str(sku.get("sku_name") or "unknown"),
            "variant_kind": kinds[0] if len(kinds) == 1 else "mixed_supported",
            "variant_value": " / ".join(values) if values else str(sku.get("sku_name") or "unknown"),
            "reference_path": reference,
            "reference_ready": check.get("status", "ready") == "ready",
            "reference_failure_reason": str(check.get("reason") or "unknown"),
        })
    return specs, {
        "mode": "sku_specific_main_shared_details",
        "variant_kinds": kinds,
        "variant_main_count": len(specs),
        "shared_detail_count": 0,
        "shared_disclaimer_count": 0,
    }


def build_image_plan(
    product_dir: Path,
    source: Dict[str, Any],
    analysis: Dict[str, Any],
    style_profile: Dict[str, Any],
    started_at: str | None = None,
) -> Dict[str, Any]:
    structures = load_json(STRUCTURES_PATH)
    pipeline_settings = load_json(ROOT / "config/pipeline-settings.json")
    product_id = product_dir.name
    expected_structure = list(style_profile["image_set_structure"])
    if not expected_structure or expected_structure[0] != "main":
        raise ValueError("style-profile must provide a product-specific structure beginning with main")
    if style_profile["classification_status"] != "selected":
        raise ValueError("style-profile must be selected before a final image plan is created")

    preflight_path = product_dir / "output" / "image-source-preflight.json"
    preflight = load_json(preflight_path) if preflight_path.is_file() else {}
    references = usable_reference_images(source, preflight)
    positioning_path = product_dir / "output" / "product-positioning.json"
    positioning = load_json(positioning_path) if positioning_path.is_file() else {}
    objections = buyer_objections(analysis, positioning)
    usable = [item for item in references if item.get("usable")]
    sku_references = [item for item in usable if item.get("role") == "sku"]
    main_references = list(reversed([item for item in usable if item.get("role") == "main"]))
    other_references = [item for item in usable if item.get("role") not in {"sku", "main"}]
    selected_references = [*sku_references, *main_references, *other_references][:5]
    reference_paths = [item["path"] for item in selected_references]
    reference_ids = [item["id"] for item in selected_references]
    counters = {"main": 0, "detail": 0, "disclaimer": 0}
    planned = {"main_images": [], "detail_images": [], "disclaimer_images": []}
    main_specs, variant_image_strategy = variant_main_specs(product_dir, source, preflight)
    dimension_annotation = product_dimension_annotation(product_dir)
    copy_path = product_dir / "output" / "copy-ru.json"
    copy = load_json(copy_path) if copy_path.is_file() else {}
    used_russian_text: set[str] = set()

    creative = style_profile.get("creative_direction") or {
        "product_visual_thesis": style_profile.get("composition_style", "unknown"),
        "click_hook": positioning.get("core_sales_angle", "unknown"),
        "hero_scene": positioning.get("recommended_visual_direction", "unknown"),
        "typography": style_profile.get("text_style", "unknown"),
        "consistency_rule": "整套视觉语气一致，每张图回答不同购买问题。",
        "anti_template_rule": "不得使用普通白底或固定类目模板。",
    }

    for image_type in expected_structure:
        requested_image_type = image_type
        fallback_reason = "unknown"
        if image_type == "comparison" and len(source.get("skus", [])) < 2:
            image_type = "detail"
            fallback_reason = "单SKU没有真实对比对象，改为第二张真实结构展示。"
        contract = structures["slot_contracts"][image_type]
        repetitions = main_specs if image_type == "main" and main_specs else [None]
        for main_spec in repetitions:
            source_sku_id = main_spec["source_sku_id"] if main_spec else None
            path = output_path(product_id, image_type, counters, source_sku_id)
            if image_type == "main":
                collection = "main_images"
                slot = f"main-{source_sku_id}" if source_sku_id else f"main-{counters['main']:03d}"
            elif image_type == "disclaimer":
                collection = "disclaimer_images"
                slot = f"disclaimer-{counters['disclaimer']:03d}"
            else:
                collection = "detail_images"
                slot = f"detail-{counters['detail']:03d}"

            variant_reference = [main_spec["reference_path"]] if main_spec and main_spec["reference_path"] != "unknown" else []
            item_reference_paths = variant_reference if main_spec else reference_paths
            item_reference_ids = [f"sku-{source_sku_id}"] if variant_reference else reference_ids
            missing_dimensions = image_type == "size_spec" and dimension_annotation is None
            low_resolution_variant = bool(main_spec and not main_spec.get("reference_ready", True))
            missing_exact_sku_references = image_type == "comparison" and bool(preflight.get("blocked_sku_ids"))
            blocked_for_input = not item_reference_paths or missing_dimensions or low_resolution_variant or missing_exact_sku_references
            status = "needs_review" if blocked_for_input else "planned"
            failure_reason = (
                str(main_spec.get("reference_failure_reason") or "当前SKU参考图不清晰，禁止放大抠图或继续生成。")
                if low_resolution_variant
                else "当前SKU没有真实关联图片，禁止猜测或使用其他变体主图。"
                if main_spec and blocked_for_input
                else "存在SKU参考图清晰度不足，禁止制作尺寸或SKU对比图。"
                if missing_exact_sku_references
                else "缺少measurement模块生成的商品本体尺寸，禁止使用包装尺寸冒充商品尺寸。"
                if missing_dimensions
                else "没有可用真实商品图，禁止生成最终商品图片。"
                if blocked_for_input
                else "unknown"
            )
            operation = (
                "needs_human_input" if status == "needs_review"
                else "compose_from_real_images" if image_type in {"comparison", "size_spec"}
                else "edit_real_image"
            )

            item = {
            "type": "main" if image_type == "main" else "disclaimer" if image_type == "disclaimer" else "detail",
            "slot": slot,
            "image_type": image_type,
            "requested_image_type": requested_image_type,
            "fallback_reason": fallback_reason,
            "purchase_reason": purchase_reason_for(image_type, positioning, objections),
            "visual_goal": f"{contract['selling_goal']}；服务于核心销售角度：{positioning.get('core_sales_angle', 'unknown')}",
            "scene_description": "；".join([
                positioning.get("recommended_visual_direction", "unknown"),
                *style_profile["usage_scene"],
            ]),
            "style_direction": "；".join([
                str(creative.get("product_visual_thesis") or "unknown"),
                str(creative.get("consistency_rule") or "unknown"),
            ]),
            "purpose": contract["selling_goal"],
            "buyer_question": contract["buyer_question"],
            "selling_goal": contract["selling_goal"],
            "scene": "；".join(style_profile["usage_scene"]),
            "russian_text": russian_image_text(image_type, copy, dimension_annotation, used_russian_text),
            "visual_direction": "；".join([
                str(creative.get("click_hook") if image_type == "main" else creative.get("hero_scene") or "unknown"),
                str(creative.get("typography") or "unknown"),
                str(creative.get("anti_template_rule") or "unknown"),
            ]),
            "reference_product_images": item_reference_paths,
            "reference_images": item_reference_paths,
            "reference_image_ids": item_reference_ids,
            "variant_scope": "sku" if main_spec else "shared",
            "shared_across_variants": main_spec is None,
            "source_sku_id": source_sku_id or "all",
            "variant_kind": main_spec["variant_kind"] if main_spec else "not_applicable",
            "variant_value": main_spec["variant_value"] if main_spec else "shared",
            "operation": operation,
            "source_text_policy": "generate_new_ozon_image_and_reject_all_chinese_text_or_seller_watermarks",
            "prompt": (
                f"Create a product-specific 3:4 Ozon {image_type} visual. "
                f"Visual thesis: {creative.get('product_visual_thesis', 'unknown')}. "
                + (
                    "Use deterministic crop, mask and layout from the real reference images; AI must not redraw any product. "
                    if operation == "compose_from_real_images"
                    else "Edit the supplied real product reference into a distinctive buyer-facing atmosphere while preserving its identity, color, structure, proportions and accessories. "
                )
                + "Do not use a plain white background or a reusable category template. Shared images must not imply that multiple SKU variants are included together."
            ),
            "prompt_brief": f"3:4 product-specific visual · {image_type} · {creative.get('click_hook', 'unknown')}",
            "output_path": path,
            "status": status,
            "failure_reason": failure_reason,
            }
            if image_type == "size_spec" and dimension_annotation:
                item["measurement_annotation"] = dimension_annotation
            planned[collection].append(item)

    timestamp = started_at or datetime.now().astimezone().replace(microsecond=0).isoformat()
    detail_count = len(planned["detail_images"])
    variant_image_strategy["shared_detail_count"] = detail_count
    variant_image_strategy["shared_disclaimer_count"] = len(planned["disclaimer_images"])
    style_ref = f"products/{product_id}/output/style-profile.json"
    positioning_ref = f"products/{product_id}/output/product-positioning.json"
    source_refs = [
        f"products/{product_id}/input/source.json",
        f"products/{product_id}/output/product-analysis.json",
    ]
    if (product_dir / "output" / "product-positioning.json").is_file():
        source_refs.append(positioning_ref)
    source_refs.append(style_ref)
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "source_refs": source_refs,
        "style_profile_ref": style_ref,
        "style_family": style_profile["style_family"],
        "image_structure_rule_ref": "dynamic:product-specific-buyer-decision-sequence",
        "image_set_structure": expected_structure,
        "variant_image_strategy": variant_image_strategy,
        "buyer_objections": objections,
        "generator_contract": {
            "must_follow_style_profile": True,
            "style_profile_ref": style_ref,
            "allowed_structure": expected_structure,
            "aspect_ratio": "3:4",
            "deviation_requires_review": False,
            "advisory_skills_required": [],
            "advisory_scope": "none",
            "project_rules_take_precedence": True,
            "image_slot_concurrency": max(1, min(int(pipeline_settings.get("image_slot_concurrency", 3)), 4)),
            "image_qc_same_execution": bool(pipeline_settings.get("merge_image_generation_and_qc", True)),
            "product_pixel_lock_required": False,
            "composition_tool": "deterministic_layout_for_comparison_and_measurement_only",
            "source_preflight_ref": f"products/{product_id}/output/image-source-preflight.json",
            "generation_strategy": "product_specific_visual_story",
            "deterministic_image_types": ["comparison", "size_spec"],
            "ai_reference_edit_image_types": ["main", "benefit", "scene", "problem_solution", "feature", "detail", "usage"],
            "raw_1688_image_direct_upload_forbidden": True,
            "final_chinese_text_forbidden": True,
            "plain_white_background_forbidden": True,
            "main_images_first": True,
            "target_total_seconds": 300,
            "quality_gate": "hard_failures_only",
            "typography_strategy": "adaptive_exact_russian_without_fixed_box",
        },
        "creative_direction": creative,
        "learned_image_preferences": style_profile.get("learned_image_preferences") or [],
        "reference_images": references,
        "buyer_analysis": {
            "who_buys": [positioning["target_customer"]] if positioning.get("target_customer") not in (None, "unknown") else style_profile["target_user"],
            "why_buy": [positioning["purchase_motivation"]] if positioning.get("purchase_motivation") not in (None, "unknown") else style_profile["purchase_motivation"],
            "main_pain_point": next(
                (value for value in positioning.get("customer_pain_points", []) if value != "unknown"),
                "unknown",
            ),
            "strongest_selling_point": positioning.get("core_sales_angle", "unknown"),
            "proof_strategy": [
                positioning.get("recommended_visual_direction", "unknown"),
                *style_profile["image_strategy"],
            ],
        },
        **planned,
        "must_preserve": ["vertical 3:4 ratio", "product structure", "product colors", "SKU differences", "accessory count"],
        "must_not_change": ["material", "dimensions", "weight", "load capacity", "certifications", "brand", "functions", "package quantity", "accessories"],
        "forbidden_content": [
            *style_profile["generator_constraints"]["forbidden_visual_signals"],
            "unverified claims",
            "unverified brand logos",
            "extra accessories",
            "different product structure or color",
        ],
        "risks": [
            {
                "area": "style",
                "level": "high",
                "message": "只拦截错商品、错SKU、额外配件功能、明显变形、中文乱码和不可读俄文；其他视觉选择交给商品专属方案。",
            },
            {
                "area": "russian_text",
                "level": "medium",
                "message": "俄文上图前必须由Codex生成并在生成后逐字检查。",
            },
        ],
        "processing": {
            "step": "image_plan",
            "status": "completed",
            "started_at": timestamp,
            "finished_at": timestamp,
            "error": None,
        },
    }


def evidence_texts(items: Any) -> List[str]:
    values = []
    for item in items or []:
        if isinstance(item, dict) and item.get("text"):
            values.append(str(item["text"]))
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a style-constrained image plan.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--write", action="store_true", help="Write output/image-plan.json; default prints only")
    args = parser.parse_args()

    product_dir = Path(args.product_dir).resolve()
    plan = build_image_plan(
        product_dir,
        load_json(product_dir / "input/source.json"),
        load_json(product_dir / "output/product-analysis.json"),
        load_json(product_dir / "output/style-profile.json"),
    )
    if args.write:
        output = product_dir / "output/image-plan.json"
        write_json_atomic(output, plan)
        print(output)
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
