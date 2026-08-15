#!/usr/bin/env python3
"""Build the current ecommerce-design image plan without calling any AI API."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    from scripts.ozon_ecommerce_designer_contract import validate_design
    from scripts.image_asset_boundaries import validate_generated_output, validate_product_reference
    from scripts.production_input_guard import validate_formal_product_input
except ModuleNotFoundError:  # Allows direct execution as scripts/image_planner.py.
    from ozon_ecommerce_designer_contract import validate_design
    from image_asset_boundaries import validate_generated_output, validate_product_reference
    from production_input_guard import validate_formal_product_input


ROOT = Path(__file__).resolve().parents[1]
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
TECHNICAL_ATTRIBUTE_NAMES = (
    "接口", "类型", "分辨率", "尺寸", "外形尺寸", "重量", "容量", "规格",
    "材质", "颜色", "型号", "货号", "质保",
)
PRIMARY_MAIN_TEXT_HINTS = (
    "usb", "2d", "hr", "интерфейс", "модель", "размер", "цвет", "комплект", "barcode",
)
LEGACY_TEMPLATE_PROMPT_REPLACEMENTS = (
    (r"\b(?:marketing|advertising)\s+poster\b", "Ozon ecommerce product image"),
    (r"\bhuge\s+headline\b", "readable source-backed heading"),
    (r"\bcapacity\s+badge\b", "source-backed capacity text"),
    (r"\b(?:blue\s+)?(?:cta|pill|chip|badge)\b", "subtle source-backed proof annotation"),
    (r"\b(?:ui|app|cta|blue)\s+button\b", "subtle source-backed proof annotation"),
    (r"\b(?:floating|decorative)\s+(?:banner|label|sticker|tag)\b", "integrated product proof annotation"),
    (r"\bthree[- ]card\s+benefit\s+row\b", "compact source-backed benefit notes"),
    (r"\bbenefit\s+cards?\b", "compact source-backed benefit notes"),
    (r"рекламн\w*\s+плакат\w*", "товарное изображение для Ozon"),
    (r"模板海报|海报模板", ""),
)


LEGACY_OVERLAY_MODULES = {
    "product_name",
    "callout_arrows",
    "dimension_lines",
    "sku_labels",
    "benefit_cards",
    "left_headline",
    "badge_row",
    "side_panel",
    "poster_header",
}


def clean_overlay_modules(values: Any) -> List[str]:
    modules = [
        str(value).strip()
        for value in (values or [])
        if str(value).strip() in {"product_name", "purchase_notice"}
    ]
    for value in ("product_name", "purchase_notice"):
        if value not in modules:
            modules.append(value)
    return list(dict.fromkeys(modules))


def load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


SLOT_CONTRACTS = {
    "main": {
        "selling_goal": "让买家在三秒内看懂商品、SKU规格和点击理由。",
        "buyer_question": "这是什么商品、这个SKU适合我吗？",
    },
    "benefit": {
        "selling_goal": "把一个真实核心卖点转成购买理由。",
        "buyer_question": "它解决我什么问题？",
    },
    "detail": {
        "selling_goal": "用真实结构和细节建立信任。",
        "buyer_question": "它的结构、做工或使用方式可靠吗？",
    },
    "scene": {
        "selling_goal": "让买家代入真实使用场景。",
        "buyer_question": "我会在哪里、怎样使用它？",
    },
    "comparison": {
        "selling_goal": "帮助买家按当前已选SKU差异做选择。",
        "buyer_question": "不同SKU之间怎么选？",
    },
    "disclaimer": {
        "selling_goal": "说明购买前必须知道的限制，减少误购和退货。",
        "buyer_question": "下单前还有什么需要确认？",
    },
}


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


def preflight_reference_replacements(preflight: Dict[str, Any]) -> Dict[str, str]:
    replacements: Dict[str, str] = {}
    for item in preflight.get("sku_references") or []:
        status = str(item.get("status") or "").strip()
        original = str(item.get("original_reference_path") or "").strip()
        preferred = str(item.get("preferred_reference_path") or "").strip()
        if status in {"ready", "ready_with_warning"} and original and preferred and original != preferred:
            replacements[original] = preferred
    return replacements


def apply_preflight_reference_replacements(paths: List[str], replacements: Dict[str, str]) -> List[str]:
    result: List[str] = []
    for value in paths:
        path = str(value).strip()
        if not path:
            continue
        path = replacements.get(path, path)
        if path not in result:
            result.append(path)
    return result


def apply_preflight_reference_replacements_to_text(text: str, replacements: Dict[str, str]) -> str:
    result = str(text or "")
    for original, preferred in replacements.items():
        result = result.replace(original, preferred)
    return result


def required_shared_detail_role_slots(roles: List[Dict[str, Any]]) -> Dict[str, str]:
    slots = [str(role.get("slot") or "") for role in roles if str(role.get("slot") or "")]

    def role_text(role: Dict[str, Any]) -> str:
        art = role.get("art_direction") if isinstance(role.get("art_direction"), dict) else {}
        semantic = {
            "slot": role.get("slot"),
            "image_type": role.get("image_type"),
            "layout_type": role.get("layout_type"),
            "design_rationale": role.get("design_rationale"),
            "commercial_purpose": role.get("commercial_purpose"),
            "buyer_question": role.get("buyer_question"),
            "russian_text": role.get("russian_text"),
            "art_direction": {
                key: art.get(key)
                for key in (
                    "concept", "scene", "composition", "product_position",
                    "information_hierarchy", "slot_differentiation",
                )
            },
        }
        return json.dumps(semantic, ensure_ascii=False).casefold()

    parameter_tokens = (
        "size_spec", "parameter", "specification", "dimension", "measurement", "размер",
        "габарит", "параметр", "характерист", "измер", "尺寸", "参数", "规格",
    )
    model_tokens = (
        "visible person", "human model", "human", "adult model", "russian model", "visible model",
        "lifestyle model", "модель", "человек", "пользователь", "русская модель",
        "российская модель", "真人", "模特",
    )

    parameter_slot = next(
        (
            str(role.get("slot") or "")
            for role in roles
            if str(role.get("layout_type") or "") == "structure_callout"
        ),
        "",
    )
    if not parameter_slot:
        parameter_slot = next(
            (
                str(role.get("slot") or "")
                for role in roles
                if any(token in role_text(role) for token in parameter_tokens)
            ),
            "",
        )

    explicit_model_slot = next(
        (
            str(role.get("slot") or "")
            for role in roles
            if any(token in role_text(role) for token in model_tokens)
        ),
        "",
    )
    notice_tokens = (
        "purchase_notice", "disclaimer", "notice", "проверьте", "перед покупкой",
        "комплектац", "крепеж", "крепёж", "fit check", "buyer reminder", "购买提醒",
    )
    explicit_notice_slot = next(
        (
            str(role.get("slot") or "")
            for role in roles
            if str(role.get("layout_type") or "") == "purchase_notice"
            or any(token in role_text(role) for token in notice_tokens)
        ),
        "",
    )

    return {
        "parameter": parameter_slot,
        "model": explicit_model_slot,
        "final_disclaimer": explicit_notice_slot,
    }


def excluded_reference_paths(product_dir: Path) -> set:
    """Product-level reference exclusions.

    Some supplier galleries mix images of different product variants (for
    example a clamp-style holder promo inside a magnetic-mount listing). The
    operator can list those images in
    `products/<id>/output/image-reference-exclusions.json` so the planner never
    uses them as product references for any slot. Accepts full paths or
    input-image ids like `main-004`. Returns absolute resolved paths.
    Empty/missing file means no exclusions.
    """
    excluded: set = set()
    exclusions_path = product_dir / "output" / "image-reference-exclusions.json"
    if not exclusions_path.is_file():
        return excluded
    try:
        data = json.loads(exclusions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return excluded
    for value in (data.get("excluded") or []):
        text = str(value or "").strip()
        if not text:
            continue
        if "/" in text or text.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            excluded.add(str(Path(text).resolve()))
            continue
        for collection in ("main-images", "detail-images", "sku-images"):
            for candidate in sorted((product_dir / "input" / collection).glob(f"{text}.*")):
                excluded.add(str(candidate.resolve()))
    return excluded


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
        path = str(
            check.get("preferred_reference_path")
            or sku.get("local_image_path")
            or sku.get("image_path")
            or sku.get("sku_image_path")
            or sku.get("image_local_path")
            or "unknown"
        )
        reference_override = check.get("reference_override") if isinstance(check.get("reference_override"), dict) else None
        if (sku.get("sku_image_missing") and not reference_override) or path == "unknown":
            continue
        if reference_override and reference_override.get("decision") == "user_bound_reference_image":
            notes = "用户绑定本商品采集图作为SKU参考图；仅共用视觉参考，不继承主图文字或其他SKU事实。"
        elif reference_override and reference_override.get("decision") == "auto_single_sku_gallery_reference":
            notes = "单SKU自动匹配本商品高清主图/详情图作为SKU视觉参考；不继承其他SKU事实。"
        elif reference_override:
            notes = (
                f"人工确认同外观SKU {reference_override.get('source_sku_id')} 的真实图片；"
                "仅共用图片，不继承源SKU规格、价格或文案。"
            )
        elif check.get("status", "ready") == "ready":
            notes = f"仅关联真实SKU {sku.get('sku_id', 'unknown')}；生图前清晰度检查通过。"
        else:
            notes = str(check.get("reason") or "SKU参考图可用于参考编辑，但清晰度低于推荐值。")
        ready = check.get("status", "ready") in {"ready", "ready_with_warning"}
        references.append({
            "id": f"sku-{index:03d}",
            "path": path,
            "role": "sku",
            "usable": ready,
            "notes": notes,
        })
    return references


def diversify_detail_reference_paths(
    base_paths: List[str],
    references: List[Dict[str, Any]],
    *,
    seed: int,
    limit: int = 5,
) -> List[str]:
    """Add current-product gallery/detail and SKU references without forcing a copy."""
    result = list(dict.fromkeys(str(value) for value in base_paths if str(value).strip()))
    if len(result) >= limit:
        return result[:limit]
    current_product_gallery = [
        str(item.get("path") or "")
        for item in references
        if item.get("usable") and item.get("role") in {"main", "detail"} and str(item.get("path") or "")
    ]
    current_product_gallery = list(dict.fromkeys(current_product_gallery))
    if current_product_gallery:
        rotated = current_product_gallery[seed % len(current_product_gallery):] + current_product_gallery[:seed % len(current_product_gallery)]
        for path in rotated:
            if path not in result:
                result.append(path)
            if len(result) >= limit:
                return result[:limit]
    sku_identity_refs = [
        str(item.get("path") or "")
        for item in references
        if item.get("usable") and item.get("role") == "sku" and str(item.get("path") or "")
    ]
    for path in list(dict.fromkeys(sku_identity_refs)):
        if path not in result:
            result.append(path)
        if len(result) >= limit:
            break
    return result


def speed_limited_generation_reference_paths(
    paths: List[str],
    *,
    is_main: bool,
    exact_evidence: bool,
) -> List[str]:
    """Keep generation requests light while preserving fact-critical evidence.

    Comparison and size/specification images may need more evidence images. Normal
    main/detail images should not send the whole gallery because it slows calls and
    increases the chance of visual blending.
    """
    unique = list(dict.fromkeys(str(value) for value in paths if str(value).strip()))
    if exact_evidence:
        return unique[:5]
    if is_main:
        sku_refs = [path for path in unique if "/input/sku-images/" in path]
        if sku_refs:
            ordered = [sku_refs[0], *[path for path in unique if path != sku_refs[0]]]
        else:
            ordered = unique
        return list(dict.fromkeys(ordered))[:3]
    return unique[:3]


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


def visual_fact_anchor_for(context: Dict[str, Any]) -> str:
    """Add light product-type anchors before generation, without creating a QC gate."""
    text = json.dumps({
        "title_ru": context.get("title_ru"),
        "title": context.get("title"),
        "sku_variants": context.get("sku_variants") or [],
        "attributes": context.get("attributes") or [],
    }, ensure_ascii=False).casefold()
    if any(token in text for token in (
        "сканер", "scanner", "barcode", "штрих", "qr", "qr-код",
        "扫码", "扫码枪", "条码", "二维码",
    )):
        return (
            "Barcode scanner anchor: preserve the reference scanner silhouette, head shape, handle angle, control area, "
            "color accents and proportions. Show a plausible barcode or QR label and a real scanning use proof."
        )
    return ""


def _clean_strategy_text(value: Any, fallback: str = "product-specific Ozon visual sales strategy") -> str:
    text = str(value or "").strip()
    if not text or text.casefold() == "unknown":
        return fallback
    if CJK_PATTERN.search(text):
        return fallback
    return re.sub(r"\s+", " ", text)


def _join_strategy_parts(values: List[Any], fallback: str) -> str:
    parts = []
    for value in values:
        if isinstance(value, list):
            rendered = " / ".join(_clean_strategy_text(item, "") for item in value if _clean_strategy_text(item, ""))
        elif isinstance(value, dict):
            rendered = " / ".join(
                f"{_clean_strategy_text(key, '')}: {_clean_strategy_text(val, '')}"
                for key, val in value.items()
                if _clean_strategy_text(val, "")
            )
        else:
            rendered = _clean_strategy_text(value, "")
        if rendered and rendered.casefold() != "unknown":
            parts.append(rendered)
    return "；".join(dict.fromkeys(parts)) or fallback


def _compact_execution_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0].rstrip("；;, ")
    return f"{shortened}."


def _dimension_component_texts(lines: List[str]) -> List[str]:
    """Allow size diagrams to split one combined source-backed dimension label."""
    allowed: List[str] = []
    seen: set[str] = set()
    for value in lines:
        text = str(value or "").strip()
        if not text or not re.search(r"\d+\s*[×xх]\s*\d+", text, re.IGNORECASE):
            continue
        unit_match = re.search(r"\b(мм|см|м|г|кг|л|ml|g|kg|mm|cm)\b", text, re.IGNORECASE)
        unit = unit_match.group(1) if unit_match else ""
        for number in re.findall(r"\d+(?:[.,]\d+)?", text):
            label = f"{number} {unit}".strip()
            if label and label not in seen:
                seen.add(label)
                allowed.append(label)
    return allowed


def _source_visual_evidence(product_dir: Path, source: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Compile lightweight visual/field evidence from existing deterministic inputs.

    This is not a new AI gate. It gives downstream steps a concise split between
    selling proof for images and factual parameters for Ozon fields.
    """
    title = str(source.get("title_cn") or (analysis.get("facts") or {}).get("title_cn") or "").strip()
    attributes: List[Dict[str, Any]] = []
    for item in source.get("product_attributes") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name_cn") or item.get("name") or "").strip()
        value = str(item.get("value_cn") or item.get("value") or "").strip()
        if not name or not value or value.casefold() == "unknown":
            continue
        attributes.append({
            "name_cn": name,
            "value_cn": value,
            "source_ref": "input/source.json.product_attributes",
        })
    visual_proofs: List[Dict[str, str]] = []
    field_parameters: List[Dict[str, str]] = []
    for item in attributes:
        name = item["name_cn"]
        value = item["value_cn"]
        target = field_parameters if any(token in name for token in TECHNICAL_ATTRIBUTE_NAMES) else visual_proofs
        target.append({
            "name": name,
            "value": value,
            "source_ref": item["source_ref"],
        })
    if title:
        visual_proofs.insert(0, {
            "name": "商品标题",
            "value": title,
            "source_ref": "input/source.json.title_cn",
        })
    sku_identity = []
    for sku in source.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        sku_identity.append({
            "sku_id": str(sku.get("sku_id") or ""),
            "sku_name": str(sku.get("sku_name") or ""),
            "option_values": [
                f"{item.get('name_cn') or item.get('name') or '规格'}={item.get('value_cn') or item.get('value')}"
                for item in (sku.get("option_values") or [])
                if isinstance(item, dict) and (item.get("value_cn") or item.get("value"))
            ],
            "source_ref": "input/source.json.skus",
        })
    summary = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "source_kind": "workbench_collection",
        "purpose": "Split existing source facts into image-selling evidence and Ozon field evidence; no new AI claims.",
        "image_selling_evidence": visual_proofs[:12],
        "field_parameter_evidence": field_parameters[:20],
        "sku_identity_evidence": sku_identity[:10],
        "rule": (
            "Image prompts use image_selling_evidence as proof/story material; "
            "Ozon fields use field_parameter_evidence through deterministic compilation. "
            "Detail images may use field_parameter_evidence for specification, size, structure and comparison proof. "
            "Main images must not turn the SKU/title translation into the visual subject."
        ),
    }
    path = product_dir / "output" / "visual-evidence-summary.json"
    write_json_atomic(path, summary)
    return summary


def _line_is_useful_main_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.casefold()
    if text == "JLC GLOBAL":
        return True
    if any(token in lowered for token in ("модель", "model", "sku", "артикул")):
        return False
    if _looks_like_poster_title(text):
        return False
    # Main images should not become title cards. Keep only short fact/proof
    # snippets such as USB, 2D, color, size, capacity or a concrete use case.
    if len(text) > 26:
        return False
    return any(
        token in lowered
        for token in (
            "usb", "2d", "1d", "qr", "bluetooth", "wifi", "см", "мм", "мл", "л",
            "цвет", "размер", "объем", "объём", "комплект", "касс", "логист",
            "склад", "кухн", "ванн", "стол", "настен", "черн", "бел", "сер",
        )
    )


def _visual_strategy_needs_model(design: Dict[str, Any]) -> bool:
    """Infer whether a real-person scene is commercially useful, without blocking generation."""
    model_tokens = (
        "visible person", "human", "human model", "adult model", "russian model", "lifestyle model",
        "человек", "люди", "модель", "русская модель", "российская модель", "пользователь",
        "真人", "模特",
    )
    # Only concrete per-slot direction should trigger a people/model scene.
    # Generic visual-system language is guidance, not a forced image role.
    role_text = json.dumps({
        "main_images": design.get("main_images") or [],
        "detail_images": design.get("detail_images") or [],
    }, ensure_ascii=False).casefold()
    return any(token in role_text for token in model_tokens)


def apply_jlc_watermark_policy(text: str) -> str:
    """Require our own brand mark while still rejecting source/seller marks."""
    value = str(text or "")
    replacements = {
        "водяные знаки или лишние аксессуары": (
            "seller/1688 watermarks or extra accessories; add one subtle JLC GLOBAL corner watermark"
        ),
        "водяные знаки": "seller/1688 watermarks",
        "seller watermark": "seller/1688 watermark",
        "seller watermarks": "seller/1688 watermarks",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    if "JLC GLOBAL" not in value:
        value = (
            f"{value}\n\nBrand ownership: include one small, unobtrusive JLC GLOBAL corner watermark; "
            "do not include workbench product-id badges such as P000xxx."
        ).strip()
    return value


def _feature_macro_targets_from_role(role: Dict[str, Any], listing_facts: str = "") -> str:
    text = json.dumps({
        "russian_text": role.get("russian_text") or [],
        "art_direction": role.get("art_direction") or {},
        "prompt": role.get("prompt") or "",
    }, ensure_ascii=False).casefold()
    facts_text = str(listing_facts or "").casefold()
    is_jewelry_like = any(
        token in facts_text or token in text
        for token in (
            "украшен", "бижутер", "серьг", "кольц", "браслет", "подвес",
            "камн", "кристалл", "страз", "металл", "серебр", "золот",
            "饰品", "首饰", "耳环", "戒指", "项链", "手链",
        )
    )
    targets: List[str] = []
    checks: List[tuple[tuple[str, ...], str]] = [
        (("текстур", "фактур", "texture", "质感"), "visible surface texture"),
        (("прозрач", "transparent", "透明"), "transparent wall/edge clarity"),
        (("угол", "corner", "rounded corner", "圆角"), "rounded corner and edge shape"),
        (("крыш", "lid", "盖"), "visible lid fit and closing edge"),
    ]
    if is_jewelry_like:
        checks.extend([
            (("бусин", "珠", "bead"), "verified colored/pearl-like beads only as visible bead details"),
            (("страз", "rhinestone", "水钻"), "visible rhinestones / transparent crystal border"),
            (("рамк", "контур", "золот", "metal", "металл", "медь", "金", "铜"), "metal frame, plating or edge texture"),
            (("камн", "stone", "кристалл"), "visible stone/crystal setting"),
            (("застеж", "застёж", "булав", "pin", "别针", "扣"), "fastener/pin only if visible in source reference"),
        ])
    for tokens, label in checks:
        if any(token in text for token in tokens):
            targets.append(label)
    return "; ".join(dict.fromkeys(targets))


def build_image_strategy_enhancement(
    design: Dict[str, Any],
    positioning: Dict[str, Any],
    listing_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Derive lightweight image-only strategy from the existing ecommerce design.

    This function must not create listing copy, tags, attributes, or upload data.
    It only sharpens how the already-approved image prompts should sell visually.
    """
    visual_system = design.get("visual_system") or {}
    buyer_profile = design.get("buyer_profile") or {}
    ecommerce_strategy = design.get("ecommerce_strategy") or {}
    title = _clean_strategy_text(listing_context.get("title_ru"), "")
    sku_summary = _join_strategy_parts(
        [item.get("name") for item in listing_context.get("sku_variants") or []],
        "selected SKU variants",
    )
    image_positioning = _join_strategy_parts(
        [
            positioning.get("market_positioning"),
            positioning.get("core_sales_angle"),
            positioning.get("purchase_motivation"),
            buyer_profile.get("purchase_motivation"),
            ecommerce_strategy.get("visual_positioning"),
            title,
            sku_summary,
        ],
        "Show the exact product as an Ozon-ready purchase decision, not a generic product display.",
    )
    main_image_goal = _join_strategy_parts(
        [
            SLOT_CONTRACTS["main"]["selling_goal"],
            positioning.get("core_sales_angle"),
            ecommerce_strategy.get("main_image_goal"),
            visual_system.get("main_image_goal"),
        ],
        "Make the buyer understand the product, SKU difference, and reason to click within three seconds.",
    )
    visual_style = _join_strategy_parts(
        [
            visual_system.get("style_name"),
            visual_system.get("style_summary"),
            visual_system.get("value_impression"),
            visual_system.get("palette_logic"),
            visual_system.get("scene_logic"),
            visual_system.get("typography_logic"),
            visual_system.get("consistency_rule"),
        ],
        "Product-specific Ozon ecommerce style with strong product presence and readable Russian information hierarchy.",
    )
    avoid_style = _join_strategy_parts(
        [
            visual_system.get("anti_template_rule"),
            ecommerce_strategy.get("avoid_style"),
            visual_system.get("forbidden_visual_signals"),
            "plain white catalog image",
            "generic template reused from another product",
            "low-contrast pale text",
            "Chinese source text, seller/1688 watermark, workbench product-id badges, empty placeholder boxes",
        ],
        "Avoid generic display-only images, fake facts, weak contrast, white catalog shots, and stale visual layouts.",
    )
    return {
        "image_positioning": image_positioning,
        "main_image_goal": main_image_goal,
        "visual_style": visual_style,
        "need_model": _visual_strategy_needs_model(design),
        "avoid_style": avoid_style,
        "visual_fact_anchor": visual_fact_anchor_for(listing_context),
    }


def load_visual_reference_analysis(product_dir: Path) -> Dict[str, Any]:
    """Load optional Ozon competitor real-photo guidance for image prompts.

    The artifact is advisory only.  It can tune camera language, lighting and
    composition, but it is never product evidence and must not alter facts.
    """
    path = product_dir / "output" / "visual-reference-analysis.json"
    if not path.is_file():
        return {}
    value = load_json(path)
    if str(value.get("product_id") or product_dir.name) != product_dir.name:
        return {}
    if str(value.get("source_kind") or "") not in {"ozon_reference_images", "competitor_reference_based"}:
        return {}
    return value


def _visual_reference_prompt_line(reference: Dict[str, Any]) -> str:
    if not reference:
        return ""
    style = reference.get("real_photo_style") if isinstance(reference.get("real_photo_style"), dict) else {}
    recipes = reference.get("shot_recipes") if isinstance(reference.get("shot_recipes"), list) else []
    negatives = reference.get("negative_style") if isinstance(reference.get("negative_style"), list) else []
    parts = [
        style.get("camera_feel"),
        style.get("lighting"),
        style.get("background"),
        style.get("depth_of_field"),
        style.get("texture"),
        style.get("imperfections"),
    ]
    recipe_parts: List[str] = []
    for recipe in recipes[:4]:
        if not isinstance(recipe, dict):
            continue
        recipe_parts.append(_join_strategy_parts([
            recipe.get("shot_type"),
            recipe.get("composition"),
            recipe.get("purpose"),
        ], ""))
    compact_parts = _compact_execution_text(_join_strategy_parts(parts, ""), 260)
    compact_recipes = _compact_execution_text("; ".join(recipe_parts), 220)
    compact_negative = _compact_execution_text("; ".join(str(item) for item in negatives[:8]), 180)
    if not any((compact_parts, compact_recipes, compact_negative)):
        return ""
    return (
        "- Ozon real-photo reference guidance: use only the competitor/reference images' camera language, "
        "seller-photo realism, lens distance, light, depth and background feel; never copy their watermark, "
        "store name, logo, brand, model, packaging, accessories, certifications or exact text; product facts "
        "still come only from the current product. "
        f"Photo feel: {compact_parts or 'real handheld marketplace photo'}; "
        f"shot recipes: {compact_recipes or 'vary close, macro, context and detail proof shots'}; "
        f"avoid reference artifacts: {compact_negative or 'AI-polished poster look, watermark and copied text'}.\n"
    )


def enhance_image_prompt_with_strategy(prompt: str, strategy: Dict[str, Any]) -> str:
    """Add one compact global contract; the designer's slot prompt owns the visuals."""
    base = str(prompt or "").strip()
    for marker in ("Mandatory Ozon ecommerce image execution:", "Image sales direction:"):
        if marker in base:
            base = base.split(marker, 1)[0].strip()
    if "Image sales strategy enhancement:" in base:
        base = base.split("Image sales strategy enhancement:", 1)[0].strip()
    for pattern, replacement in LEGACY_TEMPLATE_PROMPT_REPLACEMENTS:
        base = re.sub(pattern, replacement, base, flags=re.IGNORECASE)
    execution_tail = ""
    if "SKU execution:" in base:
        base, execution_tail = base.split("SKU execution:", 1)
        execution_tail = "SKU execution: " + execution_tail
    for marker in (
        "Product-specific photographic world:",
        "Set-level diversity execution for this slot:",
        "Reference image is an identity anchor only:",
    ):
        if marker in base:
            base = base.split(marker, 1)[0]
    # The slot brief owns the creative decision. Keep the designer's full
    # picture description intact — truncating it to a few hundred characters
    # was starving the image model of the scene, light, material and layout
    # detail it needs, which produced generic promo/render images.
    base = _compact_execution_text(base, 2000)
    execution_tail = _compact_execution_text(execution_tail, 500)
    base = "\n\n".join(part for part in (base, execution_tail) if part)
    role_guidance = []
    for role in strategy.get("required_slot_roles") or []:
        if role == "parameter":
            role_guidance.append("product parameter/specification image with source-backed values; mark estimates approximate")
        elif role == "model":
            role_guidance.append(
                "product-specific real-use or scale scene with a visible adult only if this slot's own design asks for it; "
                "keep the product primary and do not invent functions, accessories or included items"
            )
        elif role == "final_disclaimer":
            role_guidance.append("product-based purchase-risk reminder with verified limits or fit checks only when useful for this slot")
    feature = str(strategy.get("feature_macro_targets") or "").strip()
    if feature:
        role_guidance.append(f"use a true close-up of {feature}")
    if strategy.get("need_model") and not any("visible adult" in item for item in role_guidance):
        role_guidance.append(
            "Optional real-use scale scene: a visible adult may appear only when the slot's product evidence and buyer question support it; "
            "do not force a model into technical, comparison, parameter, macro or purchase-reminder images"
        )
    visible_lines = [
        _compact_execution_text(value, 80)
        for value in strategy.get("russian_text") or []
        if str(value or "").strip()
    ]
    dimension_component_lines = _dimension_component_texts(visible_lines)
    if "JLC GLOBAL" not in visible_lines:
        visible_lines.append("JLC GLOBAL")
    if visible_lines:
        non_brand_lines = [value for value in visible_lines if value != "JLC GLOBAL"]
        dimension_clause = (
            " Size diagrams may split the same combined dimension into these exact line labels: "
            + " | ".join(f'"{value}"' for value in dimension_component_lines)
            + "."
            if dimension_component_lines
            else ""
        )
        visible_contract = (
            "Text whitelist: only these Russian strings, once if used: "
            + " | ".join(f'"{value}"' for value in visible_lines)
            + "."
            + dimension_clause
            + (
                " Main SKU image text names the product (a short product/type name, "
                "the SKU difference and a compact benefit note) plus the watermark; "
                "do not render a full listing title as a huge block."
                if len(non_brand_lines) <= 4
                else " No other visible words."
            )
        )
    else:
        visible_contract = "Text whitelist: no visible text is required except one small JLC GLOBAL watermark; render no decorative words."
    global_contract = (
        "Fact lock: preserve exact product/SKU structure, color, proportions, size/capacity, quantity and confirmed accessories. "
        "Premium photo: believable optics, depth, soft shadows, material texture, reflections, real environment light and clean grading. "
        "Story: one buyer-use proof through action, scene, detail, size or SKU difference. "
        "Text: compact Russian notes attach to real proof; no large title block, empty text panel, template badge or poster text-pasting. "
        "Brand: one subtle JLC GLOBAL watermark; no workbench product-id badge."
    )
    visual_fact_anchor = str(strategy.get("visual_fact_anchor") or "").strip()
    contract = "Reference props are not included accessories unless confirmed; demos/callouts/icons are supporting refs only."
    if role_guidance:
        contract += " Slot requirement: " + "; ".join(role_guidance) + "."
    visual_reference = _visual_reference_prompt_line(strategy.get("visual_reference_analysis") or {}).strip()
    return apply_jlc_watermark_policy(
        "\n\n".join(part for part in (base, global_contract, visual_fact_anchor, visual_reference, visible_contract, contract) if part)
    )


def default_overlay_instruction(text: str, index: int, total: int, *, is_main: bool) -> Dict[str, Any]:
    roles = ["callout", "specification" if is_main else "benefit", "benefit", "specification", "callout", "notice"]
    role = roles[min(index, len(roles) - 1)]
    if is_main:
        boxes = (
            [0.075, 0.080, 0.30, 0.040],
            [0.075, 0.128, 0.30, 0.036],
            [0.695, 0.925, 0.22, 0.035],
            [0.585, 0.185, 0.28, 0.034],
            [0.585, 0.225, 0.28, 0.034],
            [0.095, 0.855, 0.34, 0.034],
        )
    else:
        boxes = (
            [0.08, 0.060, 0.84, 0.070],
            [0.10, 0.825, 0.80, 0.055],
            [0.08, 0.150, 0.38, 0.052],
            [0.54, 0.150, 0.38, 0.052],
            [0.08, 0.735, 0.38, 0.052],
            [0.54, 0.735, 0.38, 0.052],
        )
    x, y, width, height = boxes[min(index, len(boxes) - 1)]
    if total >= 5:
        height = min(height, 0.052)
    return {
        "role": role,
        "text": text,
        "box": [x, min(y, 0.86), width, height],
        "font_size_ratio": (0.024 if index == 0 else 0.020) if is_main else (0.045 if index == 0 else 0.029),
        "font_weight": "bold" if index == 0 else "regular",
        "text_color": "#F8FAFC" if is_main else "#111827",
        "accent_color": "#2563EB",
        "background_style": "translucent" if is_main else "none",
        "background_color": "#111827" if is_main else "#F8FAFC",
        "accent_style": "none",
        "align": "left",
        "vertical_align": "middle",
        "priority": index + 1,
    }


def jlc_watermark_overlay(priority: int = 99) -> Dict[str, Any]:
    return {
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
        "priority": priority,
    }


def with_jlc_watermark_overlay(overlay_plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = [
        dict(item)
        for item in overlay_plan
        if not (isinstance(item, dict) and str(item.get("text") or "").strip() == "JLC GLOBAL")
    ]
    result.append(jlc_watermark_overlay(len(result) + 1))
    return result[:7]


def _designer_overlay_plan_or_default(role_design: Dict[str, Any], russian_text: List[str], *, is_main: bool) -> List[Dict[str, Any]]:
    """Preserve visual-director typography instead of replacing it with a fixed fallback."""
    expected = [str(value).strip() for value in russian_text[:6] if str(value).strip()]
    overlay_plan = [
        value for value in (role_design.get("overlay_plan") or [])
        if isinstance(value, dict) and str(value.get("text") or "").strip()
    ]
    actual = [str(value.get("text") or "").strip() for value in overlay_plan]
    if overlay_plan and all(text in expected for text in actual):
        return with_jlc_watermark_overlay(overlay_plan[:6])
    return with_jlc_watermark_overlay([
        default_overlay_instruction(text, index, len(expected), is_main=is_main)
        for index, text in enumerate(expected)
    ])


def _looks_like_poster_title(text: str) -> bool:
    value = str(text or "").strip()
    if len(value) < 14:
        return False
    letters = [ch for ch in value if ch.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for ch in letters if ch.upper() == ch and ch.lower() != ch) / len(letters)
    broad_product_terms = (
        "душ", "держатель", "контейнер", "термос", "поднос", "органайзер",
        "сканер", "светильник", "бутыл", "сумк", "коврик", "полк", "набор",
    )
    return upper_ratio >= 0.65 or any(token in value.casefold() for token in broad_product_terms)


def compact_main_russian_text(lines: List[str]) -> List[str]:
    """Carry the designer's main-image text and add the watermark.

    Main images must name WHAT the product is — a short product/type name, the
    SKU difference and a compact benefit note — not be stripped to a lone spec
    snippet. Drop only model/SKU/article codes and empty lines.
    """
    result: List[str] = []
    for value in lines:
        text = str(value or "").strip()
        if not text or text == "JLC GLOBAL":
            continue
        lowered = text.casefold()
        if any(token in lowered for token in ("модель", "model", "артикул", "sku:")):
            continue
        if text not in result:
            result.append(text)
        if len(result) >= 4:
            break
    result.append("JLC GLOBAL")
    return result


def sanitize_main_role_prompt(prompt: str) -> str:
    """Keep the designer prompt while forbidding title-only poster execution."""
    value = str(prompt or "")
    value = re.sub(
        r"render\s+the\s+complete\s+final\s+Russian\s+typography\s+in\s+this\s+same\s+image-model\s+call",
        "render the exact Russian typography as integrated product information, not as a title-only poster block",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"Максимум\s+три\s+уровня\s+русского\s+гротеска;?\s*[^.。]*[.。]?",
        "Русский текст должен быть частью товарной инфографики: крупнее только там, где он объясняет реальный товар, SKU, пользу или доказательство.",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b(?:blue\s+)?(?:pill|chip|badge|cta)\b",
        "source-backed proof note",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b(?:ui|app|cta|blue)\s+button\b",
        "source-backed proof note",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s{2,}", " ", value)
    guard = (
        "Main image quality rule: make a premium real ecommerce product photograph first, not a title card. "
        "Use believable lens depth, material texture, soft shadows, reflections, scene light and clean color grading. "
        "Keep visible text to one or two compact proof notes plus the subtle JLC GLOBAL watermark; "
        "do not show the listing title/model as a large poster block. Text must attach to product proof, usage, SKU choice, structure or dimension."
    )
    return apply_jlc_watermark_policy("\n\n".join(part for part in (value.strip(), guard) if part))


def _spread_repeated_main_overlay(overlay_plan: List[Dict[str, Any]], variant_index: int) -> List[Dict[str, Any]]:
    """Avoid identical SKU-main text stacks when the designer repeats one layout."""
    if not overlay_plan:
        return overlay_plan
    presets = (
        ((0.075, 0.080), (0.075, 0.128), (0.695, 0.925), (0.585, 0.185), (0.585, 0.225), (0.095, 0.855)),
        ((0.590, 0.080), (0.590, 0.128), (0.695, 0.925), (0.090, 0.205), (0.565, 0.780), (0.090, 0.865)),
        ((0.085, 0.760), (0.085, 0.808), (0.695, 0.925), (0.555, 0.125), (0.555, 0.170), (0.085, 0.850)),
    )
    preset = presets[variant_index % len(presets)]
    result: List[Dict[str, Any]] = []
    for index, raw in enumerate(overlay_plan[:6]):
        item = dict(raw)
        if item.get("role") == "brand_watermark" or str(item.get("text") or "").strip() == "JLC GLOBAL":
            result.append(item)
            continue
        box = list(item.get("box") or [])
        width = float(box[2]) if len(box) == 4 else (0.30 if index == 0 else 0.28)
        height = float(box[3]) if len(box) == 4 else (0.040 if index == 0 else 0.036)
        x, y = preset[min(index, len(preset) - 1)]
        item["box"] = [round(x, 3), round(y, 3), round(min(width, 0.34), 3), round(min(height, 0.042), 3)]
        item["align"] = "left"
        item["accent_style"] = "none"
        item["background_style"] = "translucent"
        result.append(item)
    return result


def load_image_design_revision_request(product_dir: Path) -> Dict[str, Any]:
    path = product_dir / "output" / "image-design-revision-request.json"
    if not path.is_file():
        return {}
    value = load_json(path)
    if value.get("product_id") != product_dir.name:
        return {}
    return value


def revision_prompt_addendum(revision: Dict[str, Any], slot: str) -> str:
    failed_slots = {str(value) for value in revision.get("failed_slots") or []}
    if slot not in failed_slots:
        return ""
    issues = revision.get("slot_issues") or {}
    slot_issues = issues.get(slot) if isinstance(issues, dict) else []
    issue_texts = []
    for item in slot_issues or []:
        if not isinstance(item, dict):
            continue
        issue_texts.append(
            f"{item.get('code') or 'image_qc_failure'}: "
            f"{item.get('message') or item.get('severity') or 'hard failure'}"
        )
    if not issue_texts:
        issue_texts = [str(value) for value in revision.get("critical_failures") or []]
    issue_summary = _compact_execution_text(
        "; ".join(issue_texts) or str(revision.get("reason") or "image QC failure"),
        360,
    )
    return (
        "\n\nImage designer revision for this failed slot only:\n"
        f"- Previous QC failure: {issue_summary}.\n"
        "- Revise the camera task, composition, crop, reference use and typography treatment to remove this failure.\n"
        "- If the failure is unexpected_russian_text, treat it as a visible-text whitelist failure: remove every non-whitelisted visible word and keep only the exact slot russian_text strings.\n"
        "- Preserve the product fact lock: same product body, color, SKU difference, confirmed included accessories, parameters and Russian text facts; unconfirmed reference props are not hard accessories.\n"
        "- Keep passed image slots untouched; do not change title, description, tags, attributes, SKU count, image count or upload payload."
    )


def _variant_display_value(sku: Dict[str, Any]) -> str:
    """Return the seller's exact selected value for prompt grounding."""
    source_data = sku.get("source_data") or {}
    return str(
        source_data.get("sku_image_prop_value")
        or next((item.get("value_cn") for item in sku.get("option_values") or [] if item.get("value_cn")), None)
        or sku.get("sku_name")
        or "unknown"
    ).strip()


def _sku_identity_description(sku: Dict[str, Any]) -> str:
    """Compile one exact SKU identity from seller text and every selected option."""
    parts = [f"SKU title: {str(sku.get('sku_name') or sku.get('name') or 'unknown').strip()}"]
    for option in sku.get("option_values") or []:
        if not isinstance(option, dict):
            continue
        name = str(option.get("name_cn") or option.get("prop_name") or option.get("name") or "规格").strip()
        value = str(option.get("value_cn") or option.get("source_text") or option.get("value") or "").strip()
        if value:
            parts.append(f"{name}: {value}")
    return "; ".join(dict.fromkeys(parts))


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
        base.append(f"Точная идентичность выбранного SKU: {_sku_identity_description(sku)}")
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
        reference = str(
            check.get("preferred_reference_path")
            or sku.get("variant_local_image_path")
            or sku.get("local_image_path")
            or sku.get("image_path")
            or sku.get("sku_image_path")
            or sku.get("image_local_path")
            or "unknown"
        )
        specs.append({
            "source_sku_id": sku_id,
            "sku_name": str(sku.get("sku_name") or "unknown"),
            "variant_kind": kinds[0] if len(kinds) == 1 else "mixed_supported",
            "variant_value": " / ".join(values) if values else _variant_display_value(sku),
            "sku_identity": _sku_identity_description(sku),
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
    previous_contract_ignored: Dict[str, Any] | None = None,
    started_at: str | None = None,
) -> Dict[str, Any]:
    validate_formal_product_input(product_dir)
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
    preflight_path = product_dir / "output" / "image-source-preflight.json"
    preflight = load_json(preflight_path) if preflight_path.is_file() else {}
    reference_replacements = preflight_reference_replacements(preflight)
    references = usable_reference_images(source, preflight)
    excluded_references = excluded_reference_paths(product_dir)
    references = [
        item for item in references
        if str(Path(str(item.get("path") or "")).resolve()) not in excluded_references
    ]
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
    visual_evidence_summary = _source_visual_evidence(product_dir, source, analysis)
    listing_context["visual_evidence_summary"] = {
        "path": f"products/{product_id}/output/visual-evidence-summary.json",
        "image_selling_evidence": visual_evidence_summary.get("image_selling_evidence") or [],
        "field_parameter_evidence": visual_evidence_summary.get("field_parameter_evidence") or [],
        "sku_identity_evidence": visual_evidence_summary.get("sku_identity_evidence") or [],
    }
    listing_title = _image_listing_title(listing_context, listing_context)

    brief_roles = list(design.get("detail_images") or [])
    required_role_slots = required_shared_detail_role_slots(brief_roles)
    design_mains = {str(item["sku_id"]): item for item in design.get("main_images") or []}
    image_revision = load_image_design_revision_request(product_dir)

    creative = design["visual_system"]
    image_strategy = build_image_strategy_enhancement(design, positioning, listing_context)
    visual_reference_analysis = load_visual_reference_analysis(product_dir)
    if visual_reference_analysis:
        image_strategy["visual_reference_analysis"] = visual_reference_analysis
    model_scene_tokens = (
        "человек", "модель", "пользователь", "русская модель", "российская модель",
        "visible person", "adult russian", "russian model", "lifestyle model", "真人", "模特",
    )
    explicit_model_slots = {
        str(role.get("slot") or "")
        for role in (design.get("detail_images") or [])
        if any(
            token in str((role.get("art_direction") or {}).get("scene") or "").casefold()
            for token in model_scene_tokens
        )
    }
    model_slot_assigned = False

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
        contract = SLOT_CONTRACTS[image_type]
        repetitions = main_specs if image_type == "main" and main_specs else [None]
        for main_spec in repetitions:
            source_sku_id = main_spec["source_sku_id"] if main_spec else None
            role_design = design_mains.get(str(source_sku_id)) if main_spec else brief_role
            if not role_design:
                raise ValueError(f"unified ecommerce design has no image role for {source_sku_id or image_type}")
            art_direction = role_design.get("art_direction") or {}
            design_russian_text = [
                str(value).strip()
                for value in (role_design.get("russian_text") or [])
                if str(value).strip()
            ]
            if image_type == "main":
                design_russian_text = compact_main_russian_text(design_russian_text)
            overlay_plan = _designer_overlay_plan_or_default(
                role_design,
                design_russian_text,
                is_main=(image_type == "main"),
            )
            if image_type == "main" and main_spec is not None:
                overlay_plan = _spread_repeated_main_overlay(
                    overlay_plan,
                    len(planned["main_images"]),
                )
            role_prompt = str(role_design.get("prompt") or "").strip()
            role_prompt = apply_preflight_reference_replacements_to_text(
                role_prompt,
                reference_replacements,
            )
            if image_type == "main":
                role_prompt = sanitize_main_role_prompt(role_prompt)
            if not art_direction or not design_russian_text or not role_prompt:
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
            role_prompt = f"{role_prompt}{revision_prompt_addendum(image_revision, slot)}"

            variant_reference = [main_spec["reference_path"]] if main_spec and main_spec["reference_path"] != "unknown" else []
            brief_reference_paths = [
                str(value)
                for value in (role_design.get("source_references") or [])
                if str(value).strip()
            ]
            brief_reference_paths = apply_preflight_reference_replacements(
                brief_reference_paths,
                reference_replacements,
            )
            brief_reference_paths = [
                value for value in brief_reference_paths
                if str(Path(value).resolve()) not in excluded_references
            ]
            for value in brief_reference_paths:
                validate_product_reference(product_dir, value)
            item_reference_paths = (
                brief_reference_paths if main_spec
                else brief_reference_paths[:5] if brief_reference_paths
                else reference_paths
            )
            if main_spec:
                supplemental_refs = [
                    str(ref.get("path"))
                    for ref in references
                    if str(ref.get("path") or "").strip()
                    and str(ref.get("path")) not in set(item_reference_paths)
                    and str(ref.get("role") or "") in {"main", "detail"}
                ]
                # SKU reference stays first and locks the selected variant; current
                # product gallery/detail images add structure and usage context so
                # weak SKU-only references do not make the model invent details.
                item_reference_paths = list(dict.fromkeys([*item_reference_paths, *supplemental_refs]))
            layout_type = str(role_design.get("layout_type") or "unknown")
            exact_evidence_image = image_type in {"comparison", "size_spec"} or layout_type == "sku_comparison"
            if (
                main_spec is None
                and item_reference_paths
                and image_type not in {"comparison", "size_spec"}
                and layout_type != "sku_comparison"
            ):
                item_reference_paths = diversify_detail_reference_paths(
                    item_reference_paths,
                    references,
                    seed=counters["detail"] + detail_role_index,
                    limit=3,
                )
            item_reference_paths = speed_limited_generation_reference_paths(
                item_reference_paths,
                is_main=(image_type == "main"),
                exact_evidence=exact_evidence_image,
            )
            item_reference_ids = (
                [reference_ids_by_path.get(path, Path(path).stem) for path in item_reference_paths]
                if item_reference_paths
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
                else "generate_from_reference"
            )
            scene_text = str(art_direction.get("scene") or "").casefold()
            explicit_model_scene = any(
                token in scene_text
                for token in model_scene_tokens
            )
            role_slot = str(role_design.get("slot") or "")
            required_model_slot = str(required_role_slots.get("model") or "")
            model_eligible = (
                operation in {"edit_real_image", "generate_from_reference"}
                and main_spec is None
                and image_type not in {"comparison", "size_spec", "disclaimer"}
                and (
                    image_type in {"scene", "usage", "problem_solution", "benefit", "lifestyle", "detail"}
                    or explicit_model_scene
                )
            )
            strategy_model_requested_for_slot = bool(
                image_strategy["need_model"]
                and (
                    (required_model_slot and role_slot == required_model_slot)
                    or (
                        not required_model_slot
                        and (
                            not explicit_model_slots
                            or role_slot in explicit_model_slots
                        )
                    )
                )
            )
            slot_needs_model = bool(
                model_eligible
                and not model_slot_assigned
                and (strategy_model_requested_for_slot or explicit_model_scene)
            )
            if slot_needs_model:
                model_slot_assigned = True
            required_slot_roles = []
            if main_spec is None and role_slot == required_role_slots.get("parameter"):
                required_slot_roles.append("parameter")
            if main_spec is None and role_slot == required_role_slots.get("model"):
                required_slot_roles.append("model")
            if main_spec is None and role_slot == required_role_slots.get("final_disclaimer"):
                required_slot_roles.append("final_disclaimer")
            variant_prompt = (
                f"This exact SKU is {main_spec['source_sku_id']}. Identity: {main_spec['sku_identity']}. "
                f"Use only its own reference image {main_spec['reference_path']}; do not infer identity from image order or size alone."
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
            grounded_role_prompt = (
                f"{role_prompt}\n\nSKU execution: {variant_prompt}\nVerified visual facts: {listing_facts}"
            )

            russian_text = (
                list(role_design.get("russian_text") or [])
                if role_design.get("russian_text")
                else russian_image_text(image_type, copy, dimension_annotation, source_sku_id, used_russian_text)
            )
            if image_type == "main":
                russian_text = compact_main_russian_text(russian_text)
            if image_type == "size_spec" and dimension_annotation and not brief_dimension_text:
                russian_text = [dimension_annotation["label_ru"]]
            role_focus_text = json.dumps({
                "russian_text": russian_text,
                "art_direction": art_direction,
                "layout_type": layout_type,
            }, ensure_ascii=False).casefold()
            notice_slot = any(
                token in role_focus_text
                for token in (
                    "проверьте", "notice", "disclaimer",
                )
            )
            macro_requested = any(
                token in role_focus_text
                for token in ("macro", "close-up", "макро", "крупн", "фрагмент", "detail proof")
            )
            feature_macro_targets = (
                _feature_macro_targets_from_role(role_design, listing_facts)
                if (
                    main_spec is None
                    and not slot_needs_model
                    and not notice_slot
                    and (
                        layout_type == "structure_callout"
                        or macro_requested
                    )
                )
                else ""
            )
            if macro_requested and not feature_macro_targets and not notice_slot:
                feature_macro_targets = "source-backed detail proof area"
            slot_strategy = {
                **image_strategy,
                "is_main_image": main_spec is not None,
                "need_model": slot_needs_model,
                "required_slot_roles": required_slot_roles,
                "feature_macro_targets": feature_macro_targets,
                "russian_text": russian_text,
            }
            prompt_text = enhance_image_prompt_with_strategy(grounded_role_prompt, slot_strategy)
            if image_type == "main":
                prompt_text += (
                    "\n\nSKU main typography: do not repeat the same left-side text stack across SKU mains. "
                    "Place compact Russian text according to this SKU reference's product direction, natural negative space, "
                    "edge lines and scene depth; text must support the product proof and stay visually smaller than the product."
                )
            item = {
            "type": "main" if image_type == "main" else "disclaimer" if image_type == "disclaimer" else "detail",
            "slot": slot,
            "image_type": image_type,
            "image_positioning": slot_strategy["image_positioning"],
            "main_image_goal": slot_strategy["main_image_goal"],
            "visual_style": slot_strategy["visual_style"],
            "need_model": slot_strategy["need_model"],
            "avoid_style": slot_strategy["avoid_style"],
            "requested_image_type": requested_image_type,
            "fallback_reason": fallback_reason,
            "layout_type": layout_type,
            "overlay_modules": clean_overlay_modules(role_design.get("overlay_modules")),
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
            "sku_identity": main_spec["sku_identity"] if main_spec else "shared facts only",
            "operation": operation,
            "source_text_policy": "single_pass_scene_product_exact_russian_and_reject_all_chinese_text_or_seller_watermarks",
            "prompt": prompt_text,
            "prompt_brief": f"3:4 product-specific visual · {role_design.get('slot') or image_type} · {art_direction.get('concept')}",
            "output_path": path,
            "status": status,
            "failure_reason": failure_reason,
            }
            if slot in {str(value) for value in image_revision.get("failed_slots") or []}:
                item["image_design_revision"] = {
                    "source": "output/image-design-revision-request.json",
                    "reason": image_revision.get("reason") or "image QC failure",
                    "critical_failures": image_revision.get("critical_failures") or [],
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
    positioning_ref = f"products/{product_id}/output/product-positioning.json"
    source_refs = [
        f"products/{product_id}/input/source.json",
        f"products/{product_id}/output/product-analysis.json",
    ]
    if (product_dir / "output" / "product-positioning.json").is_file():
        source_refs.append(positioning_ref)
    source_refs.extend(
        ref for ref in listing_context["source_refs"] if ref not in source_refs
    )
    if preference_path.is_file():
        source_refs.append(f"products/{product_id}/input/visual-preference.json")
    if visual_reference_analysis:
        source_refs.append(f"products/{product_id}/output/visual-reference-analysis.json")
    source_refs.append(f"products/{product_id}/output/visual-evidence-summary.json")
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
        "ecommerce_design_ref": f"products/{product_id}/output/ozon-ecommerce-design.json",
        "visual_contract_ref": f"products/{product_id}/output/ozon-ecommerce-design.json#visual_system",
        "visual_family": str(creative.get("family") or creative.get("style_name") or "product_specific"),
        "image_positioning": image_strategy["image_positioning"],
        "main_image_goal": image_strategy["main_image_goal"],
        "visual_style": image_strategy["visual_style"],
        "need_model": image_strategy["need_model"],
        "avoid_style": image_strategy["avoid_style"],
        "image_sequence_ref": "dynamic:ozon-ecommerce-designer-buyer-decision-sequence",
        "image_set_structure": expected_structure,
        "variant_image_strategy": variant_image_strategy,
        "buyer_objections": objections,
        "listing_context": listing_context,
        "generator_contract": {
            "must_follow_ecommerce_design": True,
            "ecommerce_design_ref": f"products/{product_id}/output/ozon-ecommerce-design.json",
            "allowed_structure": expected_structure,
            "aspect_ratio": "3:4",
            "deviation_requires_review": False,
            "advisory_skills_required": [],
            "advisory_scope": "none",
            "project_rules_take_precedence": True,
            "image_slot_concurrency": max(1, min(int(pipeline_settings.get("image_slot_concurrency", 3)), 3)),
            "image_qc_same_execution": bool(pipeline_settings.get("merge_image_generation_and_qc", True)),
            "product_pixel_lock_required": False,
            "composition_tool": "built_in_image_editor_single_pass",
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
            "typography_strategy": "model_native_exact_russian_single_pass",
            "prompt_first_required": True,
            "empty_placeholder_panels_forbidden": True,
            "exact_shared_detail_count": 8,
            "ecommerce_design_required": True,
            "overlay_strategy": "single_pass_model_native_typography",
            "brand_watermark_required": "JLC GLOBAL small corner watermark",
        },
        "creative_direction": creative,
        "reference_images": plan_references,
        "buyer_analysis": {
            "who_buys": [positioning["target_customer"]] if positioning.get("target_customer") not in (None, "unknown") else [str((design.get("buyer_profile") or {}).get("target_buyer") or "unknown")],
            "why_buy": [positioning["purchase_motivation"]] if positioning.get("purchase_motivation") not in (None, "unknown") else [str((design.get("buyer_profile") or {}).get("purchase_motivation") or "unknown")],
            "main_pain_point": next(
                (value for value in positioning.get("customer_pain_points", []) if value != "unknown"),
                "unknown",
            ),
            "strongest_selling_point": positioning.get("core_sales_angle", "unknown"),
            "selling_points": [item["text"] for item in positioning.get("buyer_selling_points", [])],
            "proof_strategy": [
                positioning.get("recommended_visual_direction", "unknown"),
                str(creative.get("visual_positioning") or creative.get("style_summary") or "product-specific ecommerce story"),
            ],
        },
        **planned,
        "must_preserve": ["vertical 3:4 ratio", "product structure", "product colors", "SKU differences", "accessory count"],
        "must_not_change": ["material", "dimensions", "weight", "load capacity", "certifications", "brand", "functions", "package quantity", "accessories"],
        "forbidden_content": [
            "generic category template",
            "style copied from another product",
            "unverified claims",
            "unverified brand logos",
            "extra accessories",
            "category-derived filler contents such as rice, nuts, grains, snacks, feed or pet food without current-product evidence",
            "different product structure or color",
            "blank rounded rectangles, empty text boxes, placeholder cards or decorative empty frames that hide the product, make Russian text unreadable, or cause Ozon upload rejection",
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
    parser = argparse.ArgumentParser(description="Build the current ecommerce-design image plan.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--write", action="store_true", help="Write output/image-plan.json; default prints only")
    args = parser.parse_args()

    product_dir = Path(args.product_dir).resolve()
    plan = build_image_plan(
        product_dir,
        load_json(product_dir / "input/source.json"),
        load_json(product_dir / "output/product-analysis.json"),
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
