"""Local Ozon category selection and immutable collector rule snapshots.

This module only reads Ozon metadata unless ``allow_fetch`` is explicitly used.
It never calls product write or inventory endpoints.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


DEFAULT_SHOP = "zhonglian1"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _positive_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def catalog_path(root: Path) -> Path:
    return root / "ozon-adapter/metadata/ozon-rules-2026-07-10/categories.json"


def preferences_path(root: Path) -> Path:
    return root / "config/collector-category-preferences.json"


def alias_path(root: Path) -> Path:
    return root / "config/ozon-category-search-aliases.json"


def translated_tree_cache_path(root: Path) -> Path:
    return root / "ozon-adapter/metadata/ozon-rules-2026-07-10/category-tree.zh-CN.json"


def collector_rules_cache_path(root: Path) -> Path:
    return root / "collector/edge-extension/category-rules-cache.json"


def category_key(category_id: int, type_id: int) -> str:
    return f"{category_id}:{type_id}"


def _official_zh_cache(cache: Mapping[str, Any]) -> bool:
    return bool(
        cache.get("locale") == "zh-CN"
        and cache.get("source") == "ozon_seller_api"
        and cache.get("api_language") == "ZH_HANS"
        and cache.get("official_labels_required") is True
        and cache.get("children_by_parent")
        and cache.get("search_items")
    )


@lru_cache(maxsize=8)
def _load_catalog_cached(catalog_file: str, catalog_mtime_ns: int, cache_file: str, cache_mtime_ns: int) -> tuple[Dict[str, Any], ...]:
    if cache_file and Path(cache_file).is_file():
        cache = load_json(Path(cache_file), {})
        cached_items = cache.get("search_items") or []
        catalog_hash = hashlib.sha256(Path(catalog_file).read_bytes()).hexdigest()
        if _official_zh_cache(cache) and cache.get("catalog_sha256") == catalog_hash:
            return tuple({**item, "source": "official_ozon_seller_api_zh_hans"} for item in cached_items)
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in load_json(Path(catalog_file), []):
        category_id = _positive_int(item.get("categoryId", item.get("description_category_id")))
        type_id = _positive_int(item.get("typeId", item.get("type_id")))
        if category_id is None or type_id is None:
            continue
        key = category_key(category_id, type_id)
        if key in seen:
            continue
        seen.add(key)
        path = item.get("categoryPath") or []
        if isinstance(path, str):
            path = [part.strip() for part in path.split("/") if part.strip()]
        name_ru = str(item.get("nameRu") or item.get("title") or (path[-1] if path else "unknown")).strip()
        name_zh = str(item.get("nameZh") or "").strip()
        result.append({
            "category_id": category_id,
            "type_id": type_id,
            "name_ru": name_ru,
            "name_zh": name_zh or None,
            "path": [str(part).strip() for part in path if str(part).strip()],
            "source": "local_ozon_rule_catalog",
        })
    return tuple(result)


def load_catalog(root: Path) -> List[Dict[str, Any]]:
    catalog_file = catalog_path(root)
    cache_file = translated_tree_cache_path(root)
    values = _load_catalog_cached(
        str(catalog_file),
        catalog_file.stat().st_mtime_ns if catalog_file.is_file() else 0,
        str(cache_file) if cache_file.is_file() else "",
        cache_file.stat().st_mtime_ns if cache_file.is_file() else 0,
    )
    return [dict(item) for item in values]


def get_category(root: Path, category_id: int, type_id: int) -> Dict[str, Any]:
    match = next(
        (item for item in load_catalog(root) if item["category_id"] == category_id and item["type_id"] == type_id),
        None,
    )
    if match is None:
        raise ValueError("所选category_id/type_id不是当前本地Ozon类目树中的有效叶子")
    if not match.get("name_zh") or not match.get("path_zh") or match.get("source") != "official_ozon_seller_api_zh_hans":
        raise ValueError("Ozon官方简体中文类目尚未同步，禁止使用本地翻译或猜测类目")
    return match


def load_preferences(root: Path) -> Dict[str, Any]:
    value = load_json(preferences_path(root), {"schema_version": "1.0.0", "favorites": [], "recent": []})
    value.setdefault("favorites", [])
    value.setdefault("recent", [])
    return value


def _preference_categories(root: Path, records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    catalog = {category_key(item["category_id"], item["type_id"]): item for item in load_catalog(root)}
    values = []
    for record in records:
        key = category_key(int(record["category_id"]), int(record["type_id"]))
        if key in catalog:
            values.append({**catalog[key], "selected_at": record.get("selected_at")})
    return values


def public_preferences(root: Path) -> Dict[str, Any]:
    value = load_preferences(root)
    return {
        "favorites": _preference_categories(root, value["favorites"]),
        "recent": _preference_categories(root, value["recent"]),
    }


def set_favorite(root: Path, category_id: int, type_id: int, favorite: bool) -> Dict[str, Any]:
    get_category(root, category_id, type_id)
    value = load_preferences(root)
    key = category_key(category_id, type_id)
    value["favorites"] = [
        item for item in value["favorites"]
        if category_key(int(item["category_id"]), int(item["type_id"])) != key
    ]
    if favorite:
        value["favorites"].insert(0, {"category_id": category_id, "type_id": type_id, "selected_at": now()})
    value["updated_at"] = now()
    write_json(preferences_path(root), value)
    return public_preferences(root)


def record_recent(root: Path, category_id: int, type_id: int) -> None:
    value = load_preferences(root)
    key = category_key(category_id, type_id)
    recent = [
        item for item in value["recent"]
        if category_key(int(item["category_id"]), int(item["type_id"])) != key
    ]
    recent.insert(0, {"category_id": category_id, "type_id": type_id, "selected_at": now()})
    value["recent"] = recent[:20]
    value["updated_at"] = now()
    write_json(preferences_path(root), value)


def _expanded_query(root: Path, query: str) -> tuple[str, List[str]]:
    normalized = " ".join(query.casefold().split())
    aliases = load_json(alias_path(root), {}).get("aliases") or {}
    expansions = []
    stopwords = {"для", "и", "в", "на", "с", "по"}
    for chinese, russian in aliases.items():
        if chinese in query:
            phrase = " ".join(str(russian).casefold().split())
            if phrase:
                expansions.append(phrase)
                expansions.extend(token for token in phrase.split() if token not in stopwords and len(token) > 2)
    return normalized, list(dict.fromkeys(expansions))


def search_categories(root: Path, query: str, limit: int = 30) -> List[Dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        return public_preferences(root)["recent"][:limit]
    normalized, expansions = _expanded_query(root, query)
    terms = [normalized, *expansions]
    numeric = "".join(char for char in query if char.isdigit())
    ranked = []
    for item in load_catalog(root):
        if item.get("source") != "official_ozon_seller_api_zh_hans":
            continue
        haystack = " ".join([
            item["name_ru"], item.get("name_zh") or "", *item["path"], *(item.get("path_zh") or [])
        ]).casefold()
        matched = [term for term in terms if term and term in haystack]
        id_match = numeric and numeric in {str(item["category_id"]), str(item["type_id"])}
        if not matched and not id_match:
            continue
        exact_name = normalized in {item["name_ru"].casefold(), str(item.get("name_zh") or "").casefold()}
        score = (100 if id_match else 0) + (50 if exact_name else 0) + len(matched) * 10
        score -= len(item["path"]) * 0.01
        ranked.append((score, item, "id" if id_match else "中文关键词" if expansions else "俄文或路径"))
    ranked.sort(key=lambda row: (-row[0], row[1]["name_ru"], row[1]["type_id"]))
    if expansions and ranked:
        relevance_floor = max(10, ranked[0][0] - 15)
        ranked = [row for row in ranked if row[0] >= relevance_floor]
    return [{**item, "matched_by": matched_by} for _, item, matched_by in ranked[:max(1, min(limit, 100))]]


def recommend_categories(root: Path, product_text: str) -> List[Dict[str, Any]]:
    """Return at most three deterministic candidates; the user still must choose."""
    return search_categories(root, product_text, limit=3)[:3]


@lru_cache(maxsize=8)
def _load_tree_cache_cached(cache_file: str, cache_mtime_ns: int, catalog_file: str, catalog_mtime_ns: int) -> Dict[str, Any]:
    cache = load_json(Path(cache_file), {})
    if not _official_zh_cache(cache):
        return {}
    current_catalog_hash = hashlib.sha256(Path(catalog_file).read_bytes()).hexdigest()
    if cache.get("catalog_sha256") != current_catalog_hash:
        return {}
    return cache


def load_translated_tree_cache(root: Path) -> Dict[str, Any]:
    """Return only the official Ozon ZH_HANS cache.

    The historical function name is kept for compatibility with existing
    callers; locally translated caches are deliberately rejected.
    """
    cache_file = translated_tree_cache_path(root)
    catalog_file = catalog_path(root)
    if not cache_file.is_file() or not catalog_file.is_file():
        return {}
    return _load_tree_cache_cached(
        str(cache_file), cache_file.stat().st_mtime_ns, str(catalog_file), catalog_file.stat().st_mtime_ns
    )


def category_tree_children(root: Path, parent_id: str = "root") -> List[Dict[str, Any]]:
    """Return one level from the exact official Ozon Simplified Chinese tree."""
    translated_cache = load_translated_tree_cache(root)
    if translated_cache:
        children = translated_cache["children_by_parent"]
        if parent_id not in children:
            raise ValueError("类目树节点不存在或不是可展开分支")
        return [dict(item) for item in children[parent_id]]
    raise ValueError("Ozon官方简体中文类目尚未同步，禁止显示本地翻译类目")


def _cache_dir(root: Path, shop_id: str, category_id: int, type_id: int) -> Path:
    return root / "ozon-adapter/metadata/live-category-cache" / shop_id / f"category-{category_id}-type-{type_id}"


@lru_cache(maxsize=8)
def _load_collector_rules_cache_cached(cache_file: str, cache_mtime_ns: int) -> Dict[str, Any]:
    return load_json(Path(cache_file), {})


def bundled_collector_rules(root: Path, category_id: int, type_id: int, shop_id: str) -> Optional[Dict[str, Any]]:
    cache_file = collector_rules_cache_path(root)
    if not cache_file.is_file():
        return None
    cache = _load_collector_rules_cache_cached(str(cache_file), cache_file.stat().st_mtime_ns)
    rules = (cache.get("rules_by_key") or {}).get(category_key(category_id, type_id))
    if not isinstance(rules, dict):
        return None
    return {
        **rules,
        "shop_id": shop_id,
        "cache_version": cache.get("cache_version") or "unknown",
        "cache_hit": True,
        "offline_fallback": True,
        "ozon_read_api_calls": 0,
        "ozon_write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def _raw_attributes(response: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    result = response.get("result")
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict) and isinstance(result.get("attributes"), list):
        return [item for item in result["attributes"] if isinstance(item, dict)]
    return []


def _values_from_cache(path: Path) -> List[Dict[str, Any]]:
    page = load_json(path, {})
    values = page.get("values") if isinstance(page, dict) else []
    if not isinstance(values, list):
        values = (page.get("result") or []) if isinstance(page, dict) else []
    return [
        {"id": _positive_int(item.get("id")), "value": str(item.get("value") or item.get("name") or "").strip()}
        for item in values if isinstance(item, dict) and _positive_int(item.get("id")) and str(item.get("value") or item.get("name") or "").strip()
    ]


def prepare_rules(
    root: Path,
    category_id: int,
    type_id: int,
    shop_id: str = DEFAULT_SHOP,
    allow_fetch: bool = False,
    client: Any = None,
) -> Dict[str, Any]:
    category = get_category(root, category_id, type_id)
    cache_dir = _cache_dir(root, shop_id, category_id, type_id)
    attributes_path = cache_dir / "attributes.json"
    cache_hit = attributes_path.is_file()
    read_calls = 0
    if not cache_hit:
        bundled = bundled_collector_rules(root, category_id, type_id, shop_id)
        if bundled is not None:
            return {
                **bundled,
                "category_name_ru": category["name_ru"],
                "category_name_zh": category["name_zh"],
                "category_path": category["path"],
                "category_path_zh": category["path_zh"],
                "category_label_source": "ozon_seller_api",
                "category_label_language": "ZH_HANS",
            }
        if not allow_fetch:
            raise FileNotFoundError("该类目的官方属性规则尚未缓存，请先执行只读规则加载")
        if client is None:
            import sys
            sys.path.insert(0, str(root / "ozon-adapter"))
            from ozon_adapter import OzonConfig, OzonReadOnlyClient
            client = OzonReadOnlyClient(OzonConfig.from_shop(shop_id, root / "ozon-adapter/shops.json"))
        response = client.get_category_attributes(category_id, type_id)
        read_calls += 1
        write_json(attributes_path, response)
    response = load_json(attributes_path, {})
    attributes = []
    for raw in _raw_attributes(response):
        attribute_id = _positive_int(raw.get("id", raw.get("attribute_id")))
        name = str(raw.get("name") or raw.get("attribute_name") or "").strip()
        if attribute_id is None or not name:
            continue
        required = bool(raw.get("is_required", raw.get("required", False)))
        is_aspect = bool(raw.get("is_aspect", False))
        dictionary_id = _positive_int(raw.get("dictionary_id"))
        values_path = cache_dir / f"attribute-{attribute_id}-values.json"
        if dictionary_id and (required or is_aspect) and not values_path.is_file() and allow_fetch:
            if client is None:
                import sys
                sys.path.insert(0, str(root / "ozon-adapter"))
                from ozon_adapter import OzonConfig, OzonReadOnlyClient
                client = OzonReadOnlyClient(OzonConfig.from_shop(shop_id, root / "ozon-adapter/shops.json"))
            page = client.get_attribute_values(category_id, type_id, attribute_id)
            read_calls += 1
            write_json(values_path, page)
        attributes.append({
            "attribute_id": attribute_id,
            "attribute_name": name,
            "required": required,
            "is_aspect": is_aspect,
            "type": str(raw.get("type") or "unknown"),
            "dictionary_id": dictionary_id,
            "is_collection": bool(raw.get("is_collection", False)),
            "allowed_values": _values_from_cache(values_path) if values_path.is_file() else [],
        })
    if not attributes:
        raise ValueError("官方类目属性响应为空，禁止完成采集")
    snapshot_payload = {
        "category_id": category_id,
        "type_id": type_id,
        "category_path": category["path"],
        "attributes": attributes,
    }
    digest = hashlib.sha256(json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "schema_version": "1.0.0",
        **snapshot_payload,
        "category_name_ru": category["name_ru"],
        "category_name_zh": category["name_zh"],
        "category_path_zh": category["path_zh"],
        "category_label_source": "ozon_seller_api",
        "category_label_language": "ZH_HANS",
        "shop_id": shop_id,
        "rules_source": "ozon_seller_api_cache",
        "rules_snapshot_hash": digest,
        "required_attribute_ids": [item["attribute_id"] for item in attributes if item["required"]],
        "aspect_attribute_ids": [item["attribute_id"] for item in attributes if item["is_aspect"]],
        "captured_at": now(),
        "cache_hit": read_calls == 0,
        "ozon_read_api_calls": read_calls,
        "ozon_write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def build_selection(
    root: Path,
    payload: Mapping[str, Any],
    preferences_root: Optional[Path] = None,
) -> Dict[str, Any]:
    selected = payload.get("ozon_category_selection")
    if not isinstance(selected, dict):
        raise ValueError("未选择最终Ozon类目，禁止完成采集")
    category_id = _positive_int(selected.get("category_id"))
    type_id = _positive_int(selected.get("type_id"))
    if category_id is None or type_id is None:
        raise ValueError("最终Ozon类目必须同时包含有效category_id和type_id")
    category = get_category(root, category_id, type_id)
    rules = selected.get("rules_snapshot")
    if not isinstance(rules, dict):
        raise ValueError("最终Ozon类目缺少本地规则快照，禁止完成采集")
    if rules.get("category_id") != category_id or rules.get("type_id") != type_id:
        raise ValueError("类目与规则快照不一致，禁止完成采集")
    if not isinstance(rules.get("attributes"), list) or not rules["attributes"]:
        raise ValueError("类目规则快照没有官方属性，禁止完成采集")
    if list(rules.get("category_path") or []) != list(category["path"]):
        raise ValueError("类目路径与当前本地Ozon类目树不一致，禁止完成采集")
    expected_required = [item.get("attribute_id") for item in rules["attributes"] if item.get("required") is True]
    expected_aspects = [item.get("attribute_id") for item in rules["attributes"] if item.get("is_aspect") is True]
    if list(rules.get("required_attribute_ids") or []) != expected_required:
        raise ValueError("类目必填属性快照不一致，禁止完成采集")
    if list(rules.get("aspect_attribute_ids") or []) != expected_aspects:
        raise ValueError("类目is_aspect快照不一致，禁止完成采集")
    digest_payload = {
        "category_id": category_id,
        "type_id": type_id,
        "category_path": category["path"],
        "attributes": rules["attributes"],
    }
    expected_hash = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if rules.get("rules_snapshot_hash") != expected_hash:
        raise ValueError("类目规则快照哈希无效，请重新加载官方规则")
    selection = {
        "schema_version": "1.0.0",
        "selection_source": "user_final_choice",
        "selected_at": str(selected.get("selected_at") or now()),
        "category_id": category_id,
        "type_id": type_id,
        "category_name_ru": category["name_ru"],
        "category_name_zh": category["name_zh"],
        "category_path": category["path"],
        "category_path_zh": category["path_zh"],
        "category_label_source": "ozon_seller_api",
        "category_label_language": "ZH_HANS",
        "rules_snapshot": rules,
        "rules_snapshot_hash": expected_hash,
        "locked_for_batch": True,
        "allow_runtime_rematch": False,
    }
    record_recent(preferences_root or root, category_id, type_id)
    return selection
