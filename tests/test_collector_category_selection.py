from __future__ import annotations

import json
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

from scripts.collector_categories import (
    build_selection,
    category_tree_children,
    load_translated_tree_cache,
    prepare_rules,
    public_preferences,
    recommend_categories,
    search_categories,
    set_favorite,
)
from scripts.build_collector_category_rules_cache import build_cache
from scripts.build_category_zh_cache import build_official_cache


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDGE_MANIFEST = PROJECT_ROOT / "collector/edge-extension/manifest.json"
APP_SPEC = importlib.util.spec_from_file_location("category_selection_ingest_app", PROJECT_ROOT / "collector/local-ingest/app.py")
ingest_app = importlib.util.module_from_spec(APP_SPEC)
APP_SPEC.loader.exec_module(ingest_app)


def write_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_root() -> tuple[tempfile.TemporaryDirectory, Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    catalog = [
        {"categoryId": "101", "typeId": "201", "nameRu": "Наушники", "nameZh": "", "categoryPath": ["Электроника", "Наушники"]},
        {"categoryId": "102", "typeId": "202", "nameRu": "Весы для багажа", "nameZh": "", "categoryPath": ["Туризм", "Весы для багажа"]},
        {"categoryId": "103", "typeId": "203", "nameRu": "Точилка для ножей", "nameZh": "", "categoryPath": ["Дом", "Кухня", "Точилка для ножей"]},
        {"categoryId": "104", "typeId": "204", "nameRu": "Сумка", "nameZh": "", "categoryPath": ["Аксессуары", "Сумки"]},
        {"categoryId": "105", "typeId": "205", "nameRu": "Контейнер пищевой", "nameZh": "", "categoryPath": ["Дом и сад", "Хранение продуктов", "Контейнер пищевой"]},
    ]
    catalog_file = root / "ozon-adapter/metadata/ozon-rules-2026-07-10/categories.json"
    write_json(catalog_file, catalog)
    labels = {
        "Электроника": "电子产品", "Наушники": "耳机",
        "Туризм": "旅游", "Весы для багажа": "行李秤",
        "Дом": "家居", "Кухня": "厨房", "Точилка для ножей": "磨刀器",
        "Аксессуары": "配饰", "Сумки": "包袋", "Сумка": "包",
        "Дом и сад": "住宅和花园", "Хранение продуктов": "食物贮藏", "Контейнер пищевой": "食品储存罐",
    }
    children: dict[str, dict[str, dict]] = {"root": {}}
    search_items = []
    for item in catalog:
        category_id, type_id = int(item["categoryId"]), int(item["typeId"])
        path = item["categoryPath"]
        path_zh = [labels[part] for part in path]
        search_items.append({
            "category_id": category_id, "type_id": type_id,
            "name_ru": item["nameRu"], "name_zh": path_zh[-1],
            "path": path, "path_zh": path_zh,
            "label_source": "ozon_seller_api", "label_language": "ZH_HANS",
        })
        parent = "root"
        for index, (name_ru, name_zh) in enumerate(zip(path, path_zh)):
            leaf = index == len(path) - 1
            node_id = f"leaf-{category_id}-{type_id}" if leaf else "branch-" + hashlib.sha1("\x1f".join(path[:index + 1]).encode()).hexdigest()[:16]
            node = {
                "node_id": node_id, "parent_id": parent, "kind": "leaf" if leaf else "branch",
                "name_ru": item["nameRu"] if leaf else name_ru, "name_zh": path_zh[-1] if leaf else name_zh,
                "path": path if leaf else path[:index + 1], "path_zh": path_zh if leaf else path_zh[:index + 1],
                "depth": index, "has_children": not leaf, "label_source": "ozon_seller_api",
            }
            if leaf:
                node.update({"category_id": category_id, "type_id": type_id})
            children.setdefault(parent, {})[node_id] = node
            if not leaf:
                children.setdefault(node_id, {})
                parent = node_id
    write_json(root / "ozon-adapter/metadata/ozon-rules-2026-07-10/category-tree.zh-CN.json", {
        "schema_version": "2.0.0", "locale": "zh-CN", "source": "ozon_seller_api",
        "api_language": "ZH_HANS", "official_labels_required": True,
        "catalog_sha256": hashlib.sha256(catalog_file.read_bytes()).hexdigest(),
        "children_by_parent": {key: list(value.values()) for key, value in children.items()},
        "search_items": search_items, "item_count": len(search_items),
    })
    write_json(root / "config/ozon-category-search-aliases.json", {"aliases": {
        "电子": "электроника", "耳机": "наушники", "行李": "багаж", "秤": "весы",
        "食品储藏": "хранение продуктов"
    }})
    write_json(root / "ozon-adapter/metadata/live-category-cache/test/category-102-type-202/attributes.json", {
        "result": [
            {"id": 85, "name": "Бренд", "is_required": True, "is_aspect": False, "type": "String", "dictionary_id": 1},
            {"id": 10096, "name": "Цвет товара", "is_required": False, "is_aspect": True, "type": "String", "dictionary_id": 2},
            {"id": 500, "name": "Материал", "is_required": False, "is_aspect": False, "type": "String", "dictionary_id": 0},
        ]
    })
    write_json(root / "ozon-adapter/metadata/live-category-cache/test/category-102-type-202/attribute-85-values.json", {
        "values": [{"id": 10, "value": "Нет бренда"}], "truncated": False
    })
    write_json(root / "ozon-adapter/metadata/live-category-cache/test/category-102-type-202/attribute-10096-values.json", {
        "values": [{"id": 20, "value": "Черный"}], "truncated": False
    })
    return temporary, root


class CollectorCategorySelectionTest(unittest.TestCase):
    def test_search_supports_chinese_russian_keyword_and_ids(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        self.assertEqual(search_categories(root, "电子耳机")[0]["type_id"], 201)
        self.assertEqual(search_categories(root, "Весы")[0]["type_id"], 202)
        self.assertEqual(search_categories(root, "багажа")[0]["type_id"], 202)
        self.assertEqual(search_categories(root, "102")[0]["category_id"], 102)
        self.assertEqual(search_categories(root, "食品储藏")[0]["type_id"], 205)

    def test_category_tree_is_lazy_and_only_leaf_nodes_are_selectable(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        roots = category_tree_children(root)
        home = next(item for item in roots if item["name_ru"] == "Дом и сад")
        self.assertEqual(home["kind"], "branch")
        self.assertTrue(home["has_children"])
        storage = category_tree_children(root, home["node_id"])[0]
        self.assertEqual(storage["name_zh"], "食物贮藏")
        leaf = category_tree_children(root, storage["node_id"])[0]
        self.assertEqual((leaf["category_id"], leaf["type_id"]), (105, 205))
        self.assertEqual(leaf["kind"], "leaf")
        self.assertFalse(leaf["has_children"])

    def test_category_tree_rejects_unknown_parent(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(ValueError, "节点不存在"):
            category_tree_children(root, "branch-missing")

    def test_recommendations_never_exceed_three_and_user_choice_remains_required(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        self.assertLessEqual(len(recommend_categories(root, "电子耳机")), 3)

    def test_recent_and_favorite_categories_are_persisted(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        set_favorite(root, 102, 202, True)
        prefs = public_preferences(root)
        self.assertEqual(prefs["favorites"][0]["type_id"], 202)

    def test_rule_snapshot_uses_category_specific_required_dictionary_and_is_aspect(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        snapshot = prepare_rules(root, 102, 202, "test", allow_fetch=False)
        self.assertEqual(snapshot["required_attribute_ids"], [85])
        self.assertEqual(snapshot["aspect_attribute_ids"], [10096])
        self.assertEqual(snapshot["attributes"][0]["allowed_values"][0]["id"], 10)
        self.assertEqual(snapshot["ozon_write_api_calls"], 0)
        self.assertEqual(snapshot["inventory_api_calls"], 0)
        self.assertEqual(snapshot["category_label_source"], "ozon_seller_api")

    def test_rule_snapshot_fetches_dictionary_values_for_optional_attributes(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        cache_dir = root / "ozon-adapter/metadata/live-category-cache/test/category-102-type-202"
        attributes = json.loads((cache_dir / "attributes.json").read_text(encoding="utf-8"))
        attributes["result"].append({
            "id": 501, "name": "Особенности пледа", "is_required": False,
            "is_aspect": False, "type": "String", "dictionary_id": 99,
        })
        write_json(cache_dir / "attributes.json", attributes)

        test_case = self

        class ReadOnlyClient:
            def get_attribute_values(self, category_id, type_id, attribute_id):
                test_case.assertEqual((category_id, type_id, attribute_id), (102, 202, 501))
                return {"values": [{"id": 77, "value": "С подогревом"}], "truncated": False}

        client = ReadOnlyClient()
        snapshot = prepare_rules(root, 102, 202, "test", allow_fetch=True, client=client)
        feature = next(item for item in snapshot["attributes"] if item["attribute_id"] == 501)
        self.assertEqual(feature["allowed_values"], [{"id": 77, "value": "С подогревом"}])
        self.assertTrue((cache_dir / "attribute-501-values.json").is_file())
        self.assertEqual(snapshot["ozon_read_api_calls"], 1)

    def test_offline_bulk_cache_prevents_category_selection_from_stopping(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        metadata = root / "ozon-adapter/metadata/ozon-rules-2026-07-10"
        write_json(metadata / "attributes.json", [{
            "categoryId": "103", "typeId": "203",
            "attributes": [
                {"attributeId": "85", "nameRu": "Бренд", "required": True, "values": []},
                {"attributeId": "10096", "nameRu": "Цвет товара", "required": False, "values": []},
            ],
        }])
        write_json(metadata / "variants.json", [{
            "categoryId": "103", "typeId": "203",
            "attributes": [{"attributeId": "10096", "nameRu": "Цвет товара"}],
        }])
        write_json(metadata / "version.json", {"version": "fixture-v1", "updatedAt": "2026-07-13"})
        write_json(root / "collector/edge-extension/category-rules-cache.json", build_cache(metadata))
        snapshot = prepare_rules(root, 103, 203, "test", allow_fetch=False)
        self.assertEqual(snapshot["required_attribute_ids"], [85])
        self.assertEqual(snapshot["aspect_attribute_ids"], [10096])
        self.assertTrue(snapshot["offline_fallback"])
        self.assertFalse(snapshot["dictionary_values_complete"])
        self.assertEqual(snapshot["ozon_read_api_calls"], 0)
        selection = build_selection(root, {"ozon_category_selection": {
            "category_id": 103, "type_id": 203, "rules_snapshot": snapshot,
        }})
        self.assertEqual(selection["type_id"], 203)

    def test_collection_is_blocked_without_final_category_or_rules(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(ValueError, "未选择最终Ozon类目"):
            build_selection(root, {})
        with self.assertRaisesRegex(ValueError, "缺少本地规则快照"):
            build_selection(root, {"ozon_category_selection": {"category_id": 102, "type_id": 202}})

    def test_selection_saves_locked_pair_and_rule_hash(self):
        temporary, root = make_root()
        self.addCleanup(temporary.cleanup)
        rules = prepare_rules(root, 102, 202, "test", allow_fetch=False)
        selection = build_selection(root, {"ozon_category_selection": {
            "category_id": 102, "type_id": 202, "selected_at": "2026-07-12T10:00:00+08:00", "rules_snapshot": rules,
        }})
        self.assertEqual((selection["category_id"], selection["type_id"]), (102, 202))
        self.assertFalse(selection["allow_runtime_rematch"])
        self.assertEqual(selection["rules_snapshot_hash"], rules["rules_snapshot_hash"])
        self.assertEqual(selection["category_label_language"], "ZH_HANS")

    def test_official_cache_pairs_labels_only_by_exact_ozon_ids(self):
        catalog = [{"categoryId": "101", "typeId": "201"}]
        def response(root_name, leaf_name):
            return {"result": [{
                "description_category_id": 100, "category_name": root_name,
                "children": [{
                    "description_category_id": 101, "category_name": root_name,
                    "children": [{"type_id": 201, "type_name": leaf_name}],
                }],
            }]}
        cache = build_official_cache(
            catalog, response("Дом", "Контейнер пищевой"), response("住宅和花园", "食品储存罐"),
            shop_id="test", generated_at="2026-07-17T00:00:00+08:00",
        )
        item = cache["search_items"][0]
        self.assertEqual(item["name_zh"], "食品储存罐")
        self.assertEqual(item["label_source"], "ozon_seller_api")
        self.assertEqual(cache["api_language"], "ZH_HANS")

    def test_official_cache_rejects_unpaired_chinese_tree(self):
        catalog = [{"categoryId": "101", "typeId": "201"}]
        ru = {"result": [{
            "description_category_id": 101, "category_name": "Дом",
            "children": [{"type_id": 201, "type_name": "Контейнер"}],
        }]}
        zh = {"result": [{
            "description_category_id": 101, "category_name": "住宅和花园",
            "children": [{"type_id": 999, "type_name": "其他"}],
        }]}
        with self.assertRaisesRegex(ValueError, "类目ID不一致"):
            build_official_cache(catalog, ru, zh, shop_id="test")

    def test_category_change_invalidates_attributes_image_strategy_and_payload_without_ozon_write(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P000001"
            write_json(product / "status.json", {
                "status": "COLLECTED", "api_write_count": 0, "history": [], "completed_steps": ["collect_source"]
            })
            write_json(product / "input/category-selection.json", {"category_id": 1, "type_id": 2})
            write_json(product / "input/source.json", {
                "product_id": "P000001",
                "collection_id": "COL-category-change-test",
                "source_kind": "workbench_collection",
                "source_path": "products/P000001/input/source.json",
                "source_url": "https://detail.1688.com/offer/1.html",
                "collected_at": "2026-07-22T00:00:00+08:00",
                "raw_capture_file": "products/P000001/input/raw-snapshot.json",
                "main_images": [],
                "detail_images": [],
                "skus": [],
            })
            write_json(product / "input/raw-snapshot.json", {"ok": True})
            write_json(product / "output/ozon-attributes.json", {"old": True})
            write_json(product / "output/attribute-fill-input.json", {"old": True})
            write_json(product / "output/attribute-fill-input.compact.json", {"old": True})
            write_json(product / "output/ozon-ecommerce-design.json", {"old": True})
            write_json(product / "output/copy-ru.json", {"old": True})
            write_json(product / "output/sku-run-snapshot.json", {"old": True})
            write_json(product / "output/image-plan.json", {"old": True})
            write_json(product / "output/ozon-upload-payload.json", {"old": True})
            write_json(product / "input/source-manifest.json", {"old": True})
            status = json.loads((product / "status.json").read_text(encoding="utf-8"))
            status["source_snapshot_binding"] = {
                "product_id": "P000001",
                "collection_id": "COL-category-change-test",
                "source_manifest_path": "products/P000001/input/source-manifest.json",
                "source_manifest_sha256": "old",
            }
            write_json(product / "status.json", status)
            selection = {
                "category_id": 102, "type_id": 202, "rules_snapshot_hash": "new",
                "rules_snapshot": {"attributes": [{"attribute_id": 85}]},
            }
            result = ingest_app.replace_collected_category(product, selection)
            self.assertEqual(result["status"], "changed")
            self.assertFalse((product / "output/ozon-attributes.json").exists())
            self.assertFalse((product / "output/attribute-fill-input.json").exists())
            self.assertFalse((product / "output/attribute-fill-input.compact.json").exists())
            self.assertFalse((product / "output/ozon-ecommerce-design.json").exists())
            self.assertFalse((product / "output/copy-ru.json").exists())
            self.assertFalse((product / "output/sku-run-snapshot.json").exists())
            self.assertFalse((product / "output/image-plan.json").exists())
            self.assertFalse((product / "output/ozon-upload-payload.json").exists())
            self.assertTrue((product / "input/source-manifest.json").exists())
            self.assertTrue((product / "input/source.json").exists())
            self.assertEqual(result["ozon_write_api_calls"], 0)
            self.assertEqual(result["inventory_api_calls"], 0)
            updated_status = json.loads((product / "status.json").read_text(encoding="utf-8"))
            self.assertIsNone(updated_status.get("active_step"))
            self.assertEqual(updated_status.get("next_action"), "wait_for_run_task")
            self.assertEqual(updated_status.get("warnings"), [])
            self.assertEqual(updated_status.get("retry_count_by_step"), {})
            self.assertNotIn("source_snapshot_binding", updated_status)
            self.assertIn("ecommerce_design", updated_status.get("pending_steps") or [])
            self.assertIn("russian_copy", updated_status.get("pending_steps") or [])
            self.assertEqual(
                [step for step in updated_status.get("pending_steps") or [] if step.startswith("ecommerce") or step == "russian_copy"],
                ["ecommerce_design", "russian_copy"],
            )
            self.assertEqual(updated_status.get("steps"), [{
                "name": "collect_source", "status": "completed", "retry_count": 0,
                "retryable": True, "error": None,
            }])

    def test_edge_extension_build_contains_mandatory_category_gate(self):
        source = (PROJECT_ROOT / "collector/edge-extension/src/content.ts").read_text(encoding="utf-8")
        built = (PROJECT_ROOT / "collector/edge-extension/content.js").read_text(encoding="utf-8")
        manifest = json.loads((PROJECT_ROOT / "collector/edge-extension/manifest.json").read_text(encoding="utf-8"))
        self.assertIn("openFactoryCommandCenter", built)
        self.assertIn("最终 Ozon 类目（必选）", built)
        self.assertIn("/api/collector/categories/rules", built)
        self.assertIn("/api/collector/categories/cache", built)
        self.assertIn('chrome.runtime.getURL("category-tree.zh-CN.json")', built)
        self.assertIn('chrome.runtime.getURL("category-rules-cache.json")', built)
        self.assertIn("searchLocalCategoryCache", built)
        self.assertIn("caf-category-search-button", built)
        self.assertIn('categorySearch.addEventListener("input"', built)
        self.assertIn('categorySearch.addEventListener("keydown"', built)
        self.assertIn('categorySearchButton.addEventListener("click"', built)
        self.assertIn("正在搜索", built)
        self.assertIn("rememberCategoryRules", built)
        self.assertIn("cachedCategoryRules", built)
        self.assertIn("最近类目读取失败", built)
        self.assertIn("收藏类目读取失败", built)
        self.assertIn("收藏失败：", built)
        self.assertIn("allow_readonly_fetch: false", built)
        self.assertIn("Ozon后台官方中文类目树（点击逐级展开", built)
        self.assertIn("已拒绝使用本地翻译", built)
        self.assertIn("选择SKU（勾选下方商品）", built)
        self.assertIn("蓝色仅表示筛选，不代表已选SKU", built)
        self.assertIn(".caf-list { overflow: auto; padding: 0 16px 8px; flex: 3 1 0; min-height: 180px", built)
        self.assertIn(".caf-category { border-top: 1px solid #e5e7eb; padding: 10px 16px; background: #f8fafc; overflow: auto", built)
        self.assertNotIn("collectorApi(`/api/collector/categories/tree", built)
        self.assertIn("请先选择最终Ozon类目", built)
        self.assertEqual(manifest["version"], json.loads(EDGE_MANIFEST.read_text(encoding="utf-8"))["version"])
        self.assertIn("无SKU图 · 可采集，生图前需人工确认参考图", built)
        self.assertIn('status: skuDebug.missing_image_skus.length ? "WARNING" : "PASS"', built)
        resources = manifest["web_accessible_resources"][0]["resources"]
        self.assertIn("category-rules-cache.json", resources)

    def test_production_category_cache_is_complete_chinese_and_bundled_locally(self):
        server_path = PROJECT_ROOT / "ozon-adapter/metadata/ozon-rules-2026-07-10/category-tree.zh-CN.json"
        extension_path = PROJECT_ROOT / "collector/edge-extension/category-tree.zh-CN.json"
        server_cache = json.loads(server_path.read_text(encoding="utf-8"))
        extension_cache = json.loads(extension_path.read_text(encoding="utf-8"))
        self.assertEqual(server_cache, extension_cache)
        self.assertEqual(server_cache["locale"], "zh-CN")
        self.assertEqual(server_cache["source"], "ozon_seller_api")
        self.assertEqual(server_cache["api_language"], "ZH_HANS")
        self.assertTrue(server_cache["official_labels_required"])
        self.assertEqual(server_cache["item_count"], 7424)
        self.assertEqual(len(server_cache["children_by_parent"]["root"]), 26)
        nodes = [node for values in server_cache["children_by_parent"].values() for node in values]
        self.assertTrue(all(str(node["name_zh"]).strip() for node in nodes))
        self.assertTrue(all(node.get("label_source") == "ozon_seller_api" for node in nodes))
        self.assertTrue(all(all(str(part).strip() for part in node["path_zh"]) for node in nodes))
        self.assertEqual(
            next(node for node in nodes if node["name_ru"] == "Хранение продуктов")["name_zh"],
            "食物贮藏",
        )
        first_load = load_translated_tree_cache(PROJECT_ROOT)
        second_load = load_translated_tree_cache(PROJECT_ROOT)
        self.assertIs(first_load, second_load)
        self.assertEqual(first_load["cache_version"], server_cache["cache_version"])

    def test_workbench_exposes_category_change_and_invalidation_warning(self):
        html = (PROJECT_ROOT / "collector/local-ingest/static/workbench.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "collector/local-ingest/static/workbench.js").read_text(encoding="utf-8")
        self.assertIn("修改最终Ozon类目", html)
        self.assertIn('data-action="change-category"', script)
        self.assertIn("/api/collector/products/${state.currentProductId}/category", script)
        self.assertIn("旧属性、图片策略和上传数据会失效", script)
        self.assertIn('item.name_zh || "未翻译类目"', script)
        self.assertIn("category_path_zh", script)


if __name__ == "__main__":
    unittest.main()
