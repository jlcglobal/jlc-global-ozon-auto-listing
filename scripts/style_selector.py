#!/usr/bin/env python3
"""Select a constrained ecommerce image style from product facts and analysis."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
STYLE_PROFILES_PATH = ROOT / "rules" / "style_profiles.json"
SELECTOR_RULES_PATH = ROOT / "rules" / "style_selector_rules.json"
STRUCTURE_RULES_PATH = ROOT / "rules" / "image_structure_rules.json"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def clean_values(values: Iterable[Any]) -> List[str]:
    cleaned: List[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() != "unknown" and text not in cleaned:
            cleaned.append(text)
    return cleaned


def evidence_texts(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    values = []
    for item in items:
        if isinstance(item, dict):
            values.append(item.get("text"))
        else:
            values.append(item)
    return clean_values(values)


def split_category(category: str) -> Tuple[str, str]:
    if not category or category == "unknown":
        return "unknown", "unknown"
    normalized = category.replace(">", "/").replace("｜", "/")
    parts = [part.strip() for part in normalized.split("/") if part.strip()]
    return parts[0], parts[1] if len(parts) > 1 else "unknown"


def build_context(
    source: Dict[str, Any],
    analysis: Dict[str, Any],
    positioning: Dict[str, Any] | None = None,
    category_selection: Dict[str, Any] | None = None,
) -> Dict[str, List[str]]:
    positioning = positioning or {}
    category_selection = category_selection or {}
    attributes = []
    for item in source.get("product_attributes", []):
        if isinstance(item, dict):
            attributes.extend([item.get("name_cn"), item.get("value_cn")])

    target_user = clean_values([
        *analysis.get("target_customer", []),
        positioning.get("target_customer"),
    ])
    usage_scene = clean_values(analysis.get("usage_scenarios", []))
    purchase_motivation = evidence_texts(analysis.get("selling_points", []))
    purchase_motivation.extend(evidence_texts(analysis.get("competitive_advantages", [])))
    purchase_motivation.extend([
        positioning.get("purchase_motivation"),
        positioning.get("core_sales_angle"),
        positioning.get("competitive_advantage"),
    ])

    return {
        "product_type": clean_values([analysis.get("product_type")]),
        "category": clean_values([
            category_selection.get("category_name_zh"),
            *(category_selection.get("category_path_zh") or []),
            analysis.get("category"),
            analysis.get("facts", {}).get("category_cn"),
        ]),
        "title": clean_values([source.get("title_cn"), analysis.get("facts", {}).get("title_cn")]),
        "usage_scene": usage_scene,
        "target_user": target_user,
        "purchase_motivation": clean_values(purchase_motivation),
        "customer_pain_points": clean_values(positioning.get("customer_pain_points", [])),
        "market_positioning": clean_values([positioning.get("market_positioning")]),
        "attributes": clean_values(attributes),
    }


def score_rule(
    rule: Dict[str, Any],
    context: Dict[str, List[str]],
    field_weights: Dict[str, int],
) -> Tuple[int, List[str]]:
    score = 0
    matches: List[str] = []
    for field, keywords in rule.get("signals", {}).items():
        haystack = " ".join(context.get(field, [])).lower()
        if not haystack:
            continue
        for keyword in keywords:
            if str(keyword).lower() in haystack:
                score += int(field_weights.get(field, 1))
                matches.append(f"{field}:{keyword}")
    return score, matches


def score_purchase_mode(context: Dict[str, List[str]], axes: Dict[str, List[str]]) -> Dict[str, Any]:
    combined = " ".join(value for values in context.values() for value in values).lower()
    scores: Dict[str, int] = {}
    for axis, keywords in axes.items():
        scores[axis] = sum(1 for keyword in keywords if str(keyword).lower() in combined)

    renamed = {
        "parameter_driven": scores.get("parameter_driven", 0),
        "lifestyle_driven": scores.get("lifestyle_driven", 0),
        "problem_solution_driven": scores.get("problem_solution_driven", 0),
    }
    maximum = max(renamed.values(), default=0)
    leaders = [name for name, value in renamed.items() if value == maximum and value > 0]
    dominant_map = {
        "parameter_driven": "parameter",
        "lifestyle_driven": "lifestyle",
        "problem_solution_driven": "problem_solution",
    }
    if not leaders:
        dominant = "unknown"
    elif len(leaders) > 1:
        dominant = "balanced"
    else:
        dominant = dominant_map[leaders[0]]
    return {**renamed, "dominant": dominant}


def image_refs(product_dir: Path, source: Dict[str, Any]) -> List[str]:
    refs = []
    for collection in (source.get("main_images", []), source.get("detail_images", [])):
        for item in collection:
            if not isinstance(item, dict) or item.get("download_status") != "downloaded":
                continue
            local_path = item.get("local_path")
            if not local_path or str(local_path).lower() == "unknown":
                continue
            if str(item.get("original_url", "")).lower().endswith(".svg"):
                continue
            refs.append(str(local_path))
    for sku in source.get("skus", []):
        if not isinstance(sku, dict) or sku.get("sku_image_missing"):
            continue
        local_path = sku.get("local_image_path")
        if local_path and str(local_path).lower() != "unknown":
            refs.append(str(local_path))
    return list(dict.fromkeys(refs))


def confidence_for(top_score: int, second_score: int, minimum_score: int) -> float:
    if top_score <= 0:
        return 0.0
    strength = min(1.0, top_score / max(minimum_score * 2, 1))
    separation = 1.0 if second_score == 0 else max(0.0, (top_score - second_score) / top_score)
    return round((strength * 0.6) + (separation * 0.4), 2)


def has_product_dimensions(product_dir: Path) -> bool:
    path = product_dir / "output/cost-analysis.json"
    if not path.is_file():
        return False
    dimensions = load_json(path).get("product_dimensions") or {}
    try:
        return all(float(dimensions[key]) > 0 for key in ("length", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return False


def dynamic_image_structure(product_dir: Path, source: Dict[str, Any]) -> List[str]:
    """Create a buyer-decision sequence for this product, not a category template."""
    structure = ["main", "benefit", "problem_solution", "scene", "feature", "detail", "usage"]
    if has_product_dimensions(product_dir):
        structure.append("size_spec")
    if len(source.get("skus") or []) > 1:
        structure.append("comparison")
    return structure


def product_specific_creative_direction(
    context: Dict[str, List[str]],
    positioning: Dict[str, Any],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Turn the broad style family into a specific visual thesis for this exact item."""
    product = next(iter(context["product_type"] or context["title"]), "当前商品")
    target = next(iter(context["target_user"]), positioning.get("target_customer") or "目标买家")
    motivation = next(
        iter(context["purchase_motivation"]),
        positioning.get("core_sales_angle") or "最直接的使用收益",
    )
    scene = next(iter(context["usage_scene"]), "最可信的真实使用环境")
    pain = next(iter(context["customer_pain_points"]), "买家使用前最关心的问题")
    colors = "、".join(profile.get("color_direction") or ["根据商品本色现场确定"])
    return {
        "product_visual_thesis": f"围绕“{product}”本身建立视觉记忆，核心只讲“{motivation}”，不得套用同类商品固定布景。",
        "target_buyer": target,
        "click_hook": f"先让买家一眼看懂产品和“{motivation}”，再用场景证明，不做普通摆拍。",
        "hero_scene": f"以{scene}为真实语境，但构图、道具和光线必须围绕{product}重新设计。",
        "buyer_tension": pain,
        "visual_mood": profile.get("tone", "unknown"),
        "palette_logic": f"以商品真实颜色为主，{colors}只用于衬托和信息层级，不得盖过商品。",
        "lighting": "根据商品表面、透明度和结构选择能突出轮廓与质感的真实光线；禁止千篇一律的暖厨房光。",
        "composition": f"主图突出单个SKU和一个核心收益；详情图按购买决策逐步换场景、视角和信息，不重复同一构图。",
        "typography": "俄文与画面一体设计；主图只放一条大字卖点，详情图允许分层信息，但不得使用固定黑色文字框。",
        "consistency_rule": "整套保持字体家族、色彩逻辑和视觉语气一致；每张图的场景、构图和回答的问题必须不同。",
        "anti_template_rule": f"即使同属一个类目，也必须从{product}的结构、买家、卖点和使用动作重新决定画面。",
    }


def select_style_profile(
    product_dir: Path,
    source: Dict[str, Any],
    analysis: Dict[str, Any],
    positioning: Dict[str, Any] | None = None,
    category_selection: Dict[str, Any] | None = None,
    profiles_config: Dict[str, Any] | None = None,
    selector_config: Dict[str, Any] | None = None,
    structures_config: Dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> Dict[str, Any]:
    profiles_config = profiles_config or load_json(STYLE_PROFILES_PATH)
    selector_config = selector_config or load_json(SELECTOR_RULES_PATH)
    structures_config = structures_config or load_json(STRUCTURE_RULES_PATH)

    positioning = positioning or {}
    context = build_context(source, analysis, positioning, category_selection)
    scored = []
    for rule in selector_config["rules"]:
        score, matches = score_rule(rule, context, selector_config["field_weights"])
        scored.append({
            "rule_id": rule["rule_id"],
            "style_family": rule["style_family"],
            "score": score,
            "matched_signals": matches,
            "minimum_score": rule["minimum_score"],
        })
    scored.sort(key=lambda item: (-item["score"], item["rule_id"]))

    top = scored[0] if scored else None
    second_score = scored[1]["score"] if len(scored) > 1 else 0
    policy = selector_config["selection_policy"]
    style_family = policy["no_match_style"]
    classification_status = "needs_input"
    confidence = 0.0

    has_product_input = any(context[field] for field in ("product_type", "category", "title"))
    if top and top["score"] >= top["minimum_score"]:
        confidence = confidence_for(top["score"], second_score, top["minimum_score"])
        style_family = top["style_family"]
        margin = top["score"] - second_score
        classification_status = (
            "selected"
            if confidence >= policy["minimum_confidence"] and margin >= policy["review_margin"]
            else "needs_review"
        )
    elif has_product_input:
        classification_status = "needs_review"

    category_value = next(iter(context["category"]), "unknown")
    category_primary, category_secondary = split_category(category_value)
    source_ref = f"products/{product_dir.name}/input/source.json"
    analysis_ref = f"products/{product_dir.name}/output/product-analysis.json"
    positioning_ref = f"products/{product_dir.name}/output/product-positioning.json"
    refs = [source_ref, analysis_ref]
    if positioning:
        refs.append(positioning_ref)
    refs.extend(image_refs(product_dir, source))
    refs = list(dict.fromkeys(refs))

    purchase_mode = score_purchase_mode(context, selector_config["decision_axes"])
    matched_rules = [
        {
            "rule_id": item["rule_id"],
            "style_family": item["style_family"],
            "score": item["score"],
            "matched_signals": item["matched_signals"],
        }
        for item in scored
        if item["score"] > 0
    ]

    if style_family == "unknown":
        profile = {
            "tone": "unknown",
            "color_direction": [],
            "composition_style": "unknown",
            "text_style": "unknown",
            "image_strategy": ["unknown"],
            "required_visual_signals": [],
            "forbidden_visual_signals": [],
            "truthfulness_guardrails": ["不得在缺少真实商品信息时生成最终商品图片"],
        }
    else:
        profile = profiles_config["profiles"][style_family]

    selection_evidence = []
    positioning_values = clean_values([
        positioning.get("market_positioning"),
        positioning.get("target_customer"),
        positioning.get("purchase_motivation"),
        positioning.get("core_sales_angle"),
        positioning.get("competitive_advantage"),
        *positioning.get("customer_pain_points", []),
    ])
    if top:
        for match in top["matched_signals"]:
            field, keyword = match.split(":", 1)
            values = [value for value in context.get(field, []) if keyword.lower() in value.lower()]
            if field == "title" and keyword.lower() in str(source.get("title_cn", "")).lower():
                evidence_ref = source_ref
            elif any(keyword.lower() in value.lower() for value in positioning_values):
                evidence_ref = positioning_ref
            else:
                evidence_ref = analysis_ref
            selection_evidence.append({
                "signal": field,
                "value": values[0] if values else keyword,
                "source_refs": [evidence_ref],
            })

    timestamp = generated_at or datetime.now().astimezone().replace(microsecond=0).isoformat()
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "generated_at": timestamp,
        "classification_status": classification_status,
        "source_refs": refs,
        "positioning_ref": positioning_ref if positioning else analysis_ref,
        "category_primary": category_primary,
        "category_secondary": category_secondary,
        "target_user": context["target_user"] or ["unknown"],
        "purchase_motivation": context["purchase_motivation"] or ["unknown"],
        "usage_scene": context["usage_scene"] or ["unknown"],
        "price_positioning": positioning.get("recommended_price_position", "unknown"),
        "purchase_mode": purchase_mode,
        "style_family": style_family,
        "style_template_ref": f"rules/style_profiles.json#profiles/{style_family}",
        "image_strategy": profile["image_strategy"],
        "tone": profile["tone"],
        "color_direction": profile["color_direction"],
        "composition_style": profile["composition_style"],
        "text_style": profile["text_style"],
        "image_set_structure": dynamic_image_structure(product_dir, source) if style_family != "unknown" else [],
        "creative_direction": product_specific_creative_direction(context, positioning, profile),
        "selection_evidence": selection_evidence,
        "matched_rules": matched_rules,
        "confidence": confidence,
        "generator_constraints": {
            "required_visual_signals": profile["required_visual_signals"],
            "forbidden_visual_signals": profile["forbidden_visual_signals"],
            "truthfulness_guardrails": profile["truthfulness_guardrails"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Select an image style for one product directory.")
    parser.add_argument("product_dir", help="Path to products/{product_id}")
    parser.add_argument("--stdout", action="store_true", help="Print without writing output/style-profile.json")
    args = parser.parse_args()

    product_dir = Path(args.product_dir).resolve()
    source = load_json(product_dir / "input" / "source.json")
    analysis_path = product_dir / "output" / "product-analysis.json"
    if not analysis_path.is_file():
        raise SystemExit(f"Missing product analysis: {analysis_path}")
    positioning_path = product_dir / "output" / "product-positioning.json"
    positioning = load_json(positioning_path) if positioning_path.is_file() else None
    category_selection_path = product_dir / "input" / "category-selection.json"
    category_selection = load_json(category_selection_path) if category_selection_path.is_file() else None
    profile = select_style_profile(
        product_dir, source, load_json(analysis_path), positioning,
        category_selection=category_selection,
    )
    learned_path = product_dir / "input/learned-experience.json"
    learned = load_json(learned_path) if learned_path.is_file() else {}
    profile["learned_image_preferences"] = [
        item for item in learned.get("active_image_preferences") or []
        if item.get("style_family") == profile.get("style_family")
    ]

    if args.stdout:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    else:
        output = product_dir / "output" / "style-profile.json"
        write_json_atomic(output, profile)
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
