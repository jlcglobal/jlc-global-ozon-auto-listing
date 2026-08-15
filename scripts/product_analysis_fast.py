#!/usr/bin/env python3
"""Create the minimal source-backed product analysis without model calls."""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(path)


def attr_value(attrs: Any, names: list[str]) -> Any:
    wanted = {name.casefold() for name in names}
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            if str(key).casefold() in wanted:
                return value
    if isinstance(attrs, list):
        for item in attrs:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name_cn") or item.get("name") or item.get("key") or "")
            if name.casefold() in wanted:
                return item.get("value_cn") or item.get("value")
    return None


def normalize_material(value: Any, title: str) -> list[str]:
    text = " ".join(str(part or "") for part in [value, title])
    materials: list[str] = []
    for marker in ("硅胶", "塑料", "不锈钢", "玻璃", "陶瓷", "金属", "木", "亚克力"):
        if marker in text and marker not in materials:
            materials.append(marker)
    return materials


def parse_dimensions(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if len(numbers) >= 3:
        return {
            "length": float(numbers[0]),
            "width": float(numbers[1]),
            "height": float(numbers[2]),
            "unit": "cm",
            "source": "input/source.json.product_attributes",
        }
    return {"raw": text, "source": "input/source.json.product_attributes"}


def parse_weight(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    number = re.search(r"\d+(?:\.\d+)?", text)
    if not number:
        return {"raw": text, "source": "input/source.json.product_attributes"}
    amount = float(number.group(0))
    unit = "g" if "kg" not in text.casefold() and "千克" not in text else "kg"
    return {"value": amount, "unit": unit, "source": "input/source.json.product_attributes"}


def sku_entry(sku: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for item in sku.get("option_values") or []:
        if isinstance(item, dict):
            props[str(item.get("name_cn") or "规格")] = item.get("value_cn") or item.get("value") or "unknown"
    if not props and sku.get("sku_name"):
        props["规格"] = sku.get("sku_name")
    image_refs = []
    for key in ("local_image_path", "variant_local_image_path"):
        value = str(sku.get(key) or "")
        if value and value != "unknown":
            image_refs.append(value)
    return {
        "sku_id": str(sku.get("sku_id") or "unknown"),
        "name_cn": str(sku.get("sku_name") or "unknown"),
        "properties": props,
        "price_cny": float(sku.get("purchase_price") or sku.get("price") or 0) or None,
        "image_refs": image_refs,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()
    source = load_json(product_dir / "input/source.json", {})
    category = load_json(product_dir / "input/category-selection.json", {})
    attrs = source.get("product_attributes") or []
    title = str(source.get("title_cn") or "unknown")
    category_cn = str(category.get("category_name_zh") or source.get("category_cn") or "unknown")
    materials = normalize_material(attr_value(attrs, ["材质", "材料", "主体材质"]), title)
    dimensions = parse_dimensions(attr_value(attrs, ["尺寸", "规格尺寸", "产品尺寸", "长宽高"]))
    weight = parse_weight(attr_value(attrs, ["重量", "净重", "毛重"]))
    functions = []
    for name in ("供电方式", "功能", "用途", "产品规格"):
        value = attr_value(attrs, [name])
        if value:
            functions.append(f"{name}: {value}")
    risks = []
    if category_cn != "unknown" and title != "unknown" and not any(word in title for word in re.split(r"[/ >]+", category_cn) if word):
        risks.append({
            "area": "category_fit",
            "level": "medium",
            "message": f"当前类目为{category_cn}，需确认是否匹配商品标题。",
            "blocking": False,
        })
    skus = [sku_entry(item) for item in (source.get("skus") or []) if isinstance(item, dict)]
    missing_sku_images = sum(1 for item in skus if not item["image_refs"])
    if missing_sku_images:
        risks.append({
            "area": "sku_images",
            "level": "medium",
            "message": f"{missing_sku_images}个SKU缺少专属图片，生图前需要绑定或确认参考图。",
            "blocking": False,
        })
    now = datetime.now().astimezone().replace(microsecond=0).isoformat()
    analysis = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "source_refs": [
            f"products/{product_dir.name}/input/source.json",
            f"products/{product_dir.name}/input/category-selection.json",
        ],
        "product_type": title,
        "category": category_cn,
        "target_customer": ["unknown"],
        "usage_scenarios": ["依据标题和类目进行后续俄文设计"],
        "competitive_advantages": [
            {"text": "标题、SKU和结构化属性已采集，可用于后续字段编译。", "evidence": ["input/source.json"]}
        ],
        "missing_information": [
            {"field": "certifications", "reason": "1688采集未提供可靠认证。", "needed_from_human": False},
            {"field": "load_capacity", "reason": "1688采集未提供可靠承重。", "needed_from_human": False},
        ],
        "recommended": "continue",
        "score": 70,
        "facts": {
            "title_cn": title,
            "category_cn": category_cn,
            "brand": "Нет бренда",
            "materials": materials,
            "dimensions": dimensions,
            "weight": weight,
            "load_capacity": "unknown",
            "certifications": [],
            "functions": functions,
            "package_quantity": {"value": 1, "source": "project_default"},
            "accessories": [],
            "skus": skus,
        },
        "selling_points": [
            {"text": title, "evidence": ["input/source.json.title_cn"]},
        ],
        "inferences": [
            {"field": "origin_country", "value": "中国", "confidence": "high", "basis": ["1688来源商品默认中国"]},
        ],
        "unknowns": [
            {"field": "certifications", "reason": "来源未确认，不能推测。", "needed_from_human": False},
            {"field": "load_capacity", "reason": "来源未确认，不能推测。", "needed_from_human": False},
        ],
        "risks": risks or [
            {"area": "source", "level": "low", "message": "未发现阻塞性风险。", "blocking": False}
        ],
        "recommendation": {"decision": "continue", "reason": "已生成可追溯基础事实，后续步骤继续补全。"},
        "processing": {
            "step": "product_analysis",
            "status": "completed",
            "started_at": now,
            "finished_at": now,
            "error": None,
        },
    }
    write_json_atomic(product_dir / "output/product-analysis.json", analysis)
    print(json.dumps({"status": "PASS", "product_id": product_dir.name}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
