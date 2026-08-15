import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "pricing-engine"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))

from product_fact_merger import extract_capacity_ml, extract_color, freeze_sku_run_snapshot, merge_product_facts  # noqa: E402
from attribute_fill_input import build_attribute_fill_input  # noqa: E402
from ozon_attribute_compiler import compile_product_attributes, material_decision_from_fact  # noqa: E402
from ozon_uploader.service import build_import_items  # noqa: E402


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def canonical(value):
    if isinstance(value, dict):
        return value.get("canonical_value", value.get("target_value", value.get("value")))
    return value


class SkuFactAttributePayloadTests(unittest.TestCase):
    def test_nominal_jin_capacity_is_opt_in_and_converts_to_ml(self):
        self.assertIsNone(extract_capacity_ml("10斤圆"))
        self.assertEqual(extract_capacity_ml("10斤圆", allow_nominal_jin=True), 5000)
        self.assertEqual(extract_capacity_ml("6斤方", allow_nominal_jin=True), 3000)

    def test_color_can_be_extracted_from_generic_specification_text(self):
        self.assertEqual(extract_color({
            "sku_name": "枪灰套装【高配】",
            "option_values": [{"name_cn": "规格1", "value_cn": "枪灰套装【高配】"}],
        }), "枪灰")
        self.assertEqual(extract_color({
            "sku_name": "电镀套装【高配】",
            "option_values": [{"name_cn": "规格1", "value_cn": "电镀套装【高配】"}],
        }), "电镀")
        self.assertEqual(extract_color({
            "sku_name": "黑色套装【高配】",
            "option_values": [{"name_cn": "规格1", "value_cn": "黑色套装【高配】"}],
        }), "黑色")

    def make_product(self, with_override=True) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="sku-pipeline-"))
        product_dir = directory / "products/P000101"
        source = {
            "schema_version": "1.0.0",
            "product_id": "P000101",
            "collection_id": "C-001",
            "source_kind": "workbench_collection",
            "title_cn": "双容量不锈钢保温杯",
            "product_attributes": [
                {"name_cn": "材质", "value_cn": "不锈钢", "source_text": "材质 不锈钢"},
                {"name_cn": "SKU尺寸 - 红色 500ml", "value_cn": "12.5cm×8cm×8cm", "source_text": "SKU尺寸 - 红色 500ml 12.5cm×8cm×8cm"},
                {"name_cn": "SKU重量 - 红色 500ml", "value_cn": "320g", "source_text": "SKU重量 - 红色 500ml 320g"},
                {"name_cn": "SKU尺寸 - 黑色 1000ml", "value_cn": "18cm×10cm×10cm", "source_text": "SKU尺寸 - 黑色 1000ml 18cm×10cm×10cm"},
                {"name_cn": "SKU重量 - 黑色 1000ml", "value_cn": "520g", "source_text": "SKU重量 - 黑色 1000ml 520g"},
            ],
            "skus": [
                {
                    "sku_id": "S1",
                    "sku_name": "红色 500ml",
                    "selected": True,
                    "option_values": [
                        {"name_cn": "颜色", "value_cn": "红色"},
                        {"name_cn": "容量", "value_cn": "500ml"},
                    ],
                },
                {
                    "sku_id": "S2",
                    "sku_name": "黑色 1000ml",
                    "selected": True,
                    "option_values": [
                        {"name_cn": "颜色", "value_cn": "黑色"},
                        {"name_cn": "容量", "value_cn": "1000ml"},
                    ],
                },
            ],
        }
        write_json(product_dir / "input/source.json", source)
        write_json(product_dir / "input/category-selection.json", {
            "category_id": 170000,
            "type_id": 900000,
            "category_name": "Термокружки",
        })
        if with_override:
            write_json(product_dir / "input/workbench-sku-overrides.json", {
                "schema_version": "1.0.0",
                "product_id": "P000101",
                "collection_id": "C-001",
                "overrides": [
                    {
                        "product_id": "P000101",
                        "collection_id": "C-001",
                        "sku_id": "S1",
                        "field_name": "product_weight_g",
                        "canonical_value": 333,
                        "canonical_unit": "g",
                        "updated_at": "2026-07-18T00:00:00+00:00",
                    },
                    {
                        "product_id": "P000101",
                        "collection_id": "C-001",
                        "sku_id": "S1",
                        "field_name": "product_length_mm",
                        "canonical_value": 130,
                        "canonical_unit": "mm",
                        "updated_at": "2026-07-18T00:00:00+00:00",
                    },
                ],
            })
        return product_dir

    def make_multi_sku_product(self, count: int) -> Path:
        directory = Path(tempfile.mkdtemp(prefix=f"sku-{count}-pipeline-"))
        product_dir = directory / "products/P000777"
        colors = [
            ("Красный", "красный"),
            ("Черный", "черный"),
            ("Синий", "синий"),
            ("Зеленый", "зеленый"),
            ("Белый", "белый"),
            ("Серый", "серый"),
            ("Желтый", "желтый"),
            ("Фиолетовый", "фиолетовый"),
            ("Оранжевый", "оранжевый"),
            ("Бежевый", "бежевый"),
        ]
        source_skus = []
        product_attributes = [{"name_cn": "材质", "value_cn": "Нержавеющая сталь", "source_text": "材质 Нержавеющая сталь"}]
        for index in range(1, count + 1):
            sku_id = f"SKU-{index:02d}"
            capacity = 400 + index * 100
            color_label, _ = colors[(index - 1) % len(colors)]
            spec = f"модель {index:02d}"
            length_cm = 10 + index
            width_cm = 6 + index
            height_cm = 8 + index
            weight_g = 200 + index * 25
            source_skus.append({
                "sku_id": sku_id,
                "sku_name": f"{color_label} {capacity}ml",
                "selected": True,
                "option_values": [
                    {"name_cn": "颜色", "value_cn": color_label},
                    {"name_cn": "容量", "value_cn": f"{capacity}ml"},
                    {"name_cn": "规格", "value_cn": spec},
                ],
                "image_path": f"products/P000777/input/sku-images/{sku_id}.png",
            })
            product_attributes.extend([
                {
                    "name_cn": f"SKU尺寸 - {color_label} {capacity}ml",
                    "value_cn": f"{length_cm}cm×{width_cm}cm×{height_cm}cm",
                    "source_text": f"SKU尺寸 - {color_label} {capacity}ml {length_cm}cm×{width_cm}cm×{height_cm}cm",
                },
                {
                    "name_cn": f"SKU重量 - {color_label} {capacity}ml",
                    "value_cn": f"{weight_g}g",
                    "source_text": f"SKU重量 - {color_label} {capacity}ml {weight_g}g",
                },
            ])
        write_json(product_dir / "input/source.json", {
            "schema_version": "1.0.0",
            "product_id": "P000777",
            "collection_id": "C-MULTI",
            "source_kind": "workbench_collection",
            "title_cn": f"{count} SKU 多规格测试商品",
            "product_attributes": product_attributes,
            "skus": source_skus,
        })
        write_json(product_dir / "input/category-selection.json", {
            "category_id": 170000,
            "type_id": 900000,
            "category_name": "Много SKU",
        })
        return product_dir

    def test_source_image_text_measurements_bind_to_each_exact_sku_image(self):
        directory = Path(tempfile.mkdtemp(prefix="sku-image-measurements-"))
        product_dir = directory / "products/P000909"
        write_json(product_dir / "input/source.json", {
            "schema_version": "1.0.0",
            "product_id": "P000909",
            "collection_id": "C-909",
            "source_kind": "workbench_collection",
            "title_cn": "两种规格玻璃盒",
            "product_attributes": [{"name_cn": "材质", "value_cn": "玻璃"}],
            "skus": [
                {
                    "sku_id": "S1", "sku_name": "小号透明", "selected": True,
                    "local_image_path": "products/P000909/input/sku-images/sku-001.jpg",
                    "option_values": [{"name_cn": "规格", "value_cn": "小号透明"}],
                },
                {
                    "sku_id": "S2", "sku_name": "大号琥珀色", "selected": True,
                    "local_image_path": "products/P000909/input/sku-images/sku-002.jpg",
                    "option_values": [{"name_cn": "规格", "value_cn": "大号琥珀色"}],
                },
            ],
        })
        write_json(product_dir / "input/category-selection.json", {
            "category_id": 1, "type_id": 2, "category_name": "Контейнеры",
        })
        write_json(product_dir / "output/product-analysis.json", {
            "facts": {
                "dimensions": {
                    "status": "source_image_text",
                    "small": {
                        "top_diameter_cm": 18, "bottom_diameter_cm": 19, "height_cm": 7,
                        "evidence": ["input/sku-images/sku-001.jpg"],
                    },
                    "large": {
                        "top_diameter_cm": 21.5, "bottom_diameter_cm": 23, "height_cm": 8,
                        "evidence": ["input/sku-images/sku-002.jpg"],
                    },
                },
                "weight": {
                    "status": "source_image_text",
                    "small": {"value_g": 568, "evidence": ["input/sku-images/sku-001.jpg"]},
                    "large": {"value_g": 934, "evidence": ["input/sku-images/sku-002.jpg"]},
                    "main_only": {"value_g": 1115, "evidence": ["input/main-images/main-001.jpg"]},
                },
            },
        })

        merged = merge_product_facts(product_dir)
        rows = {row["sku_id"]: row for row in merged["sku_rows"]}
        self.assertEqual(canonical(rows["S1"]["product_dimensions"]), {
            "length_mm": 190.0, "width_mm": 190.0, "height_mm": 70.0,
        })
        self.assertEqual(canonical(rows["S2"]["product_dimensions"]), {
            "length_mm": 230.0, "width_mm": 230.0, "height_mm": 80.0,
        })
        self.assertEqual(canonical(rows["S1"]["product_weight"]), 568)
        self.assertEqual(canonical(rows["S2"]["product_weight"]), 934)
        self.assertEqual(rows["S1"]["product_weight"]["mapping_method"], "source_image_text_exact_sku")

    def write_multi_sku_attribute_inputs(self, product_dir: Path, sku_ids: list[str]) -> Dict[str, Any]:
        colors = [
            "красный", "черный", "синий", "зеленый", "белый",
            "серый", "желтый", "фиолетовый", "оранжевый", "бежевый",
        ]
        selected_reordered = list(reversed(sku_ids))
        sku_rows = []
        attributes_by_sku = {}
        for index, sku_id in enumerate(sku_ids, start=1):
            capacity_ml = 400 + index * 100
            product_weight = 200 + index * 25
            package_weight = product_weight + 75
            product_dims = {"length_mm": (10 + index) * 10, "width_mm": (6 + index) * 10, "height_mm": (8 + index) * 10}
            package_dims = {key: value + 10 for key, value in product_dims.items()}
            sku_rows.append({
                "sku_id": sku_id,
                "product_dimensions": {"canonical_value": product_dims, "canonical_unit": "mm"},
                "product_weight": {"canonical_value": product_weight, "canonical_unit": "g"},
                "package_dimensions": {"canonical_value": package_dims, "canonical_unit": "mm"},
                "package_weight": {"canonical_value": package_weight, "canonical_unit": "g"},
                "capacity": {"canonical_value": capacity_ml, "canonical_unit": "ml"},
                "specification": {"canonical_value": f"модель {index:02d}", "canonical_unit": "text"},
                "quantity": {"canonical_value": 1, "canonical_unit": "pcs"},
            })
            attributes_by_sku[sku_id] = [
                {
                    "attribute_id": 10097,
                    "attribute_name": "Название цвета",
                    "scope": "sku",
                    "decision_status": "filled",
                    "raw_semantic_value": colors[index - 1],
                    "canonical_value": colors[index - 1],
                    "canonical_unit": "text",
                    "ozon_value": colors[index - 1],
                    "source": "1688",
                    "mapping_method": "AI_semantic_match",
                },
                {
                    "attribute_id": 3001,
                    "attribute_name": "Объем, л",
                    "scope": "sku",
                    "decision_status": "filled",
                    "raw_semantic_value": f"{capacity_ml}ml",
                    "canonical_value": capacity_ml,
                    "canonical_unit": "ml",
                    "ozon_value": capacity_ml,
                    "source": "1688",
                    "mapping_method": "direct",
                },
                {
                    "attribute_id": 3101,
                    "attribute_name": "Размер",
                    "scope": "sku",
                    "decision_status": "filled",
                    "raw_semantic_value": f"модель {index:02d}",
                    "canonical_value": f"модель {index:02d}",
                    "canonical_unit": "text",
                    "ozon_value": f"модель {index:02d}",
                    "source": "1688",
                    "mapping_method": "direct",
                },
                {
                    "attribute_id": 5001,
                    "attribute_name": "Вес с упаковкой, г",
                    "scope": "sku",
                    "decision_status": "filled",
                    "raw_semantic_value": f"{package_weight}g",
                    "canonical_value": package_weight,
                    "canonical_unit": "g",
                    "ozon_value": package_weight,
                    "source": "1688",
                    "mapping_method": "direct",
                },
            ]
        fill_input = {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "collection_id": "C-MULTI",
            "category_id": 170000,
            "type_id": 900000,
            "selected_skus": [{"sku_id": sku_id} for sku_id in selected_reordered],
            "sku_rows": sku_rows,
            "ozon_attributes": [
                {"attribute_id": 2001, "attribute_name": "Материал", "type": "String", "required": True, "is_aspect": False, "allowed_values": [{"value": "Нержавеющая сталь", "dictionary_value_id": 77}]},
                {"attribute_id": 10097, "attribute_name": "Название цвета", "type": "String", "required": True, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 3001, "attribute_name": "Объем, л", "type": "Decimal", "required": True, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 3101, "attribute_name": "Размер", "type": "String", "required": False, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 5001, "attribute_name": "Вес с упаковкой, г", "type": "Integer", "required": False, "is_aspect": True, "allowed_values": []},
            ],
            "input_hash": f"fill-input-{len(sku_ids)}",
        }
        design = {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "attribute_decisions": {
                "common_attributes": [
                    {"attribute_id": 2001, "attribute_name": "Материал", "scope": "common", "decision_status": "filled", "raw_semantic_value": "нержавеющая сталь", "ozon_value": "Нержавеющая сталь", "dictionary_value_id": 77, "source": "1688", "mapping_method": "AI_semantic_match"}
                ],
                "attributes_by_sku": attributes_by_sku,
            },
        }
        write_json(product_dir / "output/attribute-fill-input.json", fill_input)
        write_json(product_dir / "output/ozon-ecommerce-design.json", design)
        return {"fill_input": fill_input, "design": design}

    def write_attribute_inputs(self, product_dir: Path):
        fill_input = {
            "schema_version": "1.0.0",
            "product_id": "P000101",
            "collection_id": "C-001",
            "category_id": 170000,
            "type_id": 900000,
            "selected_skus": [{"sku_id": "S1"}, {"sku_id": "S2"}],
            "sku_rows": [
                {
                    "sku_id": "S1",
                    "product_dimensions": {"canonical_value": {"length_mm": 130, "width_mm": 80, "height_mm": 80}, "canonical_unit": "mm"},
                    "product_weight": {"canonical_value": 333, "canonical_unit": "g"},
                    "package_dimensions": {"canonical_value": {"length_mm": 140, "width_mm": 90, "height_mm": 90}, "canonical_unit": "mm"},
                    "package_weight": {"canonical_value": 846.4, "canonical_unit": "g"},
                    "capacity": {"canonical_value": 500, "canonical_unit": "ml"},
                    "quantity": {"canonical_value": 1, "canonical_unit": "pcs"},
                },
                {
                    "sku_id": "S2",
                    "product_dimensions": {"canonical_value": {"length_mm": 180, "width_mm": 100, "height_mm": 100}, "canonical_unit": "mm"},
                    "product_weight": {"canonical_value": 520, "canonical_unit": "g"},
                    "package_dimensions": {"canonical_value": {"length_mm": 190, "width_mm": 110, "height_mm": 110}, "canonical_unit": "mm"},
                    "package_weight": {"canonical_value": 1100, "canonical_unit": "g"},
                    "capacity": {"canonical_value": 1000, "canonical_unit": "ml"},
                    "quantity": {"canonical_value": 1, "canonical_unit": "pcs"},
                },
            ],
            "merged_facts": {},
            "measurements": {},
            "ozon_attributes": [
                {
                    "attribute_id": 2001,
                    "attribute_name": "Материал",
                    "type": "String",
                    "required": True,
                    "is_aspect": False,
                    "allowed_values": [{"value": "Нержавеющая сталь", "dictionary_value_id": 77}],
                },
                {"attribute_id": 10097, "attribute_name": "Название цвета", "type": "String", "required": True, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 3001, "attribute_name": "Объем, л", "type": "Decimal", "required": True, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 4001, "attribute_name": "Длина, см", "type": "Decimal", "required": False, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 4002, "attribute_name": "Ширина, см", "type": "Decimal", "required": False, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 5001, "attribute_name": "Вес с упаковкой, г", "type": "Integer", "required": False, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 6001, "attribute_name": "Количество товара в упаковке, шт", "type": "Integer", "required": False, "is_aspect": False, "allowed_values": []},
            ],
            "input_hash": "fill-input-v1",
        }
        design = {
            "schema_version": "1.0.0",
            "product_id": "P000101",
            "attribute_decisions": {
                "common_attributes": [
                    {
                        "attribute_id": 2001,
                        "attribute_name": "Материал",
                        "scope": "common",
                        "decision_status": "filled",
                        "raw_semantic_value": "不锈钢",
                        "ozon_value": "Нержавеющая сталь",
                        "dictionary_value_id": 77,
                        "source": "1688",
                        "mapping_method": "AI_semantic_match",
                        "source_refs": ["input/source.json.product_attributes"],
                    },
                    {
                        "attribute_id": 6001,
                        "attribute_name": "Количество товара в упаковке, шт",
                        "scope": "common",
                        "decision_status": "filled",
                        "raw_semantic_value": 1,
                        "canonical_value": 1,
                        "canonical_unit": "pcs",
                        "ozon_value": 1,
                        "source": "project_default",
                        "mapping_method": "direct",
                    },
                ],
                "attributes_by_sku": {
                    "S1": [
                        {"attribute_id": 10097, "attribute_name": "Название цвета", "scope": "sku", "decision_status": "filled", "raw_semantic_value": "红色", "canonical_value": "красный", "canonical_unit": "text", "ozon_value": "красный", "source": "1688", "mapping_method": "AI_semantic_match", "source_refs": ["input/source.json.skus.option_values"]},
                        {"attribute_id": 3001, "attribute_name": "Объем, л", "scope": "sku", "decision_status": "filled", "raw_semantic_value": "500ml", "canonical_value": 500, "canonical_unit": "ml", "ozon_value": 500, "source": "1688", "mapping_method": "direct", "source_refs": ["input/source.json.skus"]},
                        {"attribute_id": 4001, "attribute_name": "Длина, см", "scope": "sku", "decision_status": "filled", "raw_semantic_value": "130mm", "canonical_value": 130, "canonical_unit": "mm", "ozon_value": 130, "source": "human_override", "mapping_method": "manual_workbench_edit", "source_refs": ["input/workbench-sku-overrides.json"]},
                        {"attribute_id": 4002, "attribute_name": "Ширина, см", "scope": "sku", "decision_status": "filled", "raw_semantic_value": "12.5cm", "canonical_value": 12.5, "canonical_unit": "cm", "ozon_value": 12.5, "source": "1688", "mapping_method": "direct", "source_refs": ["input/source.json.sku_measurement_table"]},
                        {"attribute_id": 5001, "attribute_name": "Вес с упаковкой, г", "scope": "sku", "decision_status": "filled", "raw_semantic_value": "846.4g", "canonical_value": 846.4, "canonical_unit": "g", "ozon_value": 846.4, "source": "AI_estimated", "mapping_method": "AI_estimated"},
                    ],
                    "S2": [
                        {"attribute_id": 10097, "attribute_name": "Название цвета", "scope": "sku", "decision_status": "filled", "raw_semantic_value": "黑色", "canonical_value": "черный", "canonical_unit": "text", "ozon_value": "черный", "source": "1688", "mapping_method": "AI_semantic_match", "source_refs": ["input/source.json.skus.option_values"]},
                        {"attribute_id": 3001, "attribute_name": "Объем, л", "scope": "sku", "decision_status": "filled", "raw_semantic_value": "1000ml", "canonical_value": 1000, "canonical_unit": "ml", "ozon_value": 1000, "source": "1688", "mapping_method": "direct", "source_refs": ["input/source.json.skus"]},
                        {"attribute_id": 4001, "attribute_name": "Длина, см", "scope": "sku", "decision_status": "filled", "raw_semantic_value": "180mm", "canonical_value": 180, "canonical_unit": "mm", "ozon_value": 180, "source": "1688", "mapping_method": "direct", "source_refs": ["input/source.json.sku_measurement_table"]},
                        {"attribute_id": 5001, "attribute_name": "Вес с упаковкой, г", "scope": "sku", "decision_status": "filled", "raw_semantic_value": "1100g", "canonical_value": 1100, "canonical_unit": "g", "ozon_value": 1100, "source": "1688", "mapping_method": "direct", "source_refs": ["input/source.json.sku_measurement_table"]},
                    ],
                },
            },
        }
        write_json(product_dir / "output/attribute-fill-input.json", fill_input)
        write_json(product_dir / "output/ozon-ecommerce-design.json", design)

    def test_sku_fact_priority_and_override_isolation(self):
        product_dir = self.make_product(with_override=True)
        merged = merge_product_facts(product_dir)
        fact_lock = json.loads((product_dir / "output/product-fact-lock.json").read_text(encoding="utf-8"))
        rows = {item["sku_id"]: item for item in merged["sku_rows"]}
        self.assertEqual(fact_lock["fact_source"], "output/merged-product-facts.json")
        self.assertEqual(fact_lock["dependencies"]["merged_facts_hash"], merged["dependency_hash"])
        self.assertEqual(len(fact_lock["locked_skus"]), 2)
        self.assertIn("material", fact_lock["non_inventable_claims"])
        self.assertEqual(canonical(rows["S1"]["product_weight"]), 333)
        self.assertEqual(rows["S1"]["product_weight"]["source"], "human_override")
        self.assertEqual(canonical(rows["S2"]["product_weight"]), 520)
        self.assertEqual(canonical(rows["S1"]["capacity"]), 500)
        self.assertEqual(canonical(rows["S2"]["capacity"]), 1000)
        self.assertEqual(canonical(rows["S1"]["product_dimensions"])["length_mm"], 130)
        self.assertEqual(canonical(rows["S2"]["product_dimensions"])["length_mm"], 180)
        self.assertGreater(canonical(rows["S1"]["package_weight"]), canonical(rows["S1"]["product_weight"]))
        self.assertGreater(canonical(rows["S2"]["package_dimensions"])["height_mm"], canonical(rows["S2"]["product_dimensions"])["height_mm"])

    def test_no_user_edit_still_builds_sku_rows(self):
        product_dir = self.make_product(with_override=False)
        merged = merge_product_facts(product_dir)
        rows = {item["sku_id"]: item for item in merged["sku_rows"]}
        self.assertEqual(canonical(rows["S1"]["product_weight"]), 320)
        self.assertEqual(canonical(rows["S2"]["product_weight"]), 520)
        self.assertEqual(canonical(rows["S1"]["package_weight"]), 620)
        self.assertEqual(canonical(rows["S2"]["package_weight"]), 820)
        self.assertEqual(canonical(rows["S1"]["quantity"]), 1)
        self.assertEqual(canonical(rows["S2"]["quantity"]), 1)

    def test_unitless_sku_sizes_get_distinct_weights_and_keep_human_weight(self):
        directory = Path(tempfile.mkdtemp(prefix="sku-estimate-pipeline-"))
        product_dir = directory / "products/P000202"
        write_json(product_dir / "input/source.json", {
            "schema_version": "1.0.0",
            "product_id": "P000202",
            "collection_id": "C-STORAGE",
            "source_kind": "workbench_collection",
            "title_cn": "透明收纳展示盒",
            "product_attributes": [],
            "skus": [
                {"sku_id": "S1", "sku_name": "【27*22*18】无隔板", "selected": True, "option_values": []},
                {"sku_id": "S2", "sku_name": "【40*25*30】无隔板", "selected": True, "option_values": []},
                {"sku_id": "S3", "sku_name": "【45*33*33】1个隔板", "selected": True, "option_values": []},
            ],
        })
        write_json(product_dir / "input/category-selection.json", {
            "category_id": 170000,
            "type_id": 900000,
            "category_name": "Коробки для хранения",
        })
        write_json(product_dir / "input/workbench-sku-overrides.json", {
            "schema_version": "1.0.0",
            "product_id": "P000202",
            "collection_id": "C-STORAGE",
            "overrides": [{
                "product_id": "P000202",
                "collection_id": "C-STORAGE",
                "sku_id": "S1",
                "field_name": "product_weight_g",
                "canonical_value": 333,
                "canonical_unit": "g",
            }],
        })
        write_json(product_dir / "output/cost-analysis.json", {
            "product_weight": {
                "value": 1000,
                "unit": "g",
                "source": "estimated",
                "source_ref": "pricing_rules.measurement_profiles.default",
                "confidence": 45,
                "estimated": True,
            },
        })

        merged = merge_product_facts(product_dir)
        rows = {item["sku_id"]: item for item in merged["sku_rows"]}

        self.assertEqual(canonical(rows["S1"]["product_weight"]), 333)
        self.assertEqual(rows["S1"]["product_weight"]["source"], "human_override")
        self.assertLess(canonical(rows["S2"]["product_weight"]), canonical(rows["S3"]["product_weight"]))
        self.assertEqual(canonical(rows["S3"]["product_weight"]), 1000)
        self.assertEqual(rows["S2"]["product_weight"]["mapping_method"], "sku_dimension_scaled_estimate")
        self.assertEqual(canonical(rows["S2"]["package_weight"]), canonical(rows["S2"]["product_weight"]) + 300)
        self.assertEqual(canonical(rows["S3"]["product_dimensions"])["length_mm"], 450)

    def test_empty_dimensions_accept_user_axes_and_freeze_snapshot(self):
        product_dir = self.make_product(with_override=False)
        source_path = product_dir / "input/source.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["product_attributes"] = [{"name_cn": "材质", "value_cn": "不锈钢"}]
        write_json(source_path, source)
        write_json(product_dir / "input/workbench-sku-overrides.json", {
            "schema_version": "1.0.0",
            "product_id": "P000101",
            "collection_id": "C-001",
            "overrides": [
                {
                    "product_id": "P000101",
                    "collection_id": "C-001",
                    "sku_id": "S1",
                    "field_name": "product_length_mm",
                    "canonical_value": 125,
                    "canonical_unit": "mm",
                    "updated_at": "2026-07-18T00:00:00+00:00",
                },
                {
                    "product_id": "P000101",
                    "collection_id": "C-001",
                    "sku_id": "S1",
                    "field_name": "product_width_mm",
                    "canonical_value": 80,
                    "canonical_unit": "mm",
                    "updated_at": "2026-07-18T00:01:00+00:00",
                },
                {
                    "product_id": "P000101",
                    "collection_id": "C-001",
                    "sku_id": "S1",
                    "field_name": "product_height_mm",
                    "canonical_value": 60,
                    "canonical_unit": "mm",
                    "updated_at": "2026-07-18T00:02:00+00:00",
                },
                {
                    "product_id": "P000101",
                    "collection_id": "C-001",
                    "sku_id": "S1",
                    "field_name": "specification_text",
                    "canonical_value": "20cm",
                    "canonical_unit": "text",
                    "updated_at": "2026-07-18T00:03:00+00:00",
                },
            ],
        })
        merged = merge_product_facts(product_dir)
        row = {item["sku_id"]: item for item in merged["sku_rows"]}["S1"]
        self.assertEqual(canonical(row["product_dimensions"]), {"length_mm": 125, "width_mm": 80, "height_mm": 60})
        self.assertEqual(canonical(row["specification"]), "20cm")
        snapshot = freeze_sku_run_snapshot(
            product_dir,
            batch_id="B-EMPTY-DIMS",
            review_mode="manual",
            auto_upload=False,
            target_store_ids=["shop-a"],
        )
        frozen = {item["sku_id"]: item for item in snapshot["sku_rows"]}["S1"]
        self.assertEqual(canonical(frozen["product_dimensions"]), {"length_mm": 125, "width_mm": 80, "height_mm": 60})
        self.assertGreater(canonical(frozen["package_dimensions"])["length_mm"], 125)

    def test_attribute_compiler_keeps_common_and_per_sku_values_separate(self):
        product_dir = self.make_product(with_override=True)
        self.write_attribute_inputs(product_dir)
        compiled = compile_product_attributes(product_dir)
        common = {item["attribute_id"]: item for item in compiled["common_attributes"]}
        by_sku = {
            sku_id: {item["attribute_id"]: item for item in values}
            for sku_id, values in compiled["attributes_by_sku"].items()
        }
        self.assertEqual(common[2001]["value"], "Нержавеющая сталь")
        self.assertEqual(common[2001]["dictionary_value_id"], 77)
        self.assertEqual(common[2001]["source"], "1688")
        self.assertEqual(common[2001]["mapping_method"], "AI_semantic_match")
        self.assertEqual(by_sku["S1"][10097]["value"], "красный")
        self.assertEqual(by_sku["S2"][10097]["value"], "черный")
        self.assertEqual(by_sku["S1"][3001]["value"], 0.5)
        self.assertEqual(by_sku["S1"][3001]["conversion_rule"], "ml_to_l")
        self.assertEqual(by_sku["S2"][3001]["value"], 1)
        self.assertEqual(by_sku["S1"][4001]["value"], 13)
        self.assertEqual(by_sku["S1"][4001]["conversion_rule"], "mm_to_cm")
        self.assertEqual(by_sku["S1"][4002]["value"], 12.5)
        self.assertEqual(by_sku["S1"][4002]["conversion_rule"], "cm_to_cm")
        self.assertEqual(by_sku["S2"][4001]["value"], 18)
        self.assertEqual(by_sku["S1"][5001]["value"], 847)
        self.assertEqual(by_sku["S2"][5001]["value"], 1100)
        self.assertGreaterEqual(compiled["required_summary"]["filled"], 5)
        self.assertNotIn("S1:10097", compiled["required_summary"]["missing_attribute_ids"])
        self.assertNotIn("S2:10097", compiled["required_summary"]["missing_attribute_ids"])

    def test_compiler_maps_collected_carbon_steel_to_live_material_dictionary(self):
        """A collected material fact must beat an absent designer decision."""
        product_dir = self.make_product(with_override=False)
        self.write_attribute_inputs(product_dir)
        fill_path = product_dir / "output/attribute-fill-input.json"
        fill_input = json.loads(fill_path.read_text(encoding="utf-8"))
        fill_input["merged_facts"] = {
            "structured_attributes": {
                "材质": {
                    "name_cn": "材质",
                    "value_cn": "碳钢",
                    "source_text": "材质：碳钢",
                    "source_ref": "input/source.json.product_attributes.0",
                }
            }
        }
        for attribute in fill_input["ozon_attributes"]:
            if attribute["attribute_id"] == 2001:
                attribute["allowed_values"] = [
                    {"value": "Углеродистая сталь", "dictionary_value_id": 62142}
                ]
        write_json(fill_path, fill_input)
        design_path = product_dir / "output/ozon-ecommerce-design.json"
        design = json.loads(design_path.read_text(encoding="utf-8"))
        design["attribute_decisions"]["common_attributes"] = [
            item for item in design["attribute_decisions"]["common_attributes"]
            if item["attribute_id"] != 2001
        ]
        write_json(design_path, design)

        compiled = compile_product_attributes(product_dir)
        material = next(item for item in compiled["common_attributes"] if item["attribute_id"] == 2001)
        self.assertEqual(material["value"], "Углеродистая сталь")
        self.assertEqual(material["dictionary_value_id"], 62142)
        self.assertEqual(material["source"], "1688")
        self.assertIn(material["mapping_method"], {"deterministic_exact_dictionary_match", "AI_semantic_match"})
        self.assertIn("碳钢", " ".join(material["evidence"]))

    def test_pet_material_does_not_match_linen_by_substring(self):
        fill_input = {
            "merged_facts": {
                "structured_attributes": {
                    "材质": {
                        "name_cn": "材质",
                        "value_cn": "pet",
                        "source_text": "材质pet容量200",
                        "source_ref": "input/source.json.product_attributes",
                    },
                },
            },
        }
        material_attribute = {
            "attribute_id": 6383,
            "attribute_name": "Материал",
            "allowed_values": [
                {"dictionary_value_id": 61911, "value": "Лён"},
                {"dictionary_value_id": 62055, "value": "ПЭТ (Полиэтилентерефталат)"},
            ],
        }

        material = material_decision_from_fact(material_attribute, fill_input)

        self.assertIsNotNone(material)
        self.assertEqual(material["ozon_value"], "ПЭТ (Полиэтилентерефталат)")
        self.assertEqual(material["dictionary_value_id"], 62055)

    def test_generic_body_material_does_not_fill_lid_material(self):
        fill_input = {
            "merged_facts": {
                "structured_attributes": {
                    "材质": {
                        "name_cn": "材质",
                        "value_cn": "玻璃",
                        "source_ref": "input/source.json.product_attributes",
                    },
                },
            },
        }
        generic = {
            "attribute_id": 100,
            "attribute_name": "Материал",
            "allowed_values": [{"dictionary_value_id": 1, "value": "Стекло"}],
        }
        lid = {
            "attribute_id": 12829,
            "attribute_name": "Материал крышки",
            "allowed_values": [{"dictionary_value_id": 1, "value": "Стекло"}],
        }
        self.assertIsNotNone(material_decision_from_fact(generic, fill_input))
        self.assertIsNone(material_decision_from_fact(lid, fill_input))

    def test_compiler_projects_sku_capacity_without_designer_decision(self):
        product_dir = self.make_product(with_override=False)
        write_json(product_dir / "output/attribute-fill-input.json", {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "collection_id": "C-001",
            "category_id": 170000,
            "type_id": 900000,
            "selected_skus": [{"sku_id": "S1"}, {"sku_id": "S2"}],
            "sku_rows": [
                {
                    "sku_id": "S1",
                    "capacity": {
                        "canonical_value": 500,
                        "canonical_unit": "ml",
                        "source": "input/source.json.skus",
                        "mapping_method": "direct",
                        "confidence": 1.0,
                    },
                },
                {
                    "sku_id": "S2",
                    "capacity": {
                        "canonical_value": 1000,
                        "canonical_unit": "ml",
                        "source": "input/source.json.skus",
                        "mapping_method": "direct",
                        "confidence": 1.0,
                    },
                },
            ],
            "ozon_attributes": [
                {"attribute_id": 3001, "attribute_name": "Объем, л", "type": "Decimal", "required": False, "is_aspect": False, "allowed_values": []},
            ],
            "input_hash": "capacity-fill-input",
        })
        write_json(product_dir / "output/ozon-ecommerce-design.json", {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "attribute_decisions": {
                "common_attributes": [],
                "attributes_by_sku": {},
            },
        })

        compiled = compile_product_attributes(product_dir)
        by_sku = {
            sku_id: {item["attribute_id"]: item for item in attrs}
            for sku_id, attrs in compiled["attributes_by_sku"].items()
        }

        self.assertEqual(by_sku["S1"][3001]["value"], 0.5)
        self.assertEqual(by_sku["S1"][3001]["conversion_rule"], "ml_to_l")
        self.assertEqual(by_sku["S2"][3001]["value"], 1)

    def test_attribute_fill_input_uses_frozen_sku_snapshot_for_running_batch(self):
        product_dir = self.make_product(with_override=True)
        write_json(product_dir / "status.json", {
            "status": "QUEUED",
            "batch_id": "B-FROZEN",
        })
        write_json(product_dir / "output/ozon-category-attributes.json", {
            "category_id": 170000,
            "type_id": 900000,
            "attributes": [
                {"attribute_id": 10097, "attribute_name": "Название цвета", "type": "String", "required": True, "is_aspect": True},
                {"attribute_id": 3001, "attribute_name": "Объем, л", "type": "Decimal", "required": True, "is_aspect": True},
            ],
        })
        snapshot = freeze_sku_run_snapshot(
            product_dir,
            batch_id="B-FROZEN",
            review_mode="manual",
            auto_upload=False,
            target_store_ids=["shop-a"],
        )
        fact_lock = json.loads((product_dir / "output/product-fact-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(fact_lock["fact_source"], "output/sku-run-snapshot.json")
        self.assertEqual(fact_lock["dependencies"]["sku_run_snapshot_hash"], snapshot["dependency_hash"])
        frozen_s1 = {
            item["sku_id"]: item
            for item in snapshot["sku_rows"]
        }["S1"]
        self.assertEqual(canonical(frozen_s1["product_weight"]), 333)

        write_json(product_dir / "input/workbench-sku-overrides.json", {
            "schema_version": "1.0.0",
            "product_id": "P000101",
            "collection_id": "C-001",
            "overrides": [
                {
                    "product_id": "P000101",
                    "collection_id": "C-001",
                    "sku_id": "S1",
                    "field_name": "product_weight_g",
                    "canonical_value": 999,
                    "canonical_unit": "g",
                    "updated_at": "2026-07-18T00:10:00+00:00",
                },
            ],
        })

        fill_input = build_attribute_fill_input(product_dir)
        rows = {item["sku_id"]: item for item in fill_input["sku_rows"]}
        self.assertEqual(fill_input["dependencies"]["sku_fact_source"], "output/sku-run-snapshot.json")
        self.assertEqual(fill_input["dependencies"]["product_fact_lock_hash"], fact_lock["lock_hash"])
        self.assertEqual(fill_input["product_fact_lock"]["fact_source"], "output/sku-run-snapshot.json")
        self.assertIn("accessories", fill_input["product_fact_lock"]["non_inventable_claims"])
        self.assertEqual(canonical(rows["S1"]["product_weight"]), 333)
        self.assertNotEqual(canonical(rows["S1"]["product_weight"]), 999)

    def test_ozon_payload_uses_each_skus_own_attributes_and_package_data(self):
        final_attributes = {
            "common_attributes": [
                {"attribute_id": 2001, "value": "Нержавеющая сталь", "target_value": "Нержавеющая сталь", "dictionary_value_id": 77},
            ],
            "attributes_by_sku": {
                "S1": [
                    {"attribute_id": 10097, "value": "красный", "target_value": "красный"},
                    {"attribute_id": 3001, "value": 0.5, "target_value": 0.5},
                    {"attribute_id": 5001, "value": 847, "target_value": 847},
                ],
                "S2": [
                    {"attribute_id": 10097, "value": "черный", "target_value": "черный"},
                    {"attribute_id": 3001, "value": 1, "target_value": 1},
                    {"attribute_id": 5001, "value": 1100, "target_value": 1100},
                ],
            },
            "sku_measurements": {
                "S1": {
                    "package_dimensions": {"canonical_value": {"length_mm": 140, "width_mm": 90, "height_mm": 90}},
                    "package_weight": {"canonical_value": 847},
                },
                "S2": {
                    "package_dimensions": {"canonical_value": {"length_mm": 190, "width_mm": 110, "height_mm": 110}},
                    "package_weight": {"canonical_value": 1100},
                },
            },
        }
        draft = {
            "description_category_id": 170000,
            "type_id": 900000,
            "title": "Термокружка",
            "description": "Описание",
            "skus": [
                {"source_sku_id": "S1", "offer_id": "P000101-S1", "display_name_ru": "красный 500 мл"},
                {"source_sku_id": "S2", "offer_id": "P000101-S2", "display_name_ru": "черный 1000 мл"},
            ],
        }
        config = {
            "sku_prices": [{"source_sku_id": "S1", "price": "1000"}, {"source_sku_id": "S2", "price": "1200"}],
            "sku_colors": [],
            "brand": {"attribute_id": 85, "dictionary_value_id": 971082156, "value": "Нет бренда"},
            "model_name": {"attribute_id": 9048, "value": "123456789012"},
            "type": {"attribute_id": 8229, "dictionary_value_id": 1, "value": "Термокружка"},
            "currency_code": "CNY",
            "vat": "0",
            "old_price": None,
            "package_dimensions": {"length_mm": 999, "width_mm": 999, "height_mm": 999},
            "package_weight": {"value_g": 9999},
        }
        items = build_import_items(draft, config, ["https://img.example/detail-1.png"], final_attributes=final_attributes)
        by_offer = {item["offer_id"]: item for item in items}
        self.assertEqual(by_offer["P000101-S1"]["depth"], 140)
        self.assertEqual(by_offer["P000101-S1"]["weight"], 847)
        self.assertEqual(by_offer["P000101-S2"]["depth"], 190)
        self.assertEqual(by_offer["P000101-S2"]["weight"], 1100)
        attrs_s1 = {item["id"]: item["values"][0]["value"] for item in by_offer["P000101-S1"]["attributes"]}
        attrs_s2 = {item["id"]: item["values"][0]["value"] for item in by_offer["P000101-S2"]["attributes"]}
        self.assertEqual(attrs_s1[10097], "красный")
        self.assertEqual(attrs_s2[10097], "черный")
        self.assertEqual(attrs_s1[3001], "0.5")
        self.assertEqual(attrs_s2[3001], "1")
        self.assertNotEqual(attrs_s1[5001], attrs_s2[5001])

    def test_single_sku_common_and_sku_attributes_do_not_duplicate(self):
        product_dir = self.make_multi_sku_product(1)
        sku_ids = ["SKU-01"]
        self.write_multi_sku_attribute_inputs(product_dir, sku_ids)
        final_attributes = compile_product_attributes(product_dir)
        draft = {
            "description_category_id": 170000,
            "type_id": 900000,
            "title": "Один вариант",
            "description": "Описание",
            "skus": [
                {"source_sku_id": "SKU-01", "offer_id": "OFFER-SKU-01", "display_name_ru": "SKU-01"},
            ],
        }
        config = {
            "sku_prices": [{"source_sku_id": "SKU-01", "price": "1000"}],
            "sku_colors": [],
            "brand": {"attribute_id": 85, "dictionary_value_id": 971082156, "value": "Нет бренда"},
            "model_name": {"attribute_id": 9048, "value": "123456789012"},
            "type": {"attribute_id": 8229, "dictionary_value_id": 1, "value": "Товар"},
            "currency_code": "CNY",
            "vat": "0",
            "old_price": None,
            "package_dimensions": {"length_mm": 999, "width_mm": 999, "height_mm": 999},
            "package_weight": {"value_g": 9999},
        }
        items = build_import_items(
            draft,
            config,
            [f"https://img.example/detail-{index}.png" for index in range(1, 9)],
            final_attributes=final_attributes,
            variant_main_image_urls={"SKU-01": "https://img.example/main-SKU-01.png"},
        )
        self.assertEqual(len(items), 1)
        attribute_ids = [item["id"] for item in items[0]["attributes"]]
        self.assertEqual(len(attribute_ids), len(set(attribute_ids)))
        self.assertIn(2001, attribute_ids)
        self.assertIn(10097, attribute_ids)
        self.assertEqual(items[0]["primary_image"], "https://img.example/main-SKU-01.png")
        self.assertEqual(len(items[0]["images"]), 9)

    def test_ten_sku_snapshot_attributes_images_and_payload_bind_by_sku_id(self):
        product_dir = self.make_multi_sku_product(10)
        write_json(product_dir / "input/workbench-sku-overrides.json", {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "collection_id": "C-MULTI",
            "overrides": [
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-01", "field_name": "color", "canonical_value": "бирюзовый", "canonical_unit": "text", "updated_at": "2026-07-18T00:00:00+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-01", "field_name": "specification_text", "canonical_value": "ручной 01", "canonical_unit": "text", "updated_at": "2026-07-18T00:00:01+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-01", "field_name": "product_weight_g", "canonical_value": 1111, "canonical_unit": "g", "updated_at": "2026-07-18T00:00:02+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-01", "field_name": "product_length_mm", "canonical_value": 211, "canonical_unit": "mm", "updated_at": "2026-07-18T00:00:03+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-01", "field_name": "product_width_mm", "canonical_value": 121, "canonical_unit": "mm", "updated_at": "2026-07-18T00:00:04+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-01", "field_name": "product_height_mm", "canonical_value": 81, "canonical_unit": "mm", "updated_at": "2026-07-18T00:00:05+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-06", "field_name": "color", "canonical_value": "оливковый", "canonical_unit": "text", "updated_at": "2026-07-18T00:00:06+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-06", "field_name": "specification_text", "canonical_value": "ручной 06", "canonical_unit": "text", "updated_at": "2026-07-18T00:00:07+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-06", "field_name": "product_weight_g", "canonical_value": 1666, "canonical_unit": "g", "updated_at": "2026-07-18T00:00:08+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-06", "field_name": "product_length_mm", "canonical_value": 266, "canonical_unit": "mm", "updated_at": "2026-07-18T00:00:09+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-06", "field_name": "product_width_mm", "canonical_value": 166, "canonical_unit": "mm", "updated_at": "2026-07-18T00:00:10+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-06", "field_name": "product_height_mm", "canonical_value": 126, "canonical_unit": "mm", "updated_at": "2026-07-18T00:00:11+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-10", "field_name": "color", "canonical_value": "графитовый", "canonical_unit": "text", "updated_at": "2026-07-18T00:00:12+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-10", "field_name": "specification_text", "canonical_value": "ручной 10", "canonical_unit": "text", "updated_at": "2026-07-18T00:00:13+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-10", "field_name": "product_weight_g", "canonical_value": 1999, "canonical_unit": "g", "updated_at": "2026-07-18T00:00:14+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-10", "field_name": "product_length_mm", "canonical_value": 299, "canonical_unit": "mm", "updated_at": "2026-07-18T00:00:15+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-10", "field_name": "product_width_mm", "canonical_value": 199, "canonical_unit": "mm", "updated_at": "2026-07-18T00:00:16+00:00"},
                {"product_id": product_dir.name, "collection_id": "C-MULTI", "sku_id": "SKU-10", "field_name": "product_height_mm", "canonical_value": 159, "canonical_unit": "mm", "updated_at": "2026-07-18T00:00:17+00:00"},
            ],
        })
        snapshot = freeze_sku_run_snapshot(
            product_dir,
            batch_id="B-TEN-SKU",
            review_mode="manual",
            auto_upload=False,
            target_store_ids=["store-a", "store-b"],
        )
        self.assertEqual(snapshot["selected_sku_count"], 10)
        self.assertEqual(len(snapshot["sku_rows"]), 10)
        self.assertEqual(len({item["sku_id"] for item in snapshot["sku_rows"]}), 10)
        snapshot_rows = {item["sku_id"]: item for item in snapshot["sku_rows"]}
        self.assertEqual(canonical(snapshot_rows["SKU-01"]["color"]), "бирюзовый")
        self.assertEqual(canonical(snapshot_rows["SKU-06"]["product_weight"]), 1666)
        self.assertEqual(canonical(snapshot_rows["SKU-10"]["product_dimensions"])["length_mm"], 299)
        self.assertEqual(canonical(snapshot_rows["SKU-02"]["product_weight"]), 250)

        fill_input = {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "collection_id": "C-MULTI",
            "category_id": 170000,
            "type_id": 900000,
            "selected_skus": [{"sku_id": sku_id} for sku_id in reversed(list(snapshot_rows))],
            "sku_rows": snapshot["sku_rows"],
            "ozon_attributes": [
                {"attribute_id": 2001, "attribute_name": "Материал", "type": "String", "required": True, "is_aspect": False, "allowed_values": [{"value": "Нержавеющая сталь", "dictionary_value_id": 77}]},
                {"attribute_id": 10097, "attribute_name": "Название цвета", "type": "String", "required": True, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 3001, "attribute_name": "Объем, л", "type": "Decimal", "required": True, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 3101, "attribute_name": "Размер", "type": "String", "required": False, "is_aspect": True, "allowed_values": []},
                {"attribute_id": 5001, "attribute_name": "Вес с упаковкой, г", "type": "Integer", "required": False, "is_aspect": True, "allowed_values": []},
            ],
            "input_hash": "ten-sku-fill-input",
        }
        attributes_by_sku = {}
        for row in snapshot["sku_rows"]:
            sku_id = row["sku_id"]
            attributes_by_sku[sku_id] = [
                {"attribute_id": 10097, "attribute_name": "Название цвета", "scope": "sku", "decision_status": "filled", "raw_semantic_value": canonical(row["color"]), "canonical_value": canonical(row["color"]), "canonical_unit": "text", "ozon_value": canonical(row["color"]), "source": row["color"]["source"], "mapping_method": row["color"]["mapping_method"]},
                {"attribute_id": 3001, "attribute_name": "Объем, л", "scope": "sku", "decision_status": "filled", "raw_semantic_value": canonical(row["capacity"]), "canonical_value": canonical(row["capacity"]), "canonical_unit": "ml", "ozon_value": canonical(row["capacity"]), "source": row["capacity"]["source"], "mapping_method": row["capacity"]["mapping_method"]},
                {"attribute_id": 3101, "attribute_name": "Размер", "scope": "sku", "decision_status": "filled", "raw_semantic_value": canonical(row["specification"]), "canonical_value": canonical(row["specification"]), "canonical_unit": "text", "ozon_value": canonical(row["specification"]), "source": row["specification"]["source"], "mapping_method": row["specification"]["mapping_method"]},
                {"attribute_id": 5001, "attribute_name": "Вес с упаковкой, г", "scope": "sku", "decision_status": "filled", "raw_semantic_value": canonical(row["package_weight"]), "canonical_value": canonical(row["package_weight"]), "canonical_unit": "g", "ozon_value": canonical(row["package_weight"]), "source": row["package_weight"]["source"], "mapping_method": row["package_weight"]["mapping_method"]},
            ]
        write_json(product_dir / "output/attribute-fill-input.json", fill_input)
        write_json(product_dir / "output/ozon-ecommerce-design.json", {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "attribute_decisions": {
                "common_attributes": [
                    {"attribute_id": 2001, "attribute_name": "Материал", "scope": "common", "decision_status": "filled", "raw_semantic_value": "нержавеющая сталь", "ozon_value": "Нержавеющая сталь", "dictionary_value_id": 77, "source": "1688", "mapping_method": "AI_semantic_match"}
                ],
                "attributes_by_sku": attributes_by_sku,
            },
        })
        final_attributes = compile_product_attributes(product_dir)
        self.assertEqual(set(final_attributes["attributes_by_sku"]), set(snapshot_rows))
        self.assertEqual(len(final_attributes["attributes_by_sku"]), 10)

        sku_order_for_payload = ["SKU-10", "SKU-01", "SKU-06"] + [f"SKU-{index:02d}" for index in range(2, 10) if index != 6]
        draft = {
            "description_category_id": 170000,
            "type_id": 900000,
            "title": "Товар с десятью вариантами",
            "description": "Описание",
            "skus": [
                {"source_sku_id": sku_id, "offer_id": f"OFFER-{sku_id}", "display_name_ru": sku_id}
                for sku_id in sku_order_for_payload
            ],
        }
        config = {
            "sku_prices": [
                {"source_sku_id": sku_id, "price": str(1000 + index * 10)}
                for index, sku_id in enumerate(reversed(sku_order_for_payload), start=1)
            ],
            "sku_colors": [],
            "brand": {"attribute_id": 85, "dictionary_value_id": 971082156, "value": "Нет бренда"},
            "model_name": {"attribute_id": 9048, "value": "123456789012"},
            "type": {"attribute_id": 8229, "dictionary_value_id": 1, "value": "Товар"},
            "currency_code": "CNY",
            "vat": "0",
            "old_price": None,
            "package_dimensions": {"length_mm": 999, "width_mm": 999, "height_mm": 999},
            "package_weight": {"value_g": 9999},
        }
        detail_urls = [f"https://img.example/detail-{index}.png" for index in range(1, 9)]
        variant_urls = {sku_id: f"https://img.example/main-{sku_id}.png" for sku_id in snapshot_rows}
        image_plan = {
            "main_images": [{"slot": f"main-{sku_id}", "sku_id": sku_id, "output_path": variant_urls[sku_id]} for sku_id in snapshot_rows],
            "detail_images": [{"slot": f"detail-{index:03d}", "output_path": url} for index, url in enumerate(detail_urls, start=1)],
        }
        self.assertEqual(len(image_plan["main_images"]), 10)
        self.assertEqual(len(image_plan["detail_images"]), 8)
        self.assertEqual(len(image_plan["main_images"]) + len(image_plan["detail_images"]), 18)
        items = build_import_items(
            draft,
            config,
            detail_urls,
            final_attributes=final_attributes,
            variant_main_image_urls=variant_urls,
        )
        self.assertEqual(len(items), 10)
        by_offer = {item["offer_id"]: item for item in items}
        for sku_id, row in snapshot_rows.items():
            item = by_offer[f"OFFER-{sku_id}"]
            attrs = {attr["id"]: attr["values"][0]["value"] for attr in item["attributes"]}
            self.assertEqual(attrs[10097], str(canonical(row["color"])))
            self.assertEqual(attrs[3101], str(canonical(row["specification"])))
            self.assertEqual(item["depth"], int(canonical(row["package_dimensions"])["length_mm"]))
            self.assertEqual(item["width"], int(canonical(row["package_dimensions"])["width_mm"]))
            self.assertEqual(item["height"], int(canonical(row["package_dimensions"])["height_mm"]))
            self.assertEqual(item["weight"], int(canonical(row["package_weight"])))
            self.assertEqual(item["primary_image"], variant_urls[sku_id])
            self.assertEqual(item["images"][1:], detail_urls)


if __name__ == "__main__":
    unittest.main()
