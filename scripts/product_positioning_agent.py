#!/usr/bin/env python3
"""Build a conservative Product Positioning draft without calling an AI API."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    from scripts.style_selector import load_json, write_json_atomic
except ModuleNotFoundError:
    from style_selector import load_json, write_json_atomic


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


def build_positioning_draft(product_dir: Path, analysis: Dict[str, Any]) -> Dict[str, Any]:
    product_id = product_dir.name
    analysis_ref = f"products/{product_id}/output/product-analysis.json"
    source_ref = f"products/{product_id}/input/source.json"
    product_type = first_known(analysis.get("product_type"))
    customer = first_known(analysis.get("target_customer", []))
    selling_point = first_known(evidence_texts(analysis.get("selling_points", [])))
    advantage = first_known(evidence_texts(analysis.get("competitive_advantages", [])))
    timestamp = datetime.now().astimezone().replace(microsecond=0).isoformat()

    evidence = []
    fields = {
        "target_customer": customer,
        "purchase_motivation": selling_point,
        "core_sales_angle": selling_point,
        "competitive_advantage": advantage,
    }
    for field, statement in fields.items():
        if statement != "unknown":
            evidence.append({
                "field": field,
                "claim_type": "supported_inference" if field == "target_customer" else "fact",
                "statement": statement,
                "source_refs": [analysis_ref],
            })

    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "source_refs": [source_ref, analysis_ref],
        "market_positioning": f"{product_type}，面向{customer}" if product_type != "unknown" and customer != "unknown" else "unknown",
        "target_customer": customer,
        "purchase_motivation": selling_point,
        "customer_pain_points": ["unknown"],
        "core_sales_angle": selling_point,
        "emotional_trigger": "unknown",
        "competitive_advantage": advantage,
        "recommended_visual_direction": "unknown",
        "recommended_price_position": "unknown",
        "positioning_evidence": evidence,
        "unknowns": [
            "客户痛点、情绪触发和视觉方向需要Codex结合真实图片与商品事实进一步判断。",
            "缺少目标市场售价、成本、平台费用和竞品数据，价格定位保持unknown。",
        ],
        "processing": {
            "step": "product_positioning",
            "status": "in_progress",
            "started_at": timestamp,
            "finished_at": "unknown",
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
