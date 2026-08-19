"""Shared-entity safety policy for JLC store-group selections.

One product may be distributed only to shops belonging to different business
entities.  The operator approved two fixed cross-entity store groups; every
other new selection must contain exactly one store.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping


PRESETS: Dict[str, List[str]] = {
    "1256": ["zhonglian1", "zhonglian2", "zhonglian5", "jlc-blobal-6"],
    "V346": ["volttech", "zhonglian3", "zhonglian4", "jlc-blobal-6"],
}


class StoreGroupPolicyError(ValueError):
    pass


def normalize_store_ids(store_ids: Iterable[Any]) -> List[str]:
    return list(dict.fromkeys(str(value).strip() for value in store_ids if str(value).strip()))


def preset_for(store_ids: Iterable[Any]) -> str | None:
    selected = set(normalize_store_ids(store_ids))
    return next((name for name, members in PRESETS.items() if selected == set(members)), None)


def validate_selection(store_ids: Iterable[Any], shops: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    selected = normalize_store_ids(store_ids)
    if not selected:
        raise StoreGroupPolicyError("请至少选择一家店铺")
    available = {str(shop.get("id") or "") for shop in shops}
    unknown = [store_id for store_id in selected if store_id not in available]
    if unknown:
        raise StoreGroupPolicyError("未知店铺：" + "、".join(unknown))
    if len(selected) == 1:
        return {"store_ids": selected, "mode": "single_store", "preset": None}
    preset = preset_for(selected)
    if preset:
        return {"store_ids": PRESETS[preset], "mode": "cross_entity_preset", "preset": preset}
    raise StoreGroupPolicyError("多店上架仅允许跨主体组合 1256 或 V346；其他情况请单选一家店铺")
