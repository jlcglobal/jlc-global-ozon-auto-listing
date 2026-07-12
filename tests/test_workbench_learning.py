import json
import tempfile
import unittest
from pathlib import Path

from scripts.workbench_learning import materialize_active_experience, record_image_feedback, record_workbench_edits


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_product(root: Path, product_id: str) -> Path:
    product = root / "products" / product_id
    write(product / "input/category-selection.json", {
        "category_id": 100, "type_id": 200,
        "category_name_zh": "食品储存罐", "category_path_zh": ["家居", "厨房", "食品储存罐"],
    })
    write(product / "output/copy-ru.json", {"title_ru": "Старый заголовок"})
    write(product / "output/style-profile.json", {"style_family": "kitchen_warm_home"})
    write(product / "output/product-analysis.json", {"product_type": "食品储藏罐"})
    return product


class WorkbenchLearningTest(unittest.TestCase):
    def test_image_preferences_activate_after_two_distinct_products(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = make_product(root, "P000001")
            second = make_product(root, "P000002")
            item = {
                "slot": "detail-001", "image_type": "scene",
                "visual_direction": "明亮真实场景", "prompt": "突出真实使用氛围",
            }
            record_image_feedback(root, first, item, "keep", "2026-07-13T10:00:00+08:00")
            self.assertEqual(materialize_active_experience(root, first, "2026-07-13T10:01:00+08:00")["active_image_preferences"], [])
            record_image_feedback(root, second, item, "keep", "2026-07-13T10:02:00+08:00")
            active = materialize_active_experience(root, second, "2026-07-13T10:03:00+08:00")["active_image_preferences"]
            self.assertEqual(len(active), 1)
            self.assertTrue(active[0]["active"])
            self.assertEqual(active[0]["actions"]["keep"], 2)

    def test_rule_activates_only_after_same_edit_on_two_distinct_products(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = make_product(root, "P000001")
            second = make_product(root, "P000002")
            record_workbench_edits(root, first, {"title_ru": "Новый заголовок"}, "2026-07-13T10:00:00+08:00")
            first_materialized = materialize_active_experience(root, first, "2026-07-13T10:01:00+08:00")
            self.assertEqual(first_materialized["active_rules"], [])
            record_workbench_edits(root, first, {"title_ru": "Новый заголовок"}, "2026-07-13T10:02:00+08:00")
            self.assertEqual(materialize_active_experience(root, first, "2026-07-13T10:03:00+08:00")["active_rules"], [])
            record_workbench_edits(root, second, {"title_ru": "Новый заголовок"}, "2026-07-13T10:04:00+08:00")
            active = materialize_active_experience(root, second, "2026-07-13T10:05:00+08:00")["active_rules"]
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]["occurrences"], 2)
            self.assertEqual(active[0]["suggested_value"], "Новый заголовок")


if __name__ == "__main__":
    unittest.main()
