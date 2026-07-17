import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "ozon-field-completion/ozon_field_completion/service.py"
SPEC = importlib.util.spec_from_file_location("source_backed_attribute_service", SERVICE_PATH)
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def attribute(attribute_id, name, values=None):
    return {
        "attribute_id": attribute_id,
        "attribute_name": name,
        "required": False,
        "allowed_values": [
            {"id": attribute_id * 100 + index, "value": value}
            for index, value in enumerate(values or [], 1)
        ],
    }


class SourceBackedAttributeInferenceTest(unittest.TestCase):
    def test_portable_juicer_fills_safe_fields_but_not_unsupported_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "products/P000001"
            write(product / "input/source.json", {
                "title_cn": "便携式充电榨汁杯",
                "skus": [
                    {"sku_name": "350ml-双层不锈钢316S"},
                    {"sku_name": "400ml-双层不锈钢304S-配盖"},
                ],
            })
            write(product / "output/product-analysis.json", {
                "product_type": "便携式充电榨汁杯",
                "category": "Миксер кухонный",
                "facts": {
                    "functions": ["卖家标题明确描述该商品可用于榨汁。"],
                    "materials": ["两个SKU均明确标注双层不锈钢。"],
                    "package_quantity": {"value": 1},
                    "accessories": ["SKU 400ml明确标注配盖。"],
                },
                "inferences": [],
            })
            write(product / "output/title-ru.json", {
                "title_ru": "Миксер кухонный портативный электрический, стакан 350/400 мл",
            })
            metadata = {"attributes": [
                attribute(1, "Тип миксера", ["Ручной", "Стационарный"]),
                attribute(2, "Вращающаяся чаша", ["Да", "Нет"]),
                attribute(3, "Планетарный механизм", ["Да", "Нет"]),
                attribute(4, "Конструктивные особенности", ["Беспроводной"]),
                attribute(5, "Материал чаши", ["Металл", "Пластик"]),
                attribute(6, "Материал корпуса", ["Нержавеющая сталь", "Пластик"]),
                attribute(7, "Название модели для шаблона наименования"),
                attribute(8, "Комплектация"),
                attribute(9, "Мощность, Вт"),
                attribute(10, "Гарантия", ["1 год"]),
            ]}
            with patch.object(service, "ROOT", root):
                values = service._safe_optional_attributes(product, metadata)
            self.assertEqual(values[1][0], "Ручной")
            self.assertEqual(values[2][0], "Нет")
            self.assertEqual(values[3][0], "Нет")
            self.assertEqual(values[4][0], "Беспроводной")
            self.assertEqual(values[5][0], "Металл")
            self.assertEqual(values[6][0], "Нержавеющая сталь")
            self.assertIn("Миксер кухонный портативный", values[7][0])
            self.assertIn("400 мл", values[8][0])
            self.assertNotIn(9, values)
            self.assertNotIn(10, values)

    def test_chinese_accessory_evidence_becomes_clean_russian_package_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "products/P000006"
            write(product / "input/source.json", {
                "title_cn": "塑料油桶",
                "skus": [{"sku_name": "10升特厚"}],
            })
            write(product / "output/product-analysis.json", {
                "product_type": "塑料油桶",
                "facts": {
                    "accessories": [
                        "内盖，SKU实物图明确标注“配内盖”（input/sku-images/sku-001.jpg）。",
                        "外旋盖，主图明确展示内盖与外盖设计（input/main-images/main-002.webp）。",
                    ],
                },
                "inferences": [],
            })
            metadata = {"attributes": [attribute(4384, "Комплектация")]}
            with patch.object(service, "ROOT", root):
                values = service._safe_optional_attributes(product, metadata)
            package_text = values[4384][0]
            self.assertEqual(
                package_text,
                "внутренняя крышка внешняя винтовая крышка",
            )
            self.assertNotRegex(package_text, r"[\u3400-\u9fff]")
            self.assertNotIn("input/", package_text)


if __name__ == "__main__":
    unittest.main()
