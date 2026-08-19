"""Store-cluster profiles for independently positioned Ozon listings.

Profiles express a real merchandising angle and a bounded price policy.  They
must never change source-backed SKU facts or platform attributes.  The profile
is carried into every selected store's isolated upload workspace so that a
future listing/image variant has an auditable reason for being different.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping


PROFILE_LIBRARY: Dict[str, Dict[str, Any]] = {
    "premium": {
        "label": "品质店", "price_multiplier": 1.18,
        "buyer_angle": "突出可见材质、结构细节、耐用性与完整购买信息；不添加未证实性能。",
        "image_angle": "细节证明、材质近景和可信使用场景。",
    },
    "gift": {
        "label": "礼品店", "price_multiplier": 1.12,
        "buyer_angle": "突出真实送礼场景、适用对象和已确认的包装信息；没有包装事实时不得声称礼盒。",
        "image_angle": "送礼场景与产品细节，不虚构包装或附赠品。",
    },
    "traffic": {
        "label": "引流店", "price_multiplier": 0.92,
        "buyer_angle": "用清楚的商品类型、SKU差异和核心真实用途帮助快速决策；不得使用虚假低价或夸张承诺。",
        "image_angle": "三秒认知主图、清楚SKU选择和核心用途。",
    },
    "accessories_specialist": {
        "label": "胸针与配饰店", "price_multiplier": 1.05,
        "buyer_angle": "只适用于真实胸针、饰品或配饰类目；突出可见造型、佩戴/搭配场景与规格。",
        "image_angle": "真实佩戴或搭配场景；不适配类目必须跳过。",
        "allowed_product_hints": ["胸针", "配饰", "饰品", "брошь", "аксессуар", "украш"],
    },
    "value": {
        "label": "性价比店", "price_multiplier": 0.97,
        "buyer_angle": "突出真实规格、实用用途和价格价值平衡；不声称最低价或虚构赠品。",
        "image_angle": "规格、用途和选择依据。",
    },
    "standard": {
        "label": "标准店", "price_multiplier": 1.00,
        "buyer_angle": "以完整、准确、易理解的标准商品资料服务广泛买家。",
        "image_angle": "标准商品认知、结构与真实使用场景。",
    },
    "scene": {
        "label": "场景店", "price_multiplier": 1.08,
        "buyer_angle": "从商品真实可用场景切入，保持规格、功能和配件完全依据来源。",
        "image_angle": "一个可验证的主使用场景加真实细节证明。",
    },
}


def normalize_profile(value: Any) -> str:
    profile_id = str(value or "standard").strip().lower()
    return profile_id if profile_id in PROFILE_LIBRARY else "standard"


def profile_definition(value: Any) -> Dict[str, Any]:
    profile_id = normalize_profile(value)
    return {"id": profile_id, **deepcopy(PROFILE_LIBRARY[profile_id])}


def profile_from_store(shop: Mapping[str, Any]) -> Dict[str, Any]:
    return profile_definition(shop.get("store_profile"))
