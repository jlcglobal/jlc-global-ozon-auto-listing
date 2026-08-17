import copy
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-field-completion"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "scripts"))

from ozon_field_completion import build_package, validate_package  # noqa: E402
from ozon_field_completion.service import (  # noqa: E402
    _auto_upload_config,
    _find_model_attribute,
    _resolve_product_model_name,
    _reliable_dynamic_attributes,
    _stable_random_model_name,
    build_attributes,
    build_color_variants,
    build_color_variant_policy,
    build_tags,
    sync_draft_attributes_from_final_attributes,
    sync_draft_images_from_image_plan,
)
from ozon_uploader.service import build_preflight, load_json  # noqa: E402
from attribute_fill_input import (  # noqa: E402
    apply_upload_config_measurement_fallback,
    build_attribute_fill_input,
)
from ozon_attribute_compiler import compile_product_attributes  # noqa: E402


VALID_HASHTAGS = [
    "#канистра", "#топливо", "#металл", "#сталь", "#гараж", "#мастерская",
    "#техника", "#емкость", "#хранение", "#переноска", "#ручка", "#крышка",
    "#автотовары", "#поездка", "#дача", "#запас", "#закрытая", "#квадратная",
    "#прочная", "#большая", "#удобная", "#практичная", "#покупка", "#товар",
    "#дом", "#работа", "#сервис", "#резерв", "#комплект", "#выбор",
]


class ActiveFieldCompletionContractTest(unittest.TestCase):
    def test_dimensions_follow_each_ozon_fields_displayed_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000123"
            (product / "input").mkdir(parents=True)
            (product / "input/source.json").write_text(json.dumps({
                "product_id": "P000123",
                "product_attributes": [{
                    "name_cn": "规格(长*宽*高)",
                    "value_cn": "32cm*31cm*58cm",
                }],
                "skus": [],
            }, ensure_ascii=False), encoding="utf-8")
            metadata = {"attributes": [
                {"attribute_id": 1, "attribute_name": "Длина, мм"},
                {"attribute_id": 2, "attribute_name": "Ширина, см"},
                {"attribute_id": 3, "attribute_name": "Высота, см"},
                {"attribute_id": 4, "attribute_name": "Ширина сиденья, см"},
                {"attribute_id": 5, "attribute_name": "Высота спинки, см"},
            ]}
            result = _reliable_dynamic_attributes(product, metadata)
        self.assertEqual(result[1][0], 320)
        self.assertEqual(result[2][0], 31)
        self.assertEqual(result[3][0], 58)
        self.assertNotIn(4, result)
        self.assertNotIn(5, result)

    def test_legacy_designer_tags_are_normalized_to_current_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000123"
            output = product / "output"
            output.mkdir(parents=True)
            tags = ["#канистра_для_гсм", "#канистра_50л", "#50_литров", *VALID_HASHTAGS[:27]]
            (output / "ozon-tags.json").write_text(json.dumps({
                "product_id": "P000123",
                "tags": tags,
                "source_ref": "products/P000123/output/ozon-ecommerce-design.json",
            }, ensure_ascii=False), encoding="utf-8")
            result = build_tags(product)
            self.assertEqual(set(result), {
                "schema_version", "product_id", "tags", "count",
                "language", "source_refs", "warnings",
            })
            # Invalid legacy model/size tags are removed.  Tags are optional
            # and may contain fewer than Ozon's maximum of 30 values.
            self.assertEqual(result["count"], 27)
            self.assertTrue(all(re.fullmatch(r"#[А-Яа-яЁё]+", item) for item in result["tags"]))
            self.assertFalse(any("_" in item or any(ch.isdigit() for ch in item) for item in result["tags"]))
            schema = load_json(ROOT / "templates/ozon-tags.schema.json")
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(result)), [])

    def test_designer_tags_are_grounded_to_current_product_context(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000124"
            output = product / "output"
            output.mkdir(parents=True)
            (output / "title-ru.json").write_text(json.dumps({
                "title_ru": "Кофеварка для колд брю и холодного кофе"
            }, ensure_ascii=False), encoding="utf-8")
            (output / "copy-ru.json").write_text(json.dumps({
                "title_ru": "Кофеварка для колд брю",
                "description_ru": "Кофеварка помогает готовить холодный кофе дома и в кофейной зоне.",
                "keywords_ru": ["кофеварка для колд брю", "кофеварка для холодного кофе", "холодный кофе дома"],
            }, ensure_ascii=False), encoding="utf-8")
            (output / "ozon-ecommerce-design.json").write_text(json.dumps({
                "listing": {
                    "seo_title_ru": "Кофеварка для колд брю и холодного кофе",
                    "description_ru": "Практичная кофеварка для холодного кофе дома.",
                    "keywords": {
                        "primary": [
                            {"text_ru": "кофеварка для колд брю"},
                            {"text_ru": "кофеварка для холодного кофе"},
                        ],
                        "scene": [{"text_ru": "холодный кофе дома"}],
                    },
                    "hashtags": [
                        "#кофеварка",
                        "#колдбрю",
                        "#кофеваркадляхолодногокофе",
                        "#канистрадлятоплива",
                        "#металлическаяканистра",
                    ],
                }
            }, ensure_ascii=False), encoding="utf-8")

            result = build_tags(product)

            self.assertIn("#кофеварка", result["tags"])
            self.assertIn("#колдбрю", result["tags"])
            self.assertIn("#кофеваркадляхолодногокофе", result["tags"])
            self.assertNotIn("#канистрадлятоплива", result["tags"])
            self.assertNotIn("#металлическаяканистра", result["tags"])
            self.assertTrue(any("语义不一致" in warning for warning in result["warnings"]))

    def test_model_name_is_random_looking_stable_and_product_scoped(self):
        source = {
            "product_id": "P000123",
            "collection_id": "COL-123",
            "source_url": "https://detail.1688.com/offer/123.html",
        }
        first = _stable_random_model_name(Path("/tmp/a/P000123"), source)
        retry = _stable_random_model_name(Path("/tmp/store-copy/P000123"), source)
        other = _stable_random_model_name(
            Path("/tmp/a/P000124"), {**source, "product_id": "P000124"}
        )
        self.assertRegex(first, r"^\d{12}$")
        self.assertEqual(retry, first)
        self.assertNotEqual(other, first)

    def test_empty_draft_images_are_restored_from_current_image_plan(self):
        products_root = ROOT / "products"
        products_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=products_root) as directory:
            product = Path(directory) / "P000123"
            output = product / "output"
            (output / "generated-images/variant-main").mkdir(parents=True)
            (output / "generated-images/detail").mkdir(parents=True)
            main_image = output / "generated-images/variant-main/SKU-1.png"
            detail_image = output / "generated-images/detail/detail-001.png"
            main_image.write_bytes(b"fake-main")
            detail_image.write_bytes(b"fake-detail")
            (output / "ozon-draft.json").write_text(json.dumps({
                "schema_version": "1.0.0",
                "product_id": "P000123",
                "images": [],
            }, ensure_ascii=False), encoding="utf-8")
            (output / "image-plan.json").write_text(json.dumps({
                "schema_version": "1.0.0",
                "product_id": "P000123",
                "main_images": [{
                    "slot": "main-SKU-1",
                    "output_path": "output/generated-images/variant-main/SKU-1.png",
                    "source_sku_id": "SKU-1",
                    "variant_scope": "sku",
                    "variant_kind": "size_or_measurement",
                    "variant_value": "1 л",
                    "reference_images": ["products/P000123/input/sku-images/sku-001.jpg"],
                    "status": "generated",
                }],
                "detail_images": [{
                    "slot": "detail-001",
                    "output_path": "output/generated-images/detail/detail-001.png",
                    "reference_images": ["products/P000123/input/main-images/main-001.jpg"],
                    "status": "generated",
                }],
            }, ensure_ascii=False), encoding="utf-8")
            (output / "image-qc-report.json").write_text(json.dumps({
                "decision": "pass",
                "images_checked": [
                    {"slot": "main-SKU-1", "path": str(main_image)},
                    {"slot": "detail-001", "path": str(detail_image)},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            draft = sync_draft_images_from_image_plan(product, write=True)
            restored = json.loads((output / "ozon-draft.json").read_text(encoding="utf-8"))
        self.assertEqual(len(draft["images"]), 2)
        self.assertEqual(restored["images"], draft["images"])
        self.assertEqual(draft["images"][0]["role"], "main")
        self.assertEqual(draft["images"][0]["source_sku_id"], "SKU-1")
        self.assertEqual(draft["images"][0]["qc_status"], "pass")
        self.assertEqual(draft["images"][1]["role"], "detail")
        self.assertTrue(draft["images"][0]["path"].endswith("P000123/output/generated-images/variant-main/SKU-1.png"))

    def test_draft_skus_are_refreshed_with_sku_specific_attributes(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000123"
            output = product / "output"
            output.mkdir(parents=True)
            (output / "ozon-draft.json").write_text(json.dumps({
                "schema_version": "1.0.0",
                "product_id": "P000123",
                "attributes": [],
                "skus": [
                    {"source_sku_id": "sku-1", "attributes": []},
                    {"source_sku_id": "sku-2", "attributes": []},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            final_attributes = {
                "common_attributes": [{
                    "attribute_id": 2001,
                    "attribute_name": "Материал",
                    "value": "Пластик",
                    "dictionary_value_id": 11,
                    "source": "1688",
                }],
                "attributes_by_sku": {
                    "sku-1": [{
                        "attribute_id": 10097,
                        "attribute_name": "Название цвета",
                        "value": "красный",
                        "source": "1688",
                    }],
                    "sku-2": [{
                        "attribute_id": 10097,
                        "attribute_name": "Название цвета",
                        "value": "черный",
                        "source": "1688",
                    }],
                },
            }

            refreshed = sync_draft_attributes_from_final_attributes(
                product, final_attributes, write=True
            )
            saved = json.loads((output / "ozon-draft.json").read_text(encoding="utf-8"))

        self.assertEqual(saved, refreshed)
        self.assertEqual(saved["attributes"][0]["attribute_id"], 2001)
        by_sku = {item["source_sku_id"]: item for item in saved["skus"]}
        sku_1_attrs = {item["attribute_id"]: item for item in by_sku["sku-1"]["attributes"]}
        sku_2_attrs = {item["attribute_id"]: item for item in by_sku["sku-2"]["attributes"]}
        self.assertEqual(sku_1_attrs[2001]["values"][0]["value"], "Пластик")
        self.assertEqual(sku_1_attrs[10097]["values"][0]["value"], "красный")
        self.assertEqual(sku_2_attrs[10097]["values"][0]["value"], "черный")

    def test_generated_sku_main_image_satisfies_main_color_variant_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / "products/P000123"
            output = product / "output"
            image_path = output / "generated-images/variant-main/SKU-1.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"fake-main")
            source = {
                "skus": [{
                    "sku_id": "SKU-1",
                    "sku_name": "皮板3件套 红色",
                    "selection_order": 1,
                }]
            }
            (output / "image-plan.json").write_text(json.dumps({
                "schema_version": "1.0.0",
                "product_id": "P000123",
                "main_images": [{
                    "slot": "main-SKU-1",
                    "image_type": "main",
                    "layout_type": "sku_main",
                    "variant_scope": "sku",
                    "source_sku_id": "SKU-1",
                    "variant_kind": "seller_specification",
                    "output_path": "products/P000123/output/generated-images/variant-main/SKU-1.png",
                }],
                "detail_images": [],
            }, ensure_ascii=False), encoding="utf-8")
            (output / "image-qc-report.json").write_text(json.dumps({
                "decision": "pass",
                "images_checked": [{"slot": "main-SKU-1"}],
            }, ensure_ascii=False), encoding="utf-8")

            colors = build_color_variants(product, source)
            policy = build_color_variant_policy("P000123", source, colors)

        self.assertEqual(colors["summary"], {"total": 1, "mapped": 1, "missing": 0})
        self.assertEqual(colors["variants"][0]["image"], "products/P000123/output/generated-images/variant-main/SKU-1.png")
        self.assertEqual(colors["variants"][0]["status"], "mapped")
        self.assertEqual(policy["status"], "PASS")

    def test_required_model_attribute_is_preferred_and_existing_value_is_not_reused(self):
        metadata = {"attributes": [
            {
                "attribute_id": 12141,
                "attribute_name": "Название модели для шаблона наименования",
                "required": False,
            },
            {
                "attribute_id": 9048,
                "attribute_name": "Название модели (для объединения в одну карточку)",
                "required": True,
            },
        ]}
        self.assertEqual(_find_model_attribute(metadata)["attribute_id"], 9048)
        value, source = _resolve_product_model_name(
            Path("/tmp/P000123"),
            {"product_id": "P000123", "collection_id": "COL-123"},
            "582904173625",
            "operator_confirmed",
        )
        self.assertRegex(value, r"^\d{12}$")
        self.assertNotEqual(value, "582904173625")
        self.assertEqual(source, "stable_random_numeric_v1")

    def test_upload_config_accepts_category_without_model_attribute(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000127"
            (product / "input").mkdir(parents=True)
            (product / "output").mkdir(parents=True)
            (product / "input/source.json").write_text(json.dumps({
                "product_id": "P000127",
                "collection_id": "COL-127",
                "source_url": "https://detail.1688.com/offer/127.html",
                "skus": [{"sku_id": "sku-1", "sku_name": "Черный"}],
            }, ensure_ascii=False), encoding="utf-8")
            (product / "output/pricing-result.json").write_text(json.dumps({
                "sku_pricing": [{"sku_id": "sku-1", "selling_price_cny": 100}]
            }, ensure_ascii=False), encoding="utf-8")
            measurements = {
                "dimensions": {"length": 10, "width": 8, "height": 3, "source_ref": "test", "estimated": True},
                "weight": {"value": 80, "source_ref": "test", "estimated": True},
                "product_dimensions": {"length": 10, "width": 8, "height": 3, "source_ref": "test", "estimated": True},
                "product_weight": {"value": 80, "source_ref": "test", "estimated": True},
                "package_dimensions": {"length": 11, "width": 9, "height": 4, "source_ref": "test", "estimated": True},
                "package_weight": {"value": 100, "source_ref": "test", "estimated": True},
            }
            (product / "output/cost-analysis.json").write_text(json.dumps(measurements, ensure_ascii=False), encoding="utf-8")
            (product / "output/ozon-category.json").write_text(json.dumps({
                "category_id": 41777465,
                "type_id": 93171,
                "category_name": "Перчатки",
                "confidence": 1,
                "match_status": "api_confirmed",
            }, ensure_ascii=False), encoding="utf-8")
            (product / "output/title-ru.json").write_text(json.dumps({"title_ru": "Перчатки с подогревом"}, ensure_ascii=False), encoding="utf-8")
            config = _auto_upload_config(product, {
                "category_id": 41777465,
                "type_id": 93171,
                "attributes": [
                    {
                        "attribute_id": 31,
                        "attribute_name": "Бренд в одежде и обуви",
                        "required": True,
                        "allowed_values": [{"id": 126745801, "value": "Нет бренда"}],
                    },
                    {
                        "attribute_id": 8229,
                        "attribute_name": "Тип",
                        "required": True,
                        "allowed_values": [{"id": 93171, "value": "Перчатки"}],
                    },
                ],
            }, allow_unpriced=True)
        self.assertEqual(config["brand"]["attribute_id"], 31)
        self.assertEqual(config["brand"]["dictionary_value_id"], 126745801)
        self.assertEqual(config["model_name"]["attribute_id"], 0)
        self.assertRegex(config["model_name"]["value"], r"^\d{12}$")

    def test_russian_size_defaults_to_universal_when_no_size_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000128"
            output = product / "output"
            output.mkdir(parents=True)
            (output / "attribute-fill-input.json").write_text(json.dumps({
                "product_id": "P000128",
                "category_id": 41777465,
                "type_id": 93171,
                "selected_skus": [
                    {"sku_id": "sku-black", "sku_name": "Черный, три режима"},
                    {"sku_id": "sku-blue", "sku_name": "Синий, три режима"},
                ],
                "sku_rows": [
                    {"sku_id": "sku-black", "specification": "черный"},
                    {"sku_id": "sku-blue", "specification": "синий"},
                ],
                "ozon_attributes": [{
                    "attribute_id": 4295,
                    "attribute_name": "Российский размер",
                    "required": True,
                    "is_aspect": True,
                    "type": "String",
                    "allowed_values": [{"id": 35646, "value": "универсальный"}],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            (output / "ozon-ecommerce-design.json").write_text(json.dumps({
                "attribute_decisions": {"common_attributes": [], "attributes_by_sku": {}}
            }, ensure_ascii=False), encoding="utf-8")
            result = compile_product_attributes(product)
        for sku_id in ("sku-black", "sku-blue"):
            values = result["attributes_by_sku"][sku_id]
            size = next(item for item in values if item["attribute_id"] == 4295)
            self.assertEqual(size["value"], "универсальный")
            self.assertEqual(size["dictionary_value_id"], 35646)
            self.assertEqual(size["source"], "AI_estimated")
            self.assertEqual(size["mapping_method"], "deterministic_universal_size_default")

    def test_optional_numeric_aspect_uses_sku_size_rank_when_ozon_requires_variant_column(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000136"
            output = product / "output"
            output.mkdir(parents=True)
            (output / "attribute-fill-input.json").write_text(json.dumps({
                "product_id": "P000136",
                "category_id": 17028741,
                "type_id": 92533,
                "selected_skus": [
                    {"sku_id": "small", "sku_name": "胡桃色凹槽托盘小号"},
                    {"sku_id": "large", "sku_name": "胡桃色凹槽托盘大号"},
                    {"sku_id": "middle", "sku_name": "胡桃色凹槽托盘中号"},
                ],
                "sku_rows": [
                    {"sku_id": "small", "specification": {"canonical_value": "胡桃色凹槽托盘小号"}},
                    {"sku_id": "large", "specification": {"canonical_value": "胡桃色凹槽托盘大号"}},
                    {"sku_id": "middle", "specification": {"canonical_value": "胡桃色凹槽托盘中号"}},
                ],
                "measurements": {
                    "product_dimensions": {
                        "length_mm": 300,
                        "width_mm": 200,
                        "height_mm": 150,
                        "source": "pricing_rules.measurement_profiles.default",
                    }
                },
                "ozon_attributes": [{
                    "attribute_id": 6432,
                    "attribute_name": "Диаметр, см",
                    "required": False,
                    "is_aspect": True,
                    "type": "Decimal",
                    "allowed_values": [],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            (output / "ozon-ecommerce-design.json").write_text(json.dumps({
                "attribute_decisions": {
                    "common_attributes": [],
                    "attributes_by_sku": {
                        sku_id: [{
                            "attribute_id": 6432,
                            "attribute_name": "Диаметр, см",
                            "scope": "sku",
                            "decision_status": "skipped_optional",
                            "ozon_value": None,
                        }]
                        for sku_id in ("small", "large", "middle")
                    },
                }
            }, ensure_ascii=False), encoding="utf-8")

            result = compile_product_attributes(product)

        values = {
            sku_id: next(item for item in attrs if item["attribute_id"] == 6432)
            for sku_id, attrs in result["attributes_by_sku"].items()
        }
        self.assertEqual(values["small"]["value"], 20.1)
        self.assertEqual(values["middle"]["value"], 24.9)
        self.assertEqual(values["large"]["value"], 30)
        self.assertTrue(all(item["source"] == "AI_estimated" for item in values.values()))
        self.assertTrue(all(item["mapping_method"] == "deterministic_size_rank_aspect_estimate" for item in values.values()))

    def test_unisex_source_gender_compiles_to_male_and_female_dictionary_values(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000130"
            output = product / "output"
            output.mkdir(parents=True)
            (output / "attribute-fill-input.json").write_text(json.dumps({
                "product_id": "P000130",
                "category_id": 170000,
                "type_id": 900000,
                "selected_skus": [{"sku_id": "sku-1", "sku_name": "Черный"}],
                "sku_rows": [{"sku_id": "sku-1", "specification": "черный"}],
                "merged_facts": {
                    "title_cn": "双肩包男女通用",
                    "structured_attributes": {
                        "适用性别": {
                            "name": "适用性别",
                            "value": "中性/男女均可",
                            "value_cn": "中性/男女均可",
                            "source_text": "容量36-55L适用性别中性/男女均可",
                            "source_ref": "input/source.json.product_attributes",
                        }
                    },
                },
                "ozon_attributes": [{
                    "attribute_id": 9163,
                    "attribute_name": "Пол",
                    "required": True,
                    "is_aspect": False,
                    "is_collection": True,
                    "max_value_count": 1,
                    "type": "String",
                    "allowed_values": [
                        {"id": 22880, "value": "Мужской"},
                        {"id": 22881, "value": "Женский"},
                        {"id": 22882, "value": "Девочки"},
                        {"id": 22883, "value": "Мальчики"},
                    ],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            (output / "ozon-ecommerce-design.json").write_text(json.dumps({
                "attribute_decisions": {
                    "common_attributes": [{
                        "attribute_id": 9163,
                        "attribute_name": "Пол",
                        "scope": "common",
                        "decision_status": "unknown_high_risk",
                        "ozon_value": "unknown",
                    }],
                    "attributes_by_sku": {},
                }
            }, ensure_ascii=False), encoding="utf-8")

            result = compile_product_attributes(product)

        gender = next(item for item in result["common_attributes"] if item["attribute_id"] == 9163)
        self.assertEqual(gender["value"], "Мужской; Женский")
        self.assertIsNone(gender["dictionary_value_id"])
        self.assertEqual(
            gender["dictionary_values"],
            [
                {"dictionary_value_id": 22880, "value": "Мужской"},
                {"dictionary_value_id": 22881, "value": "Женский"},
            ],
        )
        self.assertEqual(result["required_summary"]["missing"], 0)

    def test_russian_size_is_not_forced_when_concrete_size_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000129"
            output = product / "output"
            output.mkdir(parents=True)
            (output / "attribute-fill-input.json").write_text(json.dumps({
                "product_id": "P000129",
                "category_id": 41777465,
                "type_id": 93171,
                "selected_skus": [{"sku_id": "sku-m", "sku_name": "Размер M"}],
                "sku_rows": [{"sku_id": "sku-m", "option_values": [{"name_cn": "尺码", "value_cn": "M"}]}],
                "ozon_attributes": [{
                    "attribute_id": 4295,
                    "attribute_name": "Российский размер",
                    "required": True,
                    "is_aspect": True,
                    "type": "String",
                    "allowed_values": [{"id": 35646, "value": "универсальный"}],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            (output / "ozon-ecommerce-design.json").write_text(json.dumps({
                "attribute_decisions": {"common_attributes": [], "attributes_by_sku": {}}
            }, ensure_ascii=False), encoding="utf-8")
            result = compile_product_attributes(product)
        size = next(item for item in result["attributes_by_sku"]["sku-m"] if item["attribute_id"] == 4295)
        self.assertEqual(size["value"], "unknown")

    def test_all_model_name_aliases_share_stable_numeric_and_ignore_workbench_override(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000123"
            (product / "input").mkdir(parents=True)
            (product / "output").mkdir(parents=True)
            source = {
                "product_id": "P000123",
                "collection_id": "COL-123",
                "source_url": "https://detail.1688.com/offer/123.html",
                "skus": [],
            }
            (product / "input/source.json").write_text(
                json.dumps(source, ensure_ascii=False), encoding="utf-8"
            )
            (product / "output/workbench-draft.json").write_text(
                json.dumps({"attributes": {"9048": "人工旧值", "12141": "标题旧值"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (product / "output/product-analysis.json").write_text(
                json.dumps({"product_type": "Товар", "category": "Тест"}, ensure_ascii=False),
                encoding="utf-8",
            )
            model = _stable_random_model_name(product, source)
            attrs = build_attributes(
                product,
                {
                    "category_id": 1,
                    "type_id": 2,
                    "attributes": [
                        {
                            "attribute_id": 12141,
                            "attribute_name": "Название модели для шаблона наименования",
                            "required": False,
                        },
                        {
                            "attribute_id": 9048,
                            "attribute_name": "Название модели (для объединения в одну карточку)",
                            "required": True,
                        },
                    ],
                },
                {
                    "model_name": {"attribute_id": 9048, "value": model},
                    "brand": {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 971082156},
                    "type": {"attribute_id": 8229, "value": "Товар", "dictionary_value_id": 2},
                    "merge_product_name": "SEO товар",
                    "product_weight": {"value_g": 100, "source": "test", "source_status": "estimated_system"},
                    "product_dimensions": {"length_mm": 100, "width_mm": 100, "height_mm": 100, "source": "test", "source_status": "estimated_system"},
                    "package_weight": {"value_g": 120, "source": "test", "source_status": "estimated_system"},
                    "package_dimensions": {"length_mm": 110, "width_mm": 110, "height_mm": 110, "source": "test", "source_status": "estimated_system"},
                },
                {"description_ru": "Описание"},
                {"tags": ["#товар"]},
                {"serialized_json": "unknown"},
            )
            by_id = {item["attribute_id"]: item for item in attrs["attributes"]}
            self.assertEqual(by_id[9048]["value"], model)
            self.assertEqual(by_id[12141]["value"], model)
            self.assertEqual(by_id[9048]["source"], "AI_estimated")
            self.assertEqual(by_id[12141]["source"], "AI_estimated")

    def test_manual_workbench_tags_keep_only_valid_russian_search_hashtags(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000123"
            output = product / "output"
            output.mkdir(parents=True)
            tags = VALID_HASHTAGS[:30]
            (output / "workbench-draft.json").write_text(
                json.dumps({"tags": tags}, ensure_ascii=False), encoding="utf-8"
            )
            result = build_tags(product)
            self.assertEqual(result["count"], 30)
            self.assertEqual(result["tags"], tags)
            invalid = ["#товар_1000", "#бренд123", *VALID_HASHTAGS[:28]]
            (output / "workbench-draft.json").write_text(
                json.dumps({"tags": invalid}, ensure_ascii=False), encoding="utf-8"
            )
            normalized = build_tags(product)
            self.assertEqual(normalized["count"], 28)
            self.assertTrue(all(re.fullmatch(r"#[А-Яа-яЁё]+", item) for item in normalized["tags"]))
            (output / "workbench-draft.json").write_text(
                json.dumps({"tags": tags[:-1]}, ensure_ascii=False), encoding="utf-8"
            )
            completed = build_tags(product)
            self.assertEqual(completed["count"], 29)
            self.assertEqual(len(set(completed["tags"])), 29)

    def test_missing_main_sku_image_blocks_but_optional_missing_only_warns(self):
        source = {"skus": [
            {"sku_id": "sku-main", "selection_order": 1},
            {"sku_id": "sku-other", "selection_order": 2},
        ]}
        base = [
            {"sku_id": "sku-main", "sku_name": "3 л", "status": "mapped", "reason": "ok"},
            {"sku_id": "sku-other", "sku_name": "5 л", "status": "mapped", "reason": "ok"},
        ]
        main_missing = copy.deepcopy(base)
        main_missing[0].update({"status": "missing", "reason": "没有本SKU真实图片"})
        blocked = build_color_variant_policy("P000123", source, {"variants": main_missing})
        self.assertEqual(blocked["status"], "BLOCK")
        optional_missing = copy.deepcopy(base)
        optional_missing[1].update({"status": "missing", "reason": "没有本SKU真实图片"})
        warning = build_color_variant_policy("P000123", source, {"variants": optional_missing})
        self.assertEqual(warning["status"], "WARNING")
        self.assertEqual(warning["blocking_variants"], [])

    def test_seller_ui_and_buyer_copy_locales_remain_separate(self):
        data = load_json(ROOT / "rules/ozon_content_score_benchmarks.json")
        benchmark = data["benchmarks"][0]
        self.assertEqual(benchmark["seller_ui_locale"], "zh-CN")
        self.assertEqual(benchmark["buyer_content_locale"], "ru-RU")

    def test_upload_config_measurements_fill_missing_sku_measurement_attributes(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000130"
            output = product / "output"
            (product / "input").mkdir(parents=True)
            output.mkdir(parents=True)
            (product / "input/source.json").write_text(json.dumps({
                "schema_version": "1.0.0",
                "product_id": "P000130",
                "collection_id": "COL-130",
                "source_kind": "workbench_collection",
                "title_cn": "测试商品",
                "product_attributes": [],
                "skus": [{
                    "sku_id": "sku-1",
                    "sku_name": "默认款",
                    "selected": True,
                    "option_values": [],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            (product / "input/category-selection.json").write_text(json.dumps({
                "category_id": 170000,
                "type_id": 900000,
                "category_name": "Тестовая категория",
            }, ensure_ascii=False), encoding="utf-8")
            (output / "ozon-category-attributes.json").write_text(json.dumps({
                "category_id": 170000,
                "type_id": 900000,
                "attributes": [
                    {
                        "attribute_id": 4382,
                        "attribute_name": "Размеры, мм",
                        "type": "String",
                        "required": False,
                        "is_aspect": True,
                        "allowed_values": [],
                    },
                    {
                        "attribute_id": 4383,
                        "attribute_name": "Вес товара, г",
                        "type": "Decimal",
                        "required": False,
                        "is_aspect": True,
                        "allowed_values": [],
                    },
                    {
                        "attribute_id": 4497,
                        "attribute_name": "Вес с упаковкой, г",
                        "type": "Decimal",
                        "required": False,
                        "is_aspect": True,
                        "allowed_values": [],
                    },
                ],
            }, ensure_ascii=False), encoding="utf-8")
            (output / "ozon-upload-config.json").write_text(json.dumps({
                "product_dimensions": {
                    "length_mm": 300,
                    "width_mm": 200,
                    "height_mm": 150,
                    "source": "pricing_rules.measurement_profiles.default",
                    "source_status": "estimated_system",
                },
                "product_weight": {
                    "value_g": 1000,
                    "source": "pricing_rules.measurement_profiles.default",
                    "source_status": "estimated_system",
                },
                "package_dimensions": {
                    "length_mm": 315,
                    "width_mm": 210,
                    "height_mm": 160,
                    "source": "pricing_rules.package_estimation",
                    "source_status": "estimated_system",
                },
                "package_weight": {
                    "value_g": 1150,
                    "source": "pricing_rules.package_estimation",
                    "source_status": "estimated_system",
                },
            }, ensure_ascii=False), encoding="utf-8")
            (output / "ozon-ecommerce-design.json").write_text(json.dumps({
                "attribute_decisions": {"common_attributes": [], "attributes_by_sku": {}}
            }, ensure_ascii=False), encoding="utf-8")

            fill_input = build_attribute_fill_input(product)
            sku_row = fill_input["sku_rows"][0]
            self.assertEqual(sku_row["product_weight"]["canonical_value"], "unknown")
            self.assertEqual(sku_row["package_weight"]["canonical_value"], "unknown")
            self.assertEqual(sku_row["product_dimensions"]["canonical_value"], "unknown")

            final = compile_product_attributes(product)
            attributes = {
                item["attribute_id"]: item
                for item in final["attributes_by_sku"]["sku-1"]
            }
            self.assertNotIn(4382, attributes)
            self.assertNotIn(4383, attributes)
            self.assertNotIn(4497, attributes)

    def test_structured_existing_measurements_do_not_trigger_fallback_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Path(directory) / "P000125"
            output = product / "output"
            output.mkdir(parents=True)
            (output / "ozon-upload-config.json").write_text(json.dumps({
                "product_dimensions": {
                    "length_mm": 300,
                    "width_mm": 200,
                    "height_mm": 150,
                    "source": "pricing_rules.measurement_profiles.default",
                    "source_status": "estimated_system",
                },
                "product_weight": {
                    "value_g": 1000,
                    "source": "pricing_rules.measurement_profiles.default",
                    "source_status": "estimated_system",
                },
                "package_dimensions": {
                    "length_mm": 315,
                    "width_mm": 210,
                    "height_mm": 160,
                    "source": "pricing_rules.package_estimation",
                    "source_status": "estimated_system",
                },
                "package_weight": {
                    "value_g": 1150,
                    "source": "pricing_rules.package_estimation",
                    "source_status": "estimated_system",
                },
            }, ensure_ascii=False), encoding="utf-8")
            sku_rows = [{
                "sku_id": "sku-1",
                "product_dimensions": {
                    "canonical_value": {"length_mm": 120, "width_mm": 80, "height_mm": 30},
                    "canonical_unit": "mm",
                },
                "product_weight": {
                    "canonical_value": 200,
                    "canonical_unit": "g",
                },
            }]

            apply_upload_config_measurement_fallback(product, sku_rows)

        sku_row = sku_rows[0]
        self.assertEqual(
            sku_row["product_dimensions"]["canonical_value"],
            {"length_mm": 120, "width_mm": 80, "height_mm": 30},
        )
        self.assertEqual(sku_row["product_weight"]["canonical_value"], 200)
