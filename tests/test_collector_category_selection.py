from __future__ import annotations

import json
import importlib.util
import re
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SPEC = importlib.util.spec_from_file_location("category_selection_ingest_app", PROJECT_ROOT / "collector/local-ingest/app.py")
ingest_app = importlib.util.module_from_spec(APP_SPEC)
APP_SPEC.loader.exec_module(ingest_app)


def write_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_root() -> tuple[tempfile.TemporaryDirectory, Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    write_json(root / "ozon-adapter/metadata/ozon-rules-2026-07-10/categories.json", [
        {"categoryId": "101", "typeId": "201", "nameRu": "Наушники", "nameZh": "", "categoryPath": ["Электроника", "Наушники"]},
        {"categoryId": "102", "typeId": "202", "nameRu": "Весы для багажа", "nameZh": "", "categoryPath": ["Туризм", "Весы для багажа"]},
        {"categoryId": "103", "typeId": "203", "nameRu": "Точилка для ножей", "nameZh": "", "categoryPath": ["Дом", "Кухня", "Точилка для ножей"]},
        {"categoryId": "104", "typeId": "204", "nameRu": "Сумка", "nameZh": "", "categoryPath": ["Аксессуары", "Сумки"]},
        {"categoryId": "105", "typeId": "205", "nameRu": "Контейнер пищевой", "nameZh": "", "categoryPath": ["Дом и сад", "Хранение продуктов", "Контейнер пищевой"]},
    ])
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
        self.assertEqual(storage["name_ru"], "Хранение продуктов")
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

    def test_category_change_invalidates_attributes_image_strategy_and_payload_without_ozon_write(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "products/P000001"
            write_json(product / "status.json", {
                "status": "COLLECTED", "api_write_count": 0, "history": [], "completed_steps": ["collect_source"]
            })
            write_json(product / "input/category-selection.json", {"category_id": 1, "type_id": 2})
            write_json(product / "input/source.json", {"source_url": "https://detail.1688.com/offer/1.html"})
            write_json(product / "output/ozon-attributes.json", {"old": True})
            write_json(product / "output/image-plan.json", {"old": True})
            write_json(product / "output/ozon-upload-payload.json", {"old": True})
            selection = {
                "category_id": 102, "type_id": 202, "rules_snapshot_hash": "new",
                "rules_snapshot": {"attributes": [{"attribute_id": 85}]},
            }
            result = ingest_app.replace_collected_category(product, selection)
            self.assertEqual(result["status"], "changed")
            self.assertFalse((product / "output/ozon-attributes.json").exists())
            self.assertFalse((product / "output/image-plan.json").exists())
            self.assertFalse((product / "output/ozon-upload-payload.json").exists())
            self.assertTrue((product / "input/source.json").exists())
            self.assertEqual(result["ozon_write_api_calls"], 0)
            self.assertEqual(result["inventory_api_calls"], 0)

    def test_edge_extension_build_contains_mandatory_category_gate(self):
        source = (PROJECT_ROOT / "collector/edge-extension/src/content.ts").read_text(encoding="utf-8")
        built = (PROJECT_ROOT / "collector/edge-extension/content.js").read_text(encoding="utf-8")
        manifest = json.loads((PROJECT_ROOT / "collector/edge-extension/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(source, built)
        self.assertIn("最终 Ozon 类目（必选）", built)
        self.assertIn("/api/collector/categories/rules", built)
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
        self.assertIn("allow_readonly_fetch: false", built)
        self.assertIn("本地中文类目树（点击逐级展开", built)
        self.assertIn("选择SKU（勾选下方商品）", built)
        self.assertIn("蓝色仅表示筛选，不代表已选SKU", built)
        self.assertIn(".caf-list { overflow: auto; padding: 0 16px 8px; flex: 3 1 0; min-height: 180px", built)
        self.assertIn(".caf-category { border-top: 1px solid #e5e7eb; padding: 10px 16px; background: #f8fafc; overflow: auto", built)
        self.assertNotIn("collectorApi(`/api/collector/categories/tree", built)
        self.assertIn("请先选择最终Ozon类目", built)
        self.assertEqual(manifest["version"], "0.4.7")
        resources = manifest["web_accessible_resources"][0]["resources"]
        self.assertIn("category-rules-cache.json", resources)

    def test_production_category_cache_is_complete_chinese_and_bundled_locally(self):
        server_path = PROJECT_ROOT / "ozon-adapter/metadata/ozon-rules-2026-07-10/category-tree.zh-CN.json"
        extension_path = PROJECT_ROOT / "collector/edge-extension/category-tree.zh-CN.json"
        server_cache = json.loads(server_path.read_text(encoding="utf-8"))
        extension_cache = json.loads(extension_path.read_text(encoding="utf-8"))
        self.assertEqual(server_cache, extension_cache)
        self.assertEqual(server_cache["locale"], "zh-CN")
        self.assertEqual(server_cache["item_count"], 7424)
        self.assertEqual(len(server_cache["children_by_parent"]["root"]), 26)
        nodes = [node for values in server_cache["children_by_parent"].values() for node in values]
        self.assertTrue(all(re.search(r"[\u3400-\u9fff]", node["name_zh"]) for node in nodes))
        self.assertFalse(any(re.search(r"[А-Яа-яЁё]", node["name_zh"]) for node in nodes))
        self.assertTrue(all(all(re.search(r"[\u3400-\u9fff]", part) for part in node["path_zh"]) for node in nodes))
        self.assertEqual(
            next(node for node in nodes if node["name_ru"] == "Хранение продуктов")["name_zh"],
            "食品储藏",
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
