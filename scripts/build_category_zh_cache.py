"""Build the versioned local zh-CN Ozon category cache.

Runtime code never imports a translator. Translation is an optional one-time
development step; the collector and Edge extension only read the generated JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "ozon-adapter/metadata/ozon-rules-2026-07-10/categories.json"
TRANSLATIONS_PATH = ROOT / "config/ozon-category-translations-zh.json"
OVERRIDES_PATH = ROOT / "config/ozon-category-translation-overrides.json"
ALIASES_PATH = ROOT / "config/ozon-category-search-aliases.json"
CACHE_PATH = ROOT / "ozon-adapter/metadata/ozon-rules-2026-07-10/category-tree.zh-CN.json"
EXTENSION_CACHE_PATH = ROOT / "collector/edge-extension/category-tree.zh-CN.json"


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


def normalize_translation(value: str, source: str) -> str:
    value = re.sub(r"^(货物|产品|商品|物品)\s*[:：]\s*", "", value.strip()).strip()
    if not value or value == source or re.search(r"[А-Яа-яЁё]", value):
        value = "其他商品类目"
    if not re.search(r"[\u3400-\u9fff]", value):
        value = f"相关商品（{value}）"
    return value


def translate_missing(labels: List[str], translations: Dict[str, str], batch_size: int = 20) -> None:
    from argostranslate import translate

    missing = [label for label in labels if not translations.get(label)]
    for offset in range(0, len(missing), batch_size):
        batch = missing[offset:offset + batch_size]
        prompt = "\n".join(f"[{index}] Товар: {label}" for index, label in enumerate(batch))
        output = translate.translate(prompt, "ru", "zh")
        found: Dict[int, str] = {}
        for line in output.splitlines():
            match = re.match(r"^\[(\d+)\]\s*(.+)$", line.strip())
            if match:
                found[int(match.group(1))] = match.group(2).strip()
        for index, label in enumerate(batch):
            translated = found.get(index)
            if not translated:
                translated = translate.translate(f"Товар: {label}", "ru", "zh")
            translations[label] = normalize_translation(translated, label)
        if offset % 200 == 0 or offset + len(batch) >= len(missing):
            write_translation_file(translations)
            print(f"translated {min(offset + len(batch), len(missing))}/{len(missing)}", flush=True)


def write_translation_file(translations: Dict[str, str]) -> None:
    overrides = load_json(OVERRIDES_PATH, {}).get("translations") or {}
    merged = {source: normalize_translation(value, source) for source, value in translations.items()}
    merged.update(overrides)
    write_json(TRANSLATIONS_PATH, {
        "schema_version": "1.0.0",
        "locale": "zh-CN",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "offline_ru_en_zh_with_manual_overrides",
        "translations": dict(sorted(merged.items())),
    })


def build_cache(catalog: List[Dict[str, Any]], translations: Dict[str, str]) -> Dict[str, Any]:
    children: Dict[str, Dict[str, Dict[str, Any]]] = {"root": {}}
    search_items = []
    for raw in catalog:
        category_id = int(raw["categoryId"])
        type_id = int(raw["typeId"])
        path_ru = [str(part).strip() for part in raw.get("categoryPath") or [] if str(part).strip()]
        name_ru = str(raw.get("nameRu") or path_ru[-1]).strip()
        path_zh = [translations[part] for part in path_ru]
        name_zh = translations[name_ru]
        search_items.append({
            "category_id": category_id,
            "type_id": type_id,
            "name_ru": name_ru,
            "name_zh": name_zh,
            "path": path_ru,
            "path_zh": path_zh,
        })
        parent = "root"
        for index, name in enumerate(path_ru):
            prefix = path_ru[:index + 1]
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
                }
            else:
                node_id = branch_id(prefix)
                node = {
                    "node_id": node_id,
                    "parent_id": parent,
                    "kind": "branch",
                    "name_ru": name,
                    "name_zh": translations[name],
                    "path": prefix,
                    "path_zh": path_zh[:index + 1],
                    "depth": index,
                    "has_children": True,
                }
            children.setdefault(parent, {})[node_id] = node
            if not is_leaf:
                children.setdefault(node_id, {})
                parent = node_id
    children_by_parent = {
        parent: sorted(nodes.values(), key=lambda item: (item["kind"] == "leaf", item["name_zh"], item["node_id"]))
        for parent, nodes in children.items()
    }
    source_hash = hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()
    translation_hash = hashlib.sha256(TRANSLATIONS_PATH.read_bytes()).hexdigest()
    cache_version = hashlib.sha256(f"{source_hash}:{translation_hash}".encode()).hexdigest()[:16]
    return {
        "schema_version": "1.0.0",
        "locale": "zh-CN",
        "cache_version": cache_version,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "catalog_sha256": source_hash,
        "translation_sha256": translation_hash,
        "item_count": len(search_items),
        "search_aliases": load_json(ALIASES_PATH, {}).get("aliases") or {},
        "children_by_parent": children_by_parent,
        "search_items": search_items,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translate-missing", action="store_true")
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    catalog = load_json(CATALOG_PATH, [])
    labels = sorted({label for item in catalog for label in [*(item.get("categoryPath") or []), item.get("nameRu")] if label})
    translations = dict(load_json(TRANSLATIONS_PATH, {}).get("translations") or {})
    translations.update(load_json(OVERRIDES_PATH, {}).get("translations") or {})
    if args.translate_missing:
        translate_missing(labels, translations, max(1, min(args.batch_size, 50)))
    missing = [label for label in labels if not translations.get(label)]
    if missing:
        print(f"missing translations: {len(missing)}; rerun with --translate-missing")
        return 2
    write_translation_file(translations)
    translations = load_json(TRANSLATIONS_PATH, {})["translations"]
    cache = build_cache(catalog, translations)
    write_json(CACHE_PATH, cache)
    write_json(EXTENSION_CACHE_PATH, cache)
    print(f"cache {cache['cache_version']}: {cache['item_count']} items, {len(cache['children_by_parent'])} parents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
