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
    from scripts.ozon_ecommerce_designer_contract import validate_design
    from scripts.image_asset_boundaries import validate_generated_output, validate_product_reference
    from scripts.production_input_guard import validate_formal_product_input
except ModuleNotFoundError:  # Allows direct execution as scripts/image_planner.py.
    from style_selector import ROOT, load_json, write_json_atomic
    from ozon_ecommerce_designer_contract import validate_design
    from image_asset_boundaries import validate_generated_output, validate_product_reference
    from production_input_guard import validate_formal_product_input


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
        reference_override = check.get("reference_override") if isinstance(check.get("reference_override"), dict) else None
        if (sku.get("sku_image_missing") and not reference_override) or path == "unknown":
            continue
        ready = check.get("status", "ready") in {"ready", "ready_with_warning"}
        references.append({
            "id": f"sku-{index:03d}",
            "path": path,
            "role": "sku",
            "usable": ready,
            "notes": (
                f"人工确认同外观SKU {reference_override.get('source_sku_id')} 的真实图片；仅共用图片，不继承源SKU规格、价格或文案。"
                if reference_override
                else
                f"仅关联真实SKU {sku.get('sku_id', 'unknown')}；生图前清晰度检查通过。"
                if check.get("status", "ready") == "ready"
                else str(check.get("reason") or "SKU参考图可用于参考编辑，但清晰度低于推荐值。")
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


def final_listing_context(product_dir: Path, source: Dict[str, Any]) -> Dict[str, Any]:
    """Load the already materialized listing once; image planning must not reanalyse the product."""
    output = product_dir / "output"
    def optional(name: str) -> Dict[str, Any]:
        path = output / name
        return load_json(path) if path.is_file() else {}

    title = optional("title-ru.json")
    description = optional("description-ru.json")
    tags = optional("ozon-tags.json")
    attributes = optional("ozon-attributes-final.json")
    pricing = optional("pricing-result.json")
    grouping = optional("platform-grouping-result.json")
    final_attributes = []
    for item in attributes.get("attributes") or []:
        value = item.get("value")
        if value in (None, "", "unknown", [], ["unknown"]):
            continue
        name = str(item.get("attribute_name") or item.get("field_key") or "unknown")
        normalized_name = name.casefold()
        if any(token in normalized_name for token in (
            "аннотац", "rich", "хештег", "название модели", "код продавца",
        )) or normalized_name == "название":
            continue
        final_attributes.append({
            "name": name,
            "value": value,
            "source": str(item.get("source") or "unknown"),
            "confidence": item.get("confidence"),
        })
    sku_prices = {
        str(item.get("sku_id")): item.get("selling_price_rub")
        for item in pricing.get("sku_pricing") or []
        if item.get("selling_price_rub") is not None
    }
    sku_variants = [{
        "sku_id": str(item.get("sku_id") or "unknown"),
        "name": str(item.get("sku_name") or "unknown"),
        "option_values": item.get("option_values") or [],
        "selling_price_rub": sku_prices.get(str(item.get("sku_id"))),
    } for item in source.get("skus") or []]
    production_required_files = (
        "title-ru.json", "description-ru.json", "ozon-tags.json",
        "ozon-attributes-final.json", "pricing-result.json",
        "platform-grouping-result.json",
    )
    required_files = production_required_files
    source_refs = [
        f"products/{product_dir.name}/output/{name}"
        for name in required_files if (output / name).is_file()
    ]
    return {
        "ready": len(source_refs) == len(required_files),
        "offline_acceptance_fixture": False,
        "pricing_status": "required",
        "source_refs": source_refs,
        "title_ru": str(title.get("title_ru") or "unknown"),
        "description_summary_ru": str(description.get("description_ru") or "unknown")[:600],
        "tags": list(tags.get("tags") or []),
        "attributes": final_attributes,
        "sku_variants": sku_variants,
        "variant_decision": grouping.get("variant_decision") or grouping.get("result") or grouping,
        "upload_strategy": grouping.get("upload_strategy") or "",
    }


def prompt_fact_summary(context: Dict[str, Any], limit: int = 8) -> str:
    facts = []
    for item in context.get("attributes") or []:
        name = str(item.get("name") or "unknown")
        if any(token in name.casefold() for token in (
            "вес", "размер", "габарит", "упаков", "количество", "страна", "бренд", "код продавца",
        )):
            continue
        value = item.get("value")
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        rendered = rendered.replace("\n", " ")[:160]
        facts.append(f"{name}: {rendered}")
        if len(facts) >= limit:
            break
    return "; ".join(facts) or "no additional verified visual facts"


def _variant_display_value(sku: Dict[str, Any]) -> str:
    """Return the seller's exact selected value for prompt grounding."""
    source_data = sku.get("source_data") or {}
    return str(
        source_data.get("sku_image_prop_value")
        or next((item.get("value_cn") for item in sku.get("option_values") or [] if item.get("value_cn")), None)
        or sku.get("sku_name")
        or "unknown"
    ).strip()


def _image_listing_title(context: Dict[str, Any], grouping: Dict[str, Any]) -> str:
    title = str(context.get("title_ru") or "unknown").strip()
    strategy = str(grouping.get("upload_strategy") or "")
    if strategy == "separate_cards":
        # A separate Ozon card must not advertise a multi-colour assortment.
        title = re.sub(r"\s*[,，-]?\s*(?:\d+\s*цвет(?:а|ов)?|в\s+тр[её]х\s+цветах)\s*$", "", title, flags=re.IGNORECASE).strip(" ,，-")
    return title or "unknown"


def _sku_prompt_facts(context: Dict[str, Any], sku: Dict[str, Any] | None, limit: int = 8) -> str:
    """Use shared facts plus the exact SKU value; never leak a product-level colour."""
    base = []
    for item in context.get("attributes") or []:
        name = str(item.get("name") or "unknown")
        normalized = name.casefold()
        if any(token in normalized for token in (
            "цвет", "комплектац", "вес", "размер", "габарит", "упаков", "количество", "страна", "бренд", "код продавца",
        )):
            continue
        value = item.get("value")
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        base.append(f"{name}: {rendered.replace(chr(10), ' ')[:160]}")
        if len(base) >= limit:
            break
    if sku:
        base.append(f"Точный выбранный SKU: {_variant_display_value(sku)}")
    return "; ".join(base) or "no additional verified visual facts"


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


def compact_image_phrase(value: Any, limit: int = 70) -> str:
    """Turn existing Russian copy into safe image text without blocking the batch."""
    text = str(value or "").strip()
    if not text or text.casefold() == "unknown":
        return ""
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    if len(first_sentence) <= limit:
        return first_sentence
    shortened = first_sentence[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return shortened or first_sentence[:limit]


def russian_image_text(
    image_type: str,
    copy: Dict[str, Any],
    dimension: Dict[str, Any] | None,
    source_sku_id: str | None = None,
    used: set[str] | None = None,
) -> List[str]:
    if image_type == "size_spec" and dimension:
        return [dimension["label_ru"]]
    image_copy = copy.get("image_copy_ru")
    if isinstance(image_copy, dict):
        phrases = None
        if image_type == "main" and source_sku_id:
            phrases = (image_copy.get("main_by_sku") or {}).get(str(source_sku_id))
        phrases = phrases or image_copy.get(image_type)
        if phrases:
            return [str(value).strip() for value in phrases]

    # Legacy-only fallback for isolated unit fixtures. Runtime products with a
    # positioning file are blocked earlier when dedicated image copy is absent.
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
        text = next((value for value in normalized if value not in used), None)
    text = compact_image_phrase(text)
    if not text:
        text = compact_image_phrase(copy.get("short_title") or copy.get("title_ru")) or "ОСНОВНЫЕ ПРЕИМУЩЕСТВА"
    if text:
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
            return f"products/{product_id}/output/generated-images/variant-main/{safe_sku}.png"
        return f"products/{product_id}/output/generated-images/variant-main/main-{counters['main']:03d}.png"
    if image_type == "disclaimer":
        counters["disclaimer"] += 1
        return f"products/{product_id}/output/generated-images/detail/detail-{counters['disclaimer']:03d}.png"
    counters["detail"] += 1
    return f"products/{product_id}/output/generated-images/detail/detail-{counters['detail']:03d}.png"


def variant_main_specs(
    product_dir: Path,
    source: Dict[str, Any],
    preflight: Dict[str, Any] | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Plan one SKU-specific main for supported color, size, or quantity differences."""
    skus = sorted(
        [item for item in source.get("skus") or [] if not item.get("excluded")],
        key=lambda item: int(item.get("selection_order") or 9999),
    )
    decision_path = product_dir / "output/variant-decision.json"
    decision = load_json(decision_path) if decision_path.is_file() else {}
    supported_kinds = {"color", "size_or_measurement", "configuration"}
    differences = [
        item for item in decision.get("detected_difference_fields") or []
        if item.get("difference_kind") in supported_kinds
    ]
    detected_differences = list(decision.get("detected_difference_fields") or [])
    fallback_seller_specs = bool(skus) and (not differences or bool(detected_differences))
    if not 1 <= len(skus) <= 10:
        raise ValueError("selected SKU count must be between 1 and 10")

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
            "reference_ready": check.get("status", "ready") in {"ready", "ready_with_warning"},
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
    validate_formal_product_input(product_dir)
    structures = load_json(STRUCTURES_PATH)
    pipeline_settings = load_json(ROOT / "config/pipeline-settings.json")
    product_id = product_dir.name
    design_path = product_dir / "output" / "ozon-ecommerce-design.json"
    if not design_path.is_file():
        raise ValueError("ozon-ecommerce-design.json is required; local image-planning fallback is disabled")
    design = load_json(design_path)
    design_errors = validate_design(product_dir, design)
    if design_errors:
        raise ValueError("invalid unified ecommerce design: " + "; ".join(design_errors))
    image_type_by_layout = {
        "sku_main": "main",
        "core_benefit": "benefit",
        "structure_callout": "detail",
        "usage_scene": "scene",
        "sku_comparison": "comparison",
        "purchase_notice": "disclaimer",
    }
    expected_structure = ["main"] + [
        image_type_by_layout[str(item["layout_type"])]
        for item in design["detail_images"]
    ]
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
    reference_ids_by_path = {str(item["path"]): str(item["id"]) for item in references}
    counters = {"main": 0, "detail": 0, "disclaimer": 0}
    planned = {"main_images": [], "detail_images": [], "disclaimer_images": []}
    main_specs, variant_image_strategy = variant_main_specs(product_dir, source, preflight)
    dimension_annotation = product_dimension_annotation(product_dir)
    copy_path = product_dir / "output" / "copy-ru.json"
    copy = load_json(copy_path) if copy_path.is_file() else {}
    preference_path = product_dir / "input" / "visual-preference.json"
    visual_preference = load_json(preference_path) if preference_path.is_file() else {}
    set_hint = str(visual_preference.get("set_hint") or "").strip()
    slot_hints = visual_preference.get("slot_hints") if isinstance(visual_preference.get("slot_hints"), dict) else {}
    used_russian_text: set[str] = set()
    listing_context = final_listing_context(product_dir, source)
    listing_title = _image_listing_title(listing_context, listing_context)

    # The unified connected-Codex design is the only source of commercial
    # decisions and prompts.  The brief is a compatibility projection only.
    brief_path = product_dir / "output" / "ecommerce-creative-brief.json"
    if not brief_path.is_file():
        raise ValueError("ecommerce-creative-brief.json must be materialized from the unified design")
    creative_brief = load_json(brief_path)
    brief_roles = list(design.get("detail_images") or [])
    design_mains = {str(item["sku_id"]): item for item in design.get("main_images") or []}

    # The connected ecommerce designer is the only creative decision-maker.
    # style-profile remains a compatibility classification and must never
    # replace the product-specific visual system with a category default.
    creative = design["visual_system"]

    detail_role_index = 0
    for image_type in expected_structure:
        requested_image_type = image_type
        fallback_reason = "unknown"
        brief_role = None
        if image_type != "main":
            brief_role = brief_roles[detail_role_index]
            detail_role_index += 1
            requested_image_type = str(brief_role.get("image_type") or image_type)
            # The structure keeps the commercial slot names while the brief
            # can route an unavailable comparison/size slot to evidence detail.
            image_type = requested_image_type
        if image_type == "comparison" and len(source.get("skus", [])) < 2:
            image_type = "detail"
            fallback_reason = "单SKU没有真实对比对象，改为第二张真实结构展示。"
        contract = structures["slot_contracts"][image_type]
        repetitions = main_specs if image_type == "main" and main_specs else [None]
        for main_spec in repetitions:
            source_sku_id = main_spec["source_sku_id"] if main_spec else None
            role_design = design_mains.get(str(source_sku_id)) if main_spec else brief_role
            if not role_design:
                raise ValueError(f"unified ecommerce design has no image role for {source_sku_id or image_type}")
            art_direction = role_design.get("art_direction") or {}
            overlay_plan = role_design.get("overlay_plan") or []
            role_prompt = str(role_design.get("prompt") or "").strip()
            if not art_direction or not overlay_plan or not role_prompt:
                raise ValueError(
                    f"unified ecommerce design has incomplete art direction for {role_design.get('slot') or image_type}; "
                    "fixed-template fallback is forbidden"
                )
            # A disclaimer is a commercial detail role, not a ninth image.
            # Keep it in detail_images so the upload gate always sees exactly
            # eight shared details, while preserving its semantic image_type.
            path_image_type = "detail" if image_type == "disclaimer" else image_type
            path = output_path(product_id, path_image_type, counters, source_sku_id)
            if image_type == "main":
                collection = "main_images"
                slot = f"main-{source_sku_id}" if source_sku_id else f"main-{counters['main']:03d}"
            elif image_type == "disclaimer":
                collection = "detail_images"
                slot = f"detail-{counters['detail']:03d}"
            else:
                collection = "detail_images"
                slot = f"detail-{counters['detail']:03d}"

            variant_reference = [main_spec["reference_path"]] if main_spec and main_spec["reference_path"] != "unknown" else []
            brief_reference_paths = [
                str(value)
                for value in (role_design.get("source_references") or [])
                if str(value).strip()
            ]
            for value in brief_reference_paths:
                validate_product_reference(product_dir, value)
            item_reference_paths = (
                brief_reference_paths if main_spec
                else brief_reference_paths[:5] if brief_reference_paths
                else reference_paths
            )
            item_reference_ids = (
                [f"sku-{source_sku_id}"] if variant_reference
                else [reference_ids_by_path.get(path, Path(path).stem) for path in item_reference_paths]
                if brief_reference_paths
                else reference_ids
            )
            brief_dimension_text = bool(
                image_type == "size_spec"
                and role_design.get("russian_text")
            )
            missing_dimensions = (
                image_type == "size_spec"
                and dimension_annotation is None
                and not brief_dimension_text
            )
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
            if status != "needs_review":
                operation = str(role_design.get("operation") or operation)
            variant_prompt = (
                f"This exact SKU is {main_spec['source_sku_id']} / {main_spec['variant_value']}."
                if main_spec else
                f"This detail role is grounded only in these exact designer references: {', '.join(item_reference_paths)}. Show only the SKU and facts named by this role; do not blend structures or dimensions from other references."
                if brief_reference_paths else
                "This is shared across selected SKUs; show only facts and product features common to every selected SKU."
            )
            current_sku = next(
                (item for item in source.get("skus") or [] if str(item.get("sku_id")) == str(source_sku_id)),
                None,
            )
            listing_facts = _sku_prompt_facts(listing_context, current_sku if main_spec else None)

            russian_text = (
                list(role_design.get("russian_text") or [])
                if role_design.get("russian_text")
                else russian_image_text(image_type, copy, dimension_annotation, source_sku_id, used_russian_text)
            )
            if image_type == "size_spec" and dimension_annotation and not brief_dimension_text:
                russian_text = [dimension_annotation["label_ru"]]
            item = {
            "type": "main" if image_type == "main" else "disclaimer" if image_type == "disclaimer" else "detail",
            "slot": slot,
            "image_type": image_type,
            "requested_image_type": requested_image_type,
            "fallback_reason": fallback_reason,
            "layout_type": str(role_design.get("layout_type") or "unknown"),
            "overlay_modules": list(role_design.get("overlay_modules") or []),
            "design_rationale": str(role_design.get("design_rationale") or ""),
            "art_direction": art_direction,
            "overlay_plan": overlay_plan,
            "purchase_reason": str(role_design.get("commercial_purpose") or "").strip() or purchase_reason_for(image_type, positioning, objections),
            "visual_goal": str(art_direction.get("concept") or ""),
            "scene_description": str(art_direction.get("scene") or ""),
            "style_direction": "；".join([
                " / ".join(str(value) for value in art_direction.get("palette") or []),
                str(art_direction.get("lighting") or ""),
                str(art_direction.get("typography") or ""),
            ]),
            "purpose": str(role_design.get("commercial_purpose") or "").strip() or contract["selling_goal"],
            "buyer_question": str(role_design.get("buyer_question") or "").strip() or contract["buyer_question"],
            "selling_goal": contract["selling_goal"],
            "scene": str(art_direction.get("scene") or ""),
            "russian_text": russian_text,
            "visual_direction": "；".join([
                str(art_direction.get("composition") or ""),
                str(art_direction.get("value_signal") or ""),
                str(art_direction.get("slot_differentiation") or ""),
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
            "source_text_policy": "single_pass_scene_product_exact_russian_and_reject_all_chinese_text_or_seller_watermarks",
            "prompt": role_prompt,
            "prompt_brief": f"3:4 product-specific visual · {role_design.get('slot') or image_type} · {art_direction.get('concept')}",
            "output_path": path,
            "status": status,
            "failure_reason": failure_reason,
            }
            if image_type == "size_spec" and dimension_annotation and not brief_dimension_text:
                item["measurement_annotation"] = dimension_annotation
            planned[collection].append(item)

            # Planning must never route generated bytes into input or another
            # output bucket.  Candidate files have exactly one writable root.
            validate_generated_output(product_dir, path)

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
    source_refs.append(f"products/{product_id}/output/ecommerce-creative-brief.json")
    source_refs.extend(
        ref for ref in listing_context["source_refs"] if ref not in source_refs
    )
    if preference_path.is_file():
        source_refs.append(f"products/{product_id}/input/visual-preference.json")
    planned_reference_paths = list(dict.fromkeys(
        str(value)
        for collection in ("main_images", "detail_images", "disclaimer_images")
        for item in planned[collection]
        for value in item.get("reference_product_images") or []
    ))
    reference_by_path = {str(item.get("path")): item for item in references}
    plan_references = [
        reference_by_path.get(value) or {
            "id": Path(value).stem,
            "path": value,
            "role": "sku" if "/sku-images/" in value else "main" if "/main-images/" in value else "detail",
            "usable": True,
            "notes": "Unified ecommerce design source; output images are never eligible references.",
        }
        for value in planned_reference_paths
    ]
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "collection_id": str(source["collection_id"]),
        "source_kind": "workbench_collection",
        "source_refs": source_refs,
        "style_profile_ref": style_ref,
        "creative_brief_ref": f"products/{product_id}/output/ecommerce-creative-brief.json",
        "ecommerce_design_ref": f"products/{product_id}/output/ozon-ecommerce-design.json",
        "style_family": style_profile["style_family"],
        "image_structure_rule_ref": "dynamic:product-specific-buyer-decision-sequence",
        "image_set_structure": expected_structure,
        "variant_image_strategy": variant_image_strategy,
        "buyer_objections": objections,
        "listing_context": listing_context,
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
            "composition_tool": "built_in_image_editor_single_pass",
            "source_preflight_ref": f"products/{product_id}/output/image-source-preflight.json",
            "generation_strategy": "product_specific_visual_story",
            "deterministic_image_types": ["comparison", "size_spec", "detail", "disclaimer"],
            "ai_reference_edit_image_types": ["main", "benefit", "scene", "problem_solution", "feature", "detail", "usage"],
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
        "creative_direction": creative,
        "learned_image_preferences": style_profile.get("learned_image_preferences") or [],
        "reference_images": plan_references,
        "buyer_analysis": {
            "who_buys": [positioning["target_customer"]] if positioning.get("target_customer") not in (None, "unknown") else style_profile["target_user"],
            "why_buy": [positioning["purchase_motivation"]] if positioning.get("purchase_motivation") not in (None, "unknown") else style_profile["purchase_motivation"],
            "main_pain_point": next(
                (value for value in positioning.get("customer_pain_points", []) if value != "unknown"),
                "unknown",
            ),
            "strongest_selling_point": positioning.get("core_sales_angle", "unknown"),
            "selling_points": [item["text"] for item in positioning.get("buyer_selling_points", [])],
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
            "blank rounded rectangles, empty text boxes, placeholder cards or decorative empty frames",
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
        brief_path = product_dir / "output" / "ecommerce-creative-brief.json"
        if not brief_path.is_file():
            raise ValueError(
                "ecommerce-creative-brief.json must come from ozon-ecommerce-designer; "
                "deterministic local brief fallback is disabled"
            )
        output = product_dir / "output/image-plan.json"
        write_json_atomic(output, plan)
        print(output)
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
