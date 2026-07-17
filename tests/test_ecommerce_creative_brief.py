import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.ecommerce_creative_brief import build_brief
from scripts.marketplace_content_generator import build_copy_compatibility


class EcommerceCreativeBriefTests(unittest.TestCase):
    def fixture(self):
        root = Path(tempfile.mkdtemp())
        product = root / "products/P999901"
        (product / "input").mkdir(parents=True)
        (product / "output").mkdir(parents=True)
        (product / "input/source.json").write_text(json.dumps({
            "product_id": "P999901", "title_cn": "测试收纳盒", "skus": [
                {"sku_id": "s1", "sku_name": "透明 1L", "selection_order": 1},
                {"sku_id": "s2", "sku_name": "透明 2L", "selection_order": 2},
            ], "main_images": [], "detail_images": [],
        }, ensure_ascii=False), encoding="utf-8")
        (product / "output/product-analysis.json").write_text(json.dumps({
            "product_type": "收纳盒", "facts": {"title_cn": "测试收纳盒"},
            "selling_points": [{"text": "透明可见内容物", "evidence": ["source.title_cn"]}],
        }, ensure_ascii=False), encoding="utf-8")
        (product / "output/product-positioning.json").write_text(json.dumps({
            "target_customer": "家庭用户", "usage_scenarios": [{"text": "厨房储藏区", "source_refs": ["source.title_cn"]}],
            "buyer_selling_points": [{"text": "透明可见内容物", "claim_type": "fact", "source_refs": ["source.title_cn"]}],
        }, ensure_ascii=False), encoding="utf-8")
        (product / "output/style-profile.json").write_text(json.dumps({
            "style_family": "home_minimal_organized", "tone": "清爽", "composition_style": "整洁空间",
            "text_style": "短俄文", "color_direction": ["浅灰"],
            "creative_direction": {"product_visual_thesis": "围绕收纳盒", "visual_mood": "清爽", "palette_logic": "商品本色", "lighting": "自然光", "composition": "商品为主角", "typography": "清晰俄文"},
        }, ensure_ascii=False), encoding="utf-8")
        (product / "output/copy-ru.json").write_text(json.dumps({
            "title_ru": "Контейнер для хранения продуктов с крышкой", "selling_points": [{"text_ru": "Прозрачный корпус помогает видеть содержимое"}],
            "bullets_ru": [{"text_ru": "Удобно хранить продукты в кухонном шкафу"}],
            "image_copy_ru": {"benefit": ["Всё видно сразу"], "detail": ["Продуманная форма"], "scene": ["Для кухни и кладовой"], "usage": ["Удобно хранить"], "feature": ["Практичное хранение"], "comparison": ["Выберите подходящий объём"], "disclaimer": ["Проверьте характеристики перед покупкой"]},
        }, ensure_ascii=False), encoding="utf-8")
        return product

    def test_brief_has_eight_dynamic_roles_and_traceability(self):
        product = self.fixture()
        brief = build_brief(product)
        self.assertEqual(len(brief["image_roles"]), 8)
        self.assertEqual([r["index"] for r in brief["image_roles"]], list(range(1, 9)))
        self.assertTrue(all(role["russian_text"] for role in brief["image_roles"]))
        self.assertTrue(all(role["source_references"] for role in brief["image_roles"]))
        schema = json.loads((Path(__file__).parents[1] / "templates/ecommerce-creative-brief.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(brief)), [])

    def test_marketplace_compatibility_keeps_dedicated_image_copy(self):
        image_copy = {
            "main": ["Главное"], "benefit": ["Выгода"],
            "problem_solution": ["Решение"], "scene": ["Сцена"],
            "feature": ["Функция"], "detail": ["Деталь"],
            "usage": ["Применение"], "comparison": ["Сравнение"],
            "disclaimer": ["Важно"], "main_by_sku": {"s1": ["1 л"]},
        }
        result = build_copy_compatibility(
            "P999901",
            {"title_ru": "Контейнер"},
            {
                "source_refs": ["source.json"],
                "sections": {"product_value": "A", "usage_scenarios": "B", "core_advantages": "C"},
                "section_evidence": [
                    {"section": "product_value", "source_refs": ["source.json"]},
                    {"section": "usage_scenarios", "source_refs": ["source.json"]},
                    {"section": "core_advantages", "source_refs": ["source.json"]},
                ],
                "description_ru": "Описание", "unknown_fields": [],
            },
            {"primary_keywords": ["контейнер"], "secondary_keywords": []},
            {
                "short_title_ru": "Контейнер", "usage_scenarios_ru": ["Кухня"],
                "warnings": [], "image_copy_ru": image_copy,
            },
            "2026-07-16T00:00:00+00:00",
        )
        self.assertEqual(result["image_copy_ru"], image_copy)

    def test_operator_can_define_exact_product_specific_eight_roles(self):
        product = self.fixture()
        roles = []
        for index in range(8):
            roles.append({
                "role_id": f"manual_role_{index + 1}",
                "image_type": "comparison" if index == 6 else "scene",
                "commercial_purpose": f"商品专属图位{index + 1}",
                "buyer_question": f"问题{index + 1}",
                "operation": "compose_from_real_images" if index == 6 else "edit_real_image",
                "russian_text": [f"ТЕКСТ {index + 1}"],
                "source_references": ["products/P999901/input/source.json"],
                "must_prove": f"已确认事实{index + 1}",
            })
        (product / "input/operator-guidance.json").write_text(json.dumps({
            "image_detail_roles": roles,
        }, ensure_ascii=False), encoding="utf-8")
        brief = build_brief(product)
        self.assertEqual([item["role_id"] for item in brief["image_roles"]], [f"manual_role_{index}" for index in range(1, 9)])
        self.assertEqual(brief["image_roles"][6]["operation"], "compose_from_real_images")
        self.assertEqual(brief["image_roles"][0]["russian_text"], ["ТЕКСТ 1"])


if __name__ == "__main__":
    unittest.main()
