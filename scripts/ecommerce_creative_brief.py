#!/usr/bin/env python3
"""Legacy deterministic brief helper used only by isolated compatibility tests.

Formal production must use ``$ozon-ecommerce-designer`` and materialize its
single design artifact.  This module is not a production fallback and is never
invoked by ``image_planner.py``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from scripts.style_selector import ROOT, load_json, write_json_atomic
except ModuleNotFoundError:  # direct execution
    from style_selector import ROOT, load_json, write_json_atomic


DETAIL_ROLES = (
    ("core_benefit", "benefit", "核心卖点", "买家为什么值得买？", "edit_real_image"),
    ("structure_detail", "detail", "结构与细节", "它的结构和做工是什么样？", "compose_from_real_images"),
    ("usage_scene_1", "scene", "使用场景一", "我会在哪里使用？", "edit_real_image"),
    ("usage_scene_2", "usage", "使用场景二", "实际怎么使用？", "edit_real_image"),
    ("parameters", "size_spec", "参数尺寸", "规格是否适合我？", "compose_from_real_images"),
    ("sku_or_package", "comparison", "SKU或包装内容", "不同版本/包装里有什么？", "compose_from_real_images"),
    ("function_benefit", "feature", "关键功能利益", "哪个功能解决我的问题？", "edit_real_image"),
    ("notice", "disclaimer", "购买前说明", "购买前需要注意什么？", "edit_real_image"),
)

ALLOWED_IMAGE_TYPES = {
    "benefit", "detail", "scene", "usage", "size_spec", "comparison",
    "feature", "problem_solution", "disclaimer",
}
ALLOWED_OPERATIONS = {"edit_real_image", "compose_from_real_images"}


def _first(values: Iterable[Any], default: str = "unknown") -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.casefold() != "unknown":
            return text
    return default


def _texts(value: Any) -> List[str]:
    result: List[str] = []
    for item in value or []:
        text = item.get("text_ru") if isinstance(item, dict) else item
        text = str(text or "").strip()
        if text and text.casefold() != "unknown":
            result.append(text)
    return result


def _image_phrase(copy: Dict[str, Any], image_type: str, fallback: str, sku_id: str | None = None) -> List[str]:
    image_copy = copy.get("image_copy_ru") if isinstance(copy.get("image_copy_ru"), dict) else {}
    if image_type == "main" and sku_id:
        values = (image_copy.get("main_by_sku") or {}).get(str(sku_id)) or []
    else:
        values = image_copy.get(image_type) or []
    phrases = [str(value).strip() for value in values if str(value).strip()]
    if phrases:
        return phrases[:3]
    candidates = _texts(copy.get("selling_points")) + _texts(copy.get("bullets_ru"))
    return [(_first(candidates, fallback))[:90]]


def _claim_type(text: str, refs: List[str]) -> str:
    lowered = text.casefold()
    if any(token in lowered for token in ("пример", "ориентиров", "около", "примерно")):
        return "estimated"
    return "fact" if any("source.json" in ref or "product-analysis" in ref for ref in refs) else "supported_inference"


def _selected_skus(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(
        [item for item in source.get("skus") or [] if not item.get("excluded")],
        key=lambda item: int(item.get("selection_order") or 9999),
    )


def _format_ru_number(value: Any) -> str:
    number = float(value)
    return (f"{number:.2f}".rstrip("0").rstrip(".")).replace(".", ",")


def _sku_dimension_lines(selected: List[Dict[str, Any]]) -> List[str]:
    """Build one exact Russian dimension line per SKU from confirmed source data."""
    lines: List[str] = []
    for item in selected:
        source_data = item.get("source_data") if isinstance(item.get("source_data"), dict) else {}
        dimensions = source_data.get("external_dimensions_cm") if isinstance(source_data.get("external_dimensions_cm"), dict) else {}
        if not all(isinstance(dimensions.get(key), (int, float)) and dimensions[key] > 0 for key in ("length", "width", "height")):
            return []
        capacity_ml = source_data.get("capacity_ml")
        if isinstance(capacity_ml, (int, float)) and capacity_ml > 0:
            capacity = f"{_format_ru_number(capacity_ml / 1000)} л"
        else:
            capacity = _first(
                [
                    option.get("value_cn")
                    for option in item.get("option_values") or []
                    if isinstance(option, dict) and str(option.get("name_cn") or "") == "容量"
                ],
                str(item.get("sku_name") or "Вариант"),
            ).replace("L", " л").replace("l", " л")
        lines.append(
            f"{capacity}: {_format_ru_number(dimensions['length'])} × "
            f"{_format_ru_number(dimensions['width'])} × {_format_ru_number(dimensions['height'])} см"
        )
    return lines


def _sku_image_paths(product_id: str, selected: List[Dict[str, Any]]) -> List[str]:
    paths: List[str] = []
    for item in selected:
        value = str(item.get("variant_local_image_path") or item.get("local_image_path") or "").strip()
        if value and value not in paths:
            paths.append(value)
    return paths


def _attribute_summary(product_dir: Path) -> List[Dict[str, Any]]:
    path = product_dir / "output/ozon-attributes-final.json"
    if not path.is_file():
        return []
    data = load_json(path)
    result = []
    for item in data.get("attributes") or []:
        value = item.get("value")
        if value in (None, "", "unknown", [], ["unknown"]):
            continue
        result.append({
            "name": str(item.get("attribute_name") or item.get("field_key") or "unknown"),
            "value": value,
            "source": str(item.get("source") or "unknown"),
            "evidence": list(item.get("evidence") or []),
        })
    return result


def _operator_detail_roles(
    product_dir: Path,
    *,
    product_id: str,
    selected: List[Dict[str, Any]],
    copy: Dict[str, Any],
    selling: List[Dict[str, Any]],
) -> List[Dict[str, Any]] | None:
    """Return an explicit product-specific eight-image set when the operator supplied one.

    The general pipeline still has dynamic defaults.  This override exists for
    cases where the seller has already established a proven manual listing
    pattern for the exact product.  It defines commercial roles and copy only;
    it cannot change product facts or bypass real-image grounding.
    """
    path = product_dir / "input" / "operator-guidance.json"
    if not path.is_file():
        return None
    guidance = load_json(path)
    specs = guidance.get("image_detail_roles")
    if specs in (None, []):
        return None
    if not isinstance(specs, list) or len(specs) != 8:
        raise ValueError("operator-guidance.image_detail_roles must contain exactly 8 roles")

    sku_image_paths = _sku_image_paths(product_id, selected)
    roles: List[Dict[str, Any]] = []
    for index, spec in enumerate(specs, start=1):
        if not isinstance(spec, dict):
            raise ValueError("operator-guidance image role must be an object")
        image_type = str(spec.get("image_type") or "").strip()
        operation = str(spec.get("operation") or "").strip()
        if image_type not in ALLOWED_IMAGE_TYPES:
            raise ValueError(f"unsupported operator image_type: {image_type}")
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError(f"unsupported operator image operation: {operation}")
        source_references = [
            str(value).strip()
            for value in spec.get("source_references") or []
            if str(value).strip()
        ]
        if not source_references:
            source_references = (
                list(sku_image_paths)
                if image_type in {"comparison", "size_spec"}
                else [sku_image_paths[(index - 1) % len(sku_image_paths)]]
                if sku_image_paths
                else [f"products/{product_id}/input/source.json"]
            )
        russian_text = [
            str(value).strip()
            for value in spec.get("russian_text") or []
            if str(value).strip()
        ] or _image_phrase(copy, image_type, str(copy.get("title_ru") or "unknown"))
        must_prove = str(spec.get("must_prove") or "").strip()
        if not must_prove:
            must_prove = selling[(index - 1) % len(selling)]["text"]
        roles.append({
            "index": index,
            "role_id": str(spec.get("role_id") or f"operator_role_{index}").strip(),
            "image_type": image_type,
            "commercial_purpose": str(spec.get("commercial_purpose") or "商品专属详情图").strip(),
            "buyer_question": str(spec.get("buyer_question") or "这张图帮助我确认什么？").strip(),
            "russian_text": russian_text[:3],
            "operation": operation,
            "source_references": source_references,
            "sku_scope": str(spec.get("sku_scope") or "shared").strip(),
            "must_prove": must_prove,
        })
    return roles


def build_brief(
    product_dir: Path,
    *,
    source: Dict[str, Any] | None = None,
    analysis: Dict[str, Any] | None = None,
    positioning: Dict[str, Any] | None = None,
    style: Dict[str, Any] | None = None,
    copy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    product_id = product_dir.name
    source = source if source is not None else load_json(product_dir / "input/source.json")
    analysis = analysis if analysis is not None else (load_json(product_dir / "output/product-analysis.json") if (product_dir / "output/product-analysis.json").is_file() else {})
    positioning = positioning if positioning is not None else (load_json(product_dir / "output/product-positioning.json") if (product_dir / "output/product-positioning.json").is_file() else {})
    style = style if style is not None else (load_json(product_dir / "output/style-profile.json") if (product_dir / "output/style-profile.json").is_file() else {})
    copy = copy if copy is not None else (load_json(product_dir / "output/copy-ru.json") if (product_dir / "output/copy-ru.json").is_file() else {})
    category = load_json(product_dir / "input/category-selection.json") if (product_dir / "input/category-selection.json").is_file() else {}
    refs = [
        f"products/{product_id}/input/source.json",
        f"products/{product_id}/output/product-analysis.json",
    ]
    for name in ("product-positioning.json", "style-profile.json", "copy-ru.json", "ozon-attributes-final.json"):
        if (product_dir / "output" / name).is_file():
            refs.append(f"products/{product_id}/output/{name}")
    title_cn = _first([source.get("title_cn"), (analysis.get("facts") or {}).get("title_cn")])
    title_ru = _first([copy.get("title_ru"), copy.get("short_title")], title_cn)
    target = _first([positioning.get("target_customer"), style.get("target_user", [None])])
    scenes = [str(item.get("text") or item) for item in positioning.get("usage_scenarios") or []]
    scenes = [item for item in scenes if item and item != "unknown"]
    selling = []
    for item in positioning.get("buyer_selling_points") or []:
        text = str(item.get("text") or "").strip() if isinstance(item, dict) else str(item).strip()
        if text and text != "unknown":
            selling.append({"text": text, "claim_type": item.get("claim_type", "supported_inference") if isinstance(item, dict) else "supported_inference", "source_refs": item.get("source_refs", refs[:2]) if isinstance(item, dict) else refs[:2]})
    for item in copy.get("selling_points") or copy.get("bullets_ru") or []:
        text = str(item.get("text_ru") or item).strip() if isinstance(item, dict) else str(item).strip()
        if text and not any(existing["text"] == text for existing in selling):
            selling.append({"text": text, "claim_type": _claim_type(text, refs), "source_refs": refs[:2]})
    selling = selling[:6]
    if not selling:
        selling = [{"text": "Проверяйте характеристики товара перед покупкой", "claim_type": "supported_inference", "source_refs": refs[:2]}]
    selected = _selected_skus(source)
    sku_values = [{"sku_id": str(item.get("sku_id") or "unknown"), "name": str(item.get("sku_name") or "unknown"), "options": item.get("option_values") or []} for item in selected]
    sku_image_paths = _sku_image_paths(product_id, selected)
    dimension_lines = _sku_dimension_lines(selected)
    roles = _operator_detail_roles(
        product_dir,
        product_id=product_id,
        selected=selected,
        copy=copy,
        selling=selling,
    )
    if roles is None:
        roles = []
    for index, (role_id, image_type, purpose, question, operation) in enumerate(DETAIL_ROLES, start=1) if not roles else []:
        actual_type = image_type
        actual_operation = operation
        if not sku_image_paths:
            role_refs = [f"products/{product_id}/input/source.json"]
        elif image_type in {"size_spec", "comparison"}:
            role_refs = sku_image_paths
        else:
            role_refs = [sku_image_paths[(index - 1) % len(sku_image_paths)]]
        if image_type == "comparison" and len(selected) < 2:
            actual_type = "detail"
            purpose = "包装内容或可见配置"
            question = "包装中包含什么？"
        if image_type == "size_spec" and not (product_dir / "output/cost-analysis.json").is_file():
            actual_type = "detail"
            purpose = "已确认规格"
            question = "这件商品的规格是什么？"
        if actual_type == "detail" and image_type in {"size_spec", "comparison"}:
            actual_operation = "compose_from_real_images"
        text = _image_phrase(copy, actual_type, title_ru)
        if image_type == "size_spec" and dimension_lines:
            text = dimension_lines
        if image_type == "size_spec" and actual_type == "detail":
            text = ["Размеры и характеристики указаны в карточке товара"]
        if image_type == "comparison" and actual_type == "detail":
            text = ["Комплектация и варианты товара"]
        roles.append({
            "index": index,
            "role_id": role_id,
            "image_type": actual_type,
            "commercial_purpose": purpose,
            "buyer_question": question,
            "russian_text": text[:3],
            "operation": actual_operation,
            "source_references": role_refs,
            "sku_scope": "shared",
            "must_prove": selling[(index - 1) % len(selling)]["text"],
        })
    style_creative = style.get("creative_direction") or {}
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "collection_id": str(source.get("collection_id") or "COL-LEGACY-TEST"),
        "source_kind": str(source.get("source_kind") or "workbench_collection"),
        "source_refs": refs,
        "product_understanding": {
            "title_cn": title_cn,
            "title_ru": title_ru,
            "category_path": category.get("category_path_zh") or category.get("category_path_ru") or [],
            "selected_skus": sku_values,
            "verified_attributes": _attribute_summary(product_dir),
        },
        "product_positioning": {
            "target_customer": target,
            "usage_scenarios": scenes or style.get("usage_scene") or ["与商品用途匹配的真实使用环境"],
            "purchase_reasons": selling,
            "priority_order": [item["text"] for item in selling],
        },
        "visual_style": {
            "style_family": style.get("style_family", "unknown"),
            "style_direction": style_creative.get("product_visual_thesis") or style.get("composition_style") or "根据商品本身定制",
            "mood": style_creative.get("visual_mood") or style.get("tone", "unknown"),
            "palette": style_creative.get("palette_logic") or style.get("color_direction") or [],
            "lighting": style_creative.get("lighting") or "根据材质和使用场景设计真实光线",
            "composition": style_creative.get("composition") or "商品主体清晰，场景服务于购买理由",
            "typography": style_creative.get("typography") or style.get("text_style", "简洁、可读的俄文信息层级"),
            "anti_template_rule": style_creative.get("anti_template_rule") or "不得套用其他商品的配色、布景或文案",
        },
        "image_roles": roles,
        "preserve": ["商品外形", "真实颜色", "材质观感", "结构比例", "SKU差异", "商品数量", "配件数量"],
        "forbidden": ["白底目录图作为默认风格", "重新想象商品", "虚构功能/认证/参数", "空白框和占位卡片", "中文或乱码", "卖家水印"],
        "generation_order": ["俄文资料", "创意策划", "全部逐图提示词", "每个SKU主图", "8张共享详情图", "技术硬质检", "商品预览"],
        "processing": {"step": "ecommerce_creative_brief", "status": "completed", "generated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(), "error": None},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    brief = build_brief(Path(args.product_dir).resolve())
    if args.write:
        path = Path(args.product_dir).resolve() / "output/ecommerce-creative-brief.json"
        write_json_atomic(path, brief)
        print(path)
    else:
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
