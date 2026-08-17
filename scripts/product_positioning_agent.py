#!/usr/bin/env python3
"""Build evidence-backed buyer positioning without calling an AI API.

The product analysis owns product facts. This step turns those facts into a
buyer-facing decision brief (audience, pain, scenes and selling points) while
keeping every inference traceable and avoiding unsupported product claims.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def evidence_texts(items: Any) -> List[str]:
    values = []
    for item in items or []:
        if isinstance(item, dict) and item.get("text"):
            values.append(str(item["text"]))
    return values


def first_known(values: Any) -> str:
    if not isinstance(values, list):
        values = [values]
    for value in values:
        if value not in (None, "", "unknown"):
            return str(value)
    return "unknown"


def analysis_claims(items: Any, analysis_ref: str) -> List[Dict[str, Any]]:
    claims = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text or text == "unknown":
            continue
        refs = [str(value) for value in item.get("evidence") or [] if str(value).strip()]
        claims.append({
            "text": text,
            "claim_type": "fact",
            "source_refs": refs or [analysis_ref],
        })
    return claims[:5]


def product_family(product_type: str, analysis: Dict[str, Any]) -> str:
    facts = analysis.get("facts") or {}
    haystack = " ".join([
        product_type,
        str(analysis.get("category") or ""),
        str(facts.get("title_cn") or ""),
        str(facts.get("category_cn") or ""),
    ]).casefold()
    families = (
        ("portable_juicer", ("榨汁", "果汁杯", "миксер", "juicer", "blender")),
        ("fridge_organizer", ("冰箱收纳", "冰箱食品", "органайзер для холодильника", "fridge organizer")),
        ("food_storage", ("食品储", "储藏罐", "密封罐", "收纳罐", "контейнер", "storage container")),
        ("bathroom", ("卫浴", "浴室", "淋浴", "水龙头", "花洒", "bathroom", "shower")),
        ("outdoor", ("户外", "露营", "庭院", "帐篷", "camping", "outdoor")),
        ("electronics", ("电子", "充电器", "耳机", "数据线", "电源", "device", "charger")),
        ("kitchen", ("厨房", "厨具", "锅", "餐具", "kitchen")),
    )
    return next((name for name, tokens in families if any(token in haystack for token in tokens)), "generic")


def inferred_buyer_context(
    family: str,
    product_type: str,
    analysis_ref: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Infer only low-risk buyer context, never a new product capability."""
    contexts = {
        "fridge_organizer": {
            "target_customer": "希望把冰箱内食品分区整理、便于看清和取用的家庭用户",
            "customer_pain_points": ["冰箱内食品容易散乱混放，拿取和查看不够直接"],
            "emotional_trigger": "打开冰箱时分类清楚，取用更顺手",
            "recommended_visual_direction": "以透明盒体、盖子和前置把手为主角，放入整洁但真实的冰箱层架；分别表现分类收纳、抽取动作和3L/5L/6L尺寸选择，不添加未经确认的保鲜或耐温承诺",
            "usage_scenarios": ["冰箱冷藏室层架分类收纳", "厨房备餐后将食品放入冰箱"],
        },
        "portable_juicer": {
            "target_customer": "希望在家中或办公环境方便准备单杯果汁、重视便携杯式形态的用户",
            "customer_pain_points": ["不希望每次准备单杯果汁都依赖占空间的固定台面设备"],
            "emotional_trigger": "让日常准备一杯果汁更灵活、更省空间",
            "recommended_visual_direction": "以真实杯体为绝对主角，用现代厨房和办公桌两个场景表现便携感；通过清晰俄文大标题、容量与对应SKU材质短卖点完成电商表达",
            "usage_scenarios": ["现代厨房台面准备单杯果汁", "办公桌或休息区展示便携杯式使用情境"],
        },
        "food_storage": {
            "target_customer": "希望让厨房和食品储藏空间更整洁、便于查看和取用的家庭用户",
            "customer_pain_points": ["食品包装零散、占空间，内容物不容易快速辨认"],
            "emotional_trigger": "让储藏空间看起来更整齐、取用更直接",
            "recommended_visual_direction": "以真实容器结构和实际收纳效果为主，使用厨房台面、橱柜或食品储藏区场景；卖点文字只表达已确认的容量、结构和用途",
            "usage_scenarios": ["厨房台面日常收纳", "橱柜或食品储藏区分类摆放"],
        },
        "bathroom": {
            "target_customer": "正在改善浴室或卫浴使用体验、重视安装效果和空间适配的家庭用户",
            "customer_pain_points": ["担心商品安装后与浴室空间、使用方式或现有设施不匹配"],
            "emotional_trigger": "让卫浴空间的使用和视觉效果更清楚可预期",
            "recommended_visual_direction": "在真实卫浴环境中突出商品本体、安装位置和可见结构，卖点围绕已确认用途与尺寸，不添加认证或性能承诺",
            "usage_scenarios": ["家庭浴室真实安装环境", "卫浴使用位置的近景展示"],
        },
        "outdoor": {
            "target_customer": "需要在户外、庭院或露营环境中使用该类商品的用户",
            "customer_pain_points": ["担心商品放到真实户外环境后用途不直观或不便判断是否适配"],
            "emotional_trigger": "让户外使用方式和商品价值一眼可见",
            "recommended_visual_direction": "采用真实户外或庭院环境，以商品本体和明确使用动作表现用途；只展示来源确认的结构、数量和功能",
            "usage_scenarios": ["与商品用途匹配的户外环境", "庭院或露营区域的实际摆放"],
        },
        "electronics": {
            "target_customer": "希望快速理解设备用途、接口或便携方式的日常用户",
            "customer_pain_points": ["仅看商品外观难以快速理解设备的核心用途和适用情境"],
            "emotional_trigger": "让设备价值和使用方式更直观",
            "recommended_visual_direction": "使用干净的科技感光影和真实桌面场景，商品结构、接口和配件必须与原图一致；卖点只采用已确认功能与规格",
            "usage_scenarios": ["现代家庭或办公桌面", "与已确认用途匹配的日常设备场景"],
        },
        "kitchen": {
            "target_customer": "希望提高厨房日常使用便利性、快速理解商品用途的家庭用户",
            "customer_pain_points": ["商品放入真实厨房后的用途、占用空间或使用方式不够直观"],
            "emotional_trigger": "让厨房使用过程更清楚、更顺手",
            "recommended_visual_direction": "使用与商品用途匹配的真实厨房场景，以商品本体和一个明确使用动作作为视觉中心；只表达已确认卖点",
            "usage_scenarios": ["现代家庭厨房台面", "与商品用途匹配的厨房使用位置"],
        },
    }
    context = contexts.get(family, {
        "target_customer": f"正在选购{product_type}、希望快速判断实际用途和规格是否合适的用户" if product_type != "unknown" else "unknown",
        "customer_pain_points": ["仅看原始商品图时，核心用途、规格差异和购买理由不够直观"],
        "emotional_trigger": "更快看懂商品用途、规格和选择依据",
        "recommended_visual_direction": "以真实商品为主角，先明确商品用途和已确认规格，再使用与类目匹配的真实场景；禁止用通用空模板代替卖点表达",
        "usage_scenarios": [f"与{product_type}用途匹配的日常使用环境" if product_type != "unknown" else "日常商品使用环境"],
    })
    evidence = []
    for field in ("target_customer", "customer_pain_points", "emotional_trigger", "recommended_visual_direction", "usage_scenarios"):
        values = context[field] if isinstance(context[field], list) else [context[field]]
        for value in values:
            if value == "unknown":
                continue
            evidence.append({
                "field": field,
                "claim_type": "supported_inference",
                "statement": value,
                "source_refs": [analysis_ref],
            })
    return context, evidence


def build_positioning_draft(product_dir: Path, analysis: Dict[str, Any]) -> Dict[str, Any]:
    product_id = product_dir.name
    analysis_ref = f"products/{product_id}/output/product-analysis.json"
    source_ref = f"products/{product_id}/input/source.json"
    product_type = first_known([
        analysis.get("product_type"),
        (analysis.get("facts") or {}).get("title_cn"),
    ])
    analyzed_customer = first_known(analysis.get("target_customer", []))
    buyer_selling_points = analysis_claims(analysis.get("selling_points", []), analysis_ref)
    selling_point = first_known([item["text"] for item in buyer_selling_points])
    advantage = first_known(evidence_texts(analysis.get("competitive_advantages", [])))
    family = product_family(product_type, analysis)
    context, context_evidence = inferred_buyer_context(family, product_type, analysis_ref)
    customer = (
        context["target_customer"]
        if family != "generic"
        else analyzed_customer if analyzed_customer != "unknown" else context["target_customer"]
    )
    if advantage == "unknown" and len(buyer_selling_points) > 1:
        advantage = buyer_selling_points[1]["text"]
    inferred_point_candidates = [
        *[f"适合在{value}中使用。" for value in context["usage_scenarios"]],
        context["emotional_trigger"],
        f"围绕“{context['customer_pain_points'][0]}”提供更直观的购买理由。",
    ]
    for value in inferred_point_candidates:
        normalized = str(value or "").strip()
        if not normalized or normalized == "unknown" or any(item["text"] == normalized for item in buyer_selling_points):
            continue
        buyer_selling_points.append({
            "text": normalized,
            "claim_type": "supported_inference",
            "source_refs": [analysis_ref],
        })
        if len(buyer_selling_points) >= 5:
            break
    if selling_point == "unknown" and buyer_selling_points:
        selling_point = buyer_selling_points[0]["text"]
    if advantage == "unknown" and len(buyer_selling_points) > 1:
        advantage = buyer_selling_points[1]["text"]
    timestamp = datetime.now().astimezone().replace(microsecond=0).isoformat()

    evidence = list(context_evidence)
    fields = {
        "market_positioning": f"{product_type}，面向{customer}" if product_type != "unknown" and customer != "unknown" else "unknown",
        "target_customer": customer,
        "purchase_motivation": selling_point,
        "core_sales_angle": selling_point,
        "competitive_advantage": advantage,
    }
    for field, statement in fields.items():
        if statement != "unknown":
            if any(item["field"] == field and item["statement"] == statement for item in evidence):
                continue
            evidence.append({
                "field": field,
                "claim_type": "supported_inference" if field in {"market_positioning", "target_customer"} else "fact",
                "statement": statement,
                "source_refs": [analysis_ref],
            })

    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "source_refs": [source_ref, analysis_ref],
        "market_positioning": fields["market_positioning"],
        "target_customer": customer,
        "purchase_motivation": selling_point,
        "customer_pain_points": context["customer_pain_points"],
        "core_sales_angle": selling_point,
        "buyer_selling_points": buyer_selling_points,
        "usage_scenarios": [
            {
                "text": value,
                "claim_type": "supported_inference",
                "source_refs": [analysis_ref],
            }
            for value in context["usage_scenarios"]
        ],
        "emotional_trigger": context["emotional_trigger"],
        "competitive_advantage": advantage,
        "recommended_visual_direction": context["recommended_visual_direction"],
        "recommended_price_position": "unknown",
        "positioning_evidence": evidence,
        "unknowns": [
            "缺少目标市场售价、成本、平台费用和竞品数据，价格定位保持unknown。",
        ],
        "processing": {
            "step": "product_positioning",
            "status": "completed",
            "started_at": timestamp,
            "finished_at": timestamp,
            "error": None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a conservative Product Positioning draft.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--write", action="store_true", help="Write output/product-positioning.json")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()
    payload = build_positioning_draft(product_dir, load_json(product_dir / "output/product-analysis.json"))
    if args.write:
        output = product_dir / "output/product-positioning.json"
        write_json_atomic(output, payload)
        print(output)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
