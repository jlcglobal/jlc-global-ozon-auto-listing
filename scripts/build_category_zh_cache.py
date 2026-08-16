#!/usr/bin/env python3
"""Synchronize the exact Simplified Chinese Ozon category tree.

The Chinese labels come only from Ozon Seller API ``ZH_HANS`` responses.  The
Russian and Chinese trees are joined by ``(description_category_id, type_id)``;
text translation, fuzzy matching and label normalization are intentionally
forbidden.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "ozon-adapter/metadata/ozon-rules-2026-07-10/categories.json"
ALIASES_PATH = ROOT / "config/ozon-category-search-aliases.json"
CACHE_PATH = ROOT / "ozon-adapter/metadata/ozon-rules-2026-07-10/category-tree.zh-CN.json"
EXTENSION_CACHE_PATH = ROOT / "collector/edge-extension/category-tree.zh-CN.json"
ENDPOINT = "/v1/description-category/tree"
OFFICIAL_ZH_LANGUAGE = "ZH_HANS"
OFFICIAL_RU_LANGUAGE = "RU"


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def branch_id(path: Iterable[str]) -> str:
    value = "\x1f".join(path)
    return f"branch-{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _leaf_index(response: Mapping[str, Any]) -> Dict[Tuple[int, int], Dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "ozon-adapter"))
    from ozon_adapter.service import flatten_category_tree

    rows = flatten_category_tree(dict(response))
    result: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for row in rows:
        category_id = row.get("category_id")
        type_id = row.get("type_id")
        if not category_id or not type_id or row.get("disabled"):
            continue
        key = (int(category_id), int(type_id))
        if key in result:
            raise ValueError(f"Ozon官方类目树包含重复叶子：{key[0]}:{key[1]}")
        path = [str(part).strip() for part in row.get("path") or [] if str(part).strip()]
        if not path:
            raise ValueError(f"Ozon官方类目缺少路径：{key[0]}:{key[1]}")
        result[key] = {**row, "path": path}
    if not result:
        raise ValueError("Ozon官方类目树没有可用叶子")
    return result


def _catalog_keys(catalog: List[Dict[str, Any]]) -> set[Tuple[int, int]]:
    return {(int(item["categoryId"]), int(item["typeId"])) for item in catalog}


def build_official_cache(
    catalog: List[Dict[str, Any]],
    ru_response: Mapping[str, Any],
    zh_response: Mapping[str, Any],
    *,
    shop_id: str,
    generated_at: str | None = None,
    strict_catalog: bool = True,
) -> Dict[str, Any]:
    ru_items = _leaf_index(ru_response)
    zh_items = _leaf_index(zh_response)
    catalog_keys = _catalog_keys(catalog)
    ru_keys = set(ru_items)
    zh_keys = set(zh_items)
    if ru_keys != zh_keys:
        raise ValueError(
            f"Ozon俄文与中文官方类目ID不一致：仅俄文{len(ru_keys - zh_keys)}，仅中文{len(zh_keys - ru_keys)}"
        )
    if strict_catalog and ru_keys != catalog_keys:
        raise ValueError(
            f"Ozon官方类目与本地属性规则ID不一致：仅官方{len(ru_keys - catalog_keys)}，仅本地{len(catalog_keys - ru_keys)}"
        )
    selected_keys = catalog_keys if strict_catalog else ru_keys

    children: Dict[str, Dict[str, Dict[str, Any]]] = {"root": {}}
    search_items: List[Dict[str, Any]] = []
    for category_id, type_id in sorted(selected_keys):
        ru = ru_items[(category_id, type_id)]
        zh = zh_items[(category_id, type_id)]
        path_ru = ru["path"]
        path_zh = zh["path"]
        if len(path_ru) != len(path_zh):
            raise ValueError(
                f"Ozon官方中俄类目层级不一致：{category_id}:{type_id}，俄文{len(path_ru)}层，中文{len(path_zh)}层"
            )
        name_ru = str(ru.get("type_name") or path_ru[-1]).strip()
        name_zh = str(zh.get("type_name") or path_zh[-1]).strip()
        if not name_ru or not name_zh:
            raise ValueError(f"Ozon官方类目名称为空：{category_id}:{type_id}")
        search_items.append({
            "category_id": category_id,
            "type_id": type_id,
            "name_ru": name_ru,
            "name_zh": name_zh,
            "path": path_ru,
            "path_zh": path_zh,
            "label_source": "ozon_seller_api",
            "label_language": OFFICIAL_ZH_LANGUAGE,
        })

        parent = "root"
        for index, (ru_name, zh_name) in enumerate(zip(path_ru, path_zh)):
            ru_prefix = path_ru[:index + 1]
            zh_prefix = path_zh[:index + 1]
            is_leaf = index == len(path_ru) - 1
            if is_leaf:
                node_id = f"leaf-{category_id}-{type_id}"
                node = {
                    "node_id": node_id,
                    "parent_id": parent,
                    "kind": "leaf",
                    "name_ru": name_ru,
                    "name_zh": name_zh,
                    "path": path_ru,
                    "path_zh": path_zh,
                    "depth": index,
                    "has_children": False,
                    "category_id": category_id,
                    "type_id": type_id,
                    "label_source": "ozon_seller_api",
                }
            else:
                node_id = branch_id(ru_prefix)
                node = {
                    "node_id": node_id,
                    "parent_id": parent,
                    "kind": "branch",
                    "name_ru": ru_name,
                    "name_zh": zh_name,
                    "path": ru_prefix,
                    "path_zh": zh_prefix,
                    "depth": index,
                    "has_children": True,
                    "label_source": "ozon_seller_api",
                }
            existing = children.setdefault(parent, {}).get(node_id)
            if existing and existing != node:
                raise ValueError(f"Ozon官方类目分支映射冲突：{node_id}")
            children[parent][node_id] = node
            if not is_leaf:
                children.setdefault(node_id, {})
                parent = node_id

    children_by_parent = {
        parent: sorted(nodes.values(), key=lambda item: (item["kind"] == "leaf", item["name_zh"], item["node_id"]))
        for parent, nodes in children.items()
    }
    catalog_hash = hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()
    ru_hash = canonical_hash(ru_response)
    zh_hash = canonical_hash(zh_response)
    cache_version = hashlib.sha256(f"{catalog_hash}:{ru_hash}:{zh_hash}".encode()).hexdigest()[:16]
    return {
        "schema_version": "2.0.0",
        "locale": "zh-CN",
        "source": "ozon_seller_api",
        "api_endpoint": ENDPOINT,
        "api_language": OFFICIAL_ZH_LANGUAGE,
        "russian_language": OFFICIAL_RU_LANGUAGE,
        "official_labels_required": True,
        "shop_id": shop_id,
        "cache_version": cache_version,
        "generated_at": generated_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "catalog_sha256": catalog_hash,
        "ru_response_sha256": ru_hash,
        "zh_response_sha256": zh_hash,
        "item_count": len(search_items),
        "catalog_compatibility": "strict" if strict_catalog else "runtime_live_tree",
        "search_aliases": load_json(ALIASES_PATH, {}).get("aliases") or {},
        "children_by_parent": children_by_parent,
        "search_items": search_items,
    }


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"店铺只读密钥文件不存在：{path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fetch_official_trees(shop_id: str, env_file: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    _load_env_file(env_file)
    sys.path.insert(0, str(ROOT / "ozon-adapter"))
    from ozon_adapter import OzonConfig, OzonReadOnlyClient

    client = OzonReadOnlyClient(OzonConfig.from_shop(shop_id, ROOT / "ozon-adapter/shops.json"))
    return client.get_category_tree(OFFICIAL_RU_LANGUAGE), client.get_category_tree(OFFICIAL_ZH_LANGUAGE)


def main() -> int:
    parser = argparse.ArgumentParser(description="同步Ozon官方简体中文类目树（只读）")
    parser.add_argument("--shop", default="zhonglian1")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--ru-response", type=Path, help="使用已保存的Ozon RU响应，不联网")
    parser.add_argument("--zh-response", type=Path, help="使用已保存的Ozon ZH_HANS响应，不联网")
    parser.add_argument("--check-only", action="store_true", help="只校验，不写入缓存")
    args = parser.parse_args()

    if bool(args.ru_response) != bool(args.zh_response):
        parser.error("--ru-response 与 --zh-response 必须同时提供")
    if args.ru_response:
        ru_response = load_json(args.ru_response, {})
        zh_response = load_json(args.zh_response, {})
    else:
        env_file = args.env_file or ROOT / f"ozon-adapter/.env.{args.shop}"
        ru_response, zh_response = fetch_official_trees(args.shop, env_file)
    cache = build_official_cache(
        load_json(CATALOG_PATH, []), ru_response, zh_response, shop_id=args.shop
    )
    if not args.check_only:
        write_json(CACHE_PATH, cache)
        write_json(EXTENSION_CACHE_PATH, cache)
    mode = "validated" if args.check_only else "synchronized"
    print(
        f"official category cache {mode}: version={cache['cache_version']} "
        f"items={cache['item_count']} source={cache['source']} language={cache['api_language']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
