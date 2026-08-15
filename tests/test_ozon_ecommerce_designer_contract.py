import json
import re
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.ozon_ecommerce_designer_contract import (
    _draft_attributes,
    materialize,
    normalize_design_hashtags,
    repair_existing_buyer_copy_projection,
    validate_design,
)
from scripts.production_input_guard import write_source_manifest
from scripts.russian_seo_rules import product_specific_longtail_candidates
from scripts.sku_image_bindings import save_sku_image_binding


VALID_HASHTAGS = [
    "#органайзер", "#хранение", "#порядок", "#кухня", "#дом", "#контейнер",
    "#полка", "#шкаф", "#удобство", "#покупка", "#семья", "#быт",
    "#аккуратно", "#пространство", "#практично", "#ежедневно", "#компактно",
    "#выбор", "#товары", "#решение", "#польза", "#чистота", "#форма",
    "#посуда", "#еда", "#запасы", "#прозрачный", "#крышка", "#размер", "#набор",
]


def make_product(root: Path, product_id: str, sku_count: int) -> tuple[Path, list[dict]]:
    product = root / "products" / product_id
    for relative in ("input/sku-images", "input/main-images", "input/detail-images", "output"):
        (product / relative).mkdir(parents=True, exist_ok=True)
    skus = []
    for index in range(1, sku_count + 1):
        path = product / "input/sku-images" / f"sku-{index}.png"
        path.write_bytes(f"real-sku-{index}".encode())
        skus.append({
            "sku_id": f"sku-{index}", "sku_name": f"规格 {index}", "selection_order": index,
            "local_image_path": f"products/{product_id}/input/sku-images/sku-{index}.png",
        })
    source = {
        "product_id": product_id,
        "collection_id": f"COL-{product_id}-DESIGN",
        "source_kind": "workbench_collection",
        "source_path": f"products/{product_id}/input/source.json",
        "source_url": "https://detail.1688.com/offer/test.html",
        "title_cn": "多规格收纳用品",
        "collected_at": "2026-07-16T12:00:00+08:00",
        "captured_at": "2026-07-16T12:00:00+08:00",
        "raw_capture_file": f"products/{product_id}/input/raw-snapshot.json",
        "skus": skus, "main_images": [], "detail_images": [],
    }
    (product / "input/source.json").write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    (product / "input/raw-snapshot.json").write_text(json.dumps({"product_id": product_id}), encoding="utf-8")
    (product / "input/category-selection.json").write_text(json.dumps({
        "product_id": product_id, "category_id": 10, "type_id": 20,
    }), encoding="utf-8")
    (product / "output/ozon-category.json").write_text(json.dumps({
        "product_id": product_id,
        "category_id": 10,
        "type_id": 20,
        "category_name": "Категория",
        "category_name_ru": "Категория",
        "confidence": 1.0,
        "match_status": "api_confirmed",
    }, ensure_ascii=False), encoding="utf-8")
    (product / "output/attribute-fill-input.json").write_text(json.dumps({
        "schema_version": "1.0.0",
        "product_id": product_id,
        "collection_id": f"COL-{product_id}-DESIGN",
        "category_id": 10,
        "type_id": 20,
        "selected_skus": [item["sku_id"] for item in skus],
        "merged_facts": {},
        "ozon_attributes": [],
        "dependencies": {"fixture": True},
        "input_hash": "fixture-attribute-input-hash",
    }, ensure_ascii=False), encoding="utf-8")
    (product / "status.json").write_text(json.dumps({"status": "CONTENT_GENERATED"}), encoding="utf-8")
    write_source_manifest(product)
    return product, skus


def keyword(text: str, source_ref: str) -> dict:
    return {"text_ru": text, "intent": "commercial", "source_refs": [source_ref], "metrics": "unknown"}


def image_role(slot: str, layout: str, refs: list[str], operation: str, sku_id: str | None = None) -> dict:
    russian_text = ["ПОНЯТНАЯ ПОЛЬЗА", "ТОЧНЫЕ ХАРАКТЕРИСТИКИ"]
    if sku_id:
        russian_text = ["ОРГАНАЙЗЕР ДЛЯ ХРАНЕНИЯ", f"Вариант {sku_id}", "ПОНЯТНЫЕ ХАРАКТЕРИСТИКИ"]
    value = {
        "slot": slot,
        "image_role": "sku_main" if sku_id else layout,
        "customer_question": "Как покупатель поймет назначение и реальные свойства товара?",
        "visual_goal": f"Show a source-grounded visual proof for {slot}",
        "shot_type": "full product camera-shot" if sku_id else "buyer-decision product shot",
        "composition": f"Product-led 3:4 composition for {slot}",
        "must_show": ["real product shape", "selected SKU facts" if sku_id else "shared product facts"],
        "avoid": ["fake materials", "changed structure", "unverified claims"],
        "layout_type": layout,
        "commercial_purpose": "Помочь покупателю принять решение",
        "buyer_question": "Почему этот товар подходит покупателю?",
        "source_references": refs,
        "russian_text": russian_text,
        "prompt": (
            "Create a source-grounded 3:4 Ozon карточка товара / инфографика with the real product dominant, "
            "a distinct buyer-decision purpose, natural scene, faithful proportions, exact SKU identity, "
            "and render the complete final Russian typography in this same image-model call. "
            f"Render these exact lines once, correctly and legibly: {'; '.join(russian_text)}. "
            "Never redraw the product or invent accessories."
        ),
        "operation": operation,
        "overlay_modules": ["product_name", "benefit_section", "icon_chips"],
        "must_preserve": ["shape", "color", "SKU differences"],
        "design_rationale": f"This product-specific treatment makes {slot} answer its buyer question without using a reusable category template.",
        "art_direction": {
            "concept": f"Product-specific decision concept for {slot}",
            "scene": f"Source-grounded real usage scene created only for {slot}",
            "composition": f"Distinct asymmetrical composition created for slot {slot}",
            "product_scale_percent": 55,
            "product_position": "lower centre",
            "background": "quiet real-life environment with natural depth",
            "palette": ["#F6F1E8", "#202A30", "#55705E"],
            "lighting": "soft natural directional light",
            "typography": "clear Cyrillic hierarchy with restrained contrast",
            "iconography": "minimal source-backed marks",
            "information_hierarchy": ["product purpose", "verified benefit"],
            "negative_space": "calm upper-left area reserved for exact copy",
            "value_signal": "real context and evidence-led hierarchy create buyer trust",
            "slot_differentiation": f"The composition and decision job are unique to {slot}",
        },
        "overlay_plan": [{
            "role": (
                ["headline", "sku_badge", "callout"][index]
                if sku_id else
                ("headline" if index == 0 else "benefit")
            ),
            "text": text,
            "box": [0.05, 0.06 + index * 0.16, 0.62, 0.095 if index == 0 else 0.08],
            "font_size_ratio": 0.045 if index == 0 else 0.027,
            "font_weight": "bold",
            "text_color": "#202A30",
            "accent_color": "#55705E",
            "background_style": "none",
            "background_color": "#F6F1E8",
            "accent_style": "top_line" if index == 0 else "left_line",
            "align": "left",
            "vertical_align": "middle",
            "priority": index + 1,
        } for index, text in enumerate(russian_text)],
    }
    if sku_id:
        value["sku_id"] = sku_id
        value["overlay_modules"] = ["product_name", "capacity_badge", "benefit_section", "callout_arrows"]
    return value


def build_design(product: Path, skus: list[dict]) -> dict:
    product_id = product.name
    source_ref = f"products/{product_id}/input/source.json"
    raw_ref = f"products/{product_id}/input/raw-snapshot.json"
    category_ref = f"products/{product_id}/input/category-selection.json"
    image_refs = [str(item["local_image_path"]) for item in skus]
    source_refs = [source_ref, raw_ref, category_ref, *image_refs]
    long_section = (
        "Товар помогает организовать повседневное использование и выбрать подходящую комплектацию "
        "по фактическим параметрам каждого выбранного варианта без неподтвержденных обещаний."
    )
    layouts = ["core_benefit", "structure_callout", "usage_scene", "usage_scene", "usage_scene", "usage_scene"]
    if len(skus) > 1:
        layouts.extend(["sku_comparison", "purchase_notice"])
    else:
        layouts.extend(["purchase_notice", "purchase_notice"])
    details = []
    for index, layout in enumerate(layouts, start=1):
        refs = image_refs if layout == "sku_comparison" else [image_refs[0]]
        operation = "compose_from_real_images" if layout in {"structure_callout", "sku_comparison", "purchase_notice"} else "edit_real_image"
        details.append(image_role(f"detail-{index:03d}", layout, refs, operation))
    return {
        "schema_version": "1.0.0", "product_id": product_id,
        "collection_id": f"COL-{product_id}-DESIGN", "source_kind": "workbench_collection",
        "source_refs": source_refs,
        "product_understanding": {"product_type_ru": "товар для хранения"},
        "buyer_strategy": {"target": "покупатели Ozon", "motivation": "понятный выбор"},
        "listing": {
            "seo_title_ru": "Органайзер для хранения, практичная конструкция, выбор размера",
            "short_title_ru": "Органайзер для хранения",
            "description_ru": "\n\n".join([long_section] * 4),
            "description_sections": {key: long_section for key in (
                "product_value", "usage_scenarios", "core_advantages", "usage_method", "notices"
            )},
            "selling_points": [
                {"text_ru": "Выбор подходящего размера", "claim_type": "fact", "source_refs": [source_ref]},
                {"text_ru": "Понятные различия вариантов", "claim_type": "fact", "source_refs": [source_ref]},
                {"text_ru": "Характеристики по исходным данным", "claim_type": "fact", "source_refs": [raw_ref]},
            ],
            "keywords": {
                "primary": [keyword("органайзер для хранения", category_ref)],
                "long_tail": [keyword("органайзер с выбором размера", source_ref)],
                "scene": [keyword("организация хранения", source_ref)],
                "excluded": [keyword("сертифицированный товар", source_ref)],
            },
            "hashtags": VALID_HASHTAGS[:30],
        },
        "attribute_plan": [],
        "attribute_decisions": {
            "input_hash": "fixture-attribute-input-hash",
            "common_attributes": [],
            "attributes_by_sku": {},
            "coverage_summary": {
                "total_realtime_attributes": 0,
                "decided_attributes": 0,
                "unknown_high_risk_attribute_ids": [],
                "skipped_optional_attribute_ids": [],
            },
        },
        "sku_plan": [{
            "sku_id": item["sku_id"], "name_ru": f"Вариант {index}",
            "difference_ru": f"Размер {index}", "specification": {"index": index},
            "source_image": item["local_image_path"],
        } for index, item in enumerate(skus, start=1)],
        "visual_system": {
            "style_name": "product-specific",
            "value_impression": "Real context and visible evidence create practical value.",
            "palette_logic": "Colors come from this product and its real use environment.",
            "scene_logic": "Every image answers a different buyer question in a real scene.",
            "typography_logic": "Exact Russian copy follows the current slot hierarchy.",
            "consistency_rule": "SKU mains share a language while details use distinct compositions.",
            "anti_template_rule": "No default header, badge, benefit rail, palette or reusable product layout.",
        },
        "main_images": [image_role(
            f"main-{item['sku_id']}", "sku_main", [item["local_image_path"]],
            "edit_real_image", item["sku_id"],
        ) for item in skus],
        "detail_images": details,
        "forbidden": ["invented facts", "cross-product references", "generated output as input"],
        "decision_trace": {
            "steps": [{"name": name, "status": "completed", "evidence": [f"evidence for {name}"]} for name in (
                "product_evidence", "buyer_analysis", "selling_point_ranking", "image_sequence",
                "per_slot_art_direction", "prompt_completion", "pre_generation_validation",
            )],
            "compliance_status": "PASS", "violations": [], "attempt": 1,
        },
        "processing": {
            "step": "ecommerce_design", "status": "completed", "model_mode": "connected_codex",
            "generated_at": "2026-07-16T12:10:00+08:00", "error": None,
        },
    }


class OzonEcommerceDesignerContractTests(unittest.TestCase):
    def test_longtail_rules_do_not_match_bak_inside_tobacco(self):
        candidates = product_specific_longtail_candidates([
            "Мундштук для кальяна",
            "Не впитывает запахи табака и подходит для стандартного шланга.",
        ])

        self.assertNotIn("канистра для топлива", candidates)
        self.assertNotIn("канистра для гсм", candidates)

    def test_hashtag_normalization_removes_unsupported_fuel_and_lid_claims(self):
        design = {
            "product_understanding": {
                "product_type_ru": "стеклянная банка для продуктов",
                "grounded_facts": [
                    "прозрачный и коричневый варианты корпуса",
                    "крышка входит в выбранные SKU",
                ],
            },
            "listing": {
                "hashtags": ["#банка", "#емкостьдлятоплива", "#бакдлятоплива", "#прозрачнаякрышка"],
                "keywords": {"primary": [], "long_tail": [], "scene": []},
                "seo_title_ru": "Стеклянная банка для продуктов",
                "short_title_ru": "Банка для продуктов",
                "description_ru": "Стеклянная банка для сладостей и орехов.",
                "selling_points": [],
            },
        }

        normalize_design_hashtags(design)

        tags = design["listing"]["hashtags"]
        self.assertIn("#банка", tags)
        self.assertFalse(any("топлив" in tag or "гсм" in tag for tag in tags))
        self.assertNotIn("#прозрачнаякрышка", tags)

    def test_draft_attributes_preserve_multiple_dictionary_values(self):
        attributes = _draft_attributes({
            "common_attributes": [{
                "attribute_id": 9163,
                "attribute_name": "Пол",
                "required": True,
                "value": "Мужской; Женский",
                "target_value": "Мужской; Женский",
                "dictionary_value_id": None,
                "dictionary_values": [
                    {"dictionary_value_id": 22880, "value": "Мужской"},
                    {"dictionary_value_id": 22881, "value": "Женский"},
                ],
                "source": "1688",
            }],
            "attributes_by_sku": {},
        })

        self.assertEqual(attributes[0]["values"], [
            {"value": "Мужской", "dictionary_value_id": 22880},
            {"value": "Женский", "dictionary_value_id": 22881},
        ])
        self.assertEqual(attributes[0]["status"], "confirmed")

    def test_materialize_no_longer_writes_ozon_tags(self):
        # 2026-08-14 双写合并：ozon-tags.json 由 field_completion 单一出口生成，
        # materialize 只投影文案与关键词。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product, skus = make_product(root, "P000935", 2)
            design = build_design(product, skus)
            materialize(product, design)
            self.assertFalse((product / "output/ozon-tags.json").exists())
            self.assertFalse((product / "output/ozon-draft.json").exists())
            self.assertFalse((product / "output/ozon-attributes-final.json").exists())

    def test_materialize_projects_search_keywords_with_underscores(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000944", 1)
            design = build_design(product, skus)
            source_ref = f"products/{product.name}/input/source.json"
            design["listing"]["keywords"] = {
                "primary": [keyword("сумка-органайзер для барбекю", source_ref)],
                "long_tail": [keyword("сумка для пикника и кемпинга", source_ref)],
                "scene": [keyword("хранение принадлежностей на даче", source_ref)],
                "excluded": [keyword("сумка-чехол с сертификатом", source_ref)],
            }

            materialize(product, design)

            copy_value = json.loads((product / "output/copy-ru.json").read_text())
            keyword_research = json.loads((product / "output/keyword-research-ru.json").read_text())
            projected = [
                *copy_value["keywords_ru"],
                *(item["keyword"] for item in keyword_research["approved_keywords"]),
                *(item["keyword"] for item in keyword_research["excluded_keywords"]),
            ]
            self.assertIn("сумка_органайзер_для_барбекю", projected)
            self.assertIn("сумка_для_пикника_и_кемпинга", projected)
            self.assertTrue(all(" " not in item and "-" not in item for item in projected))

    def test_materialize_no_longer_builds_draft_or_sku_attributes(self):
        # 2026-08-14 双写合并：draft / attributes-final 由 field_completion 单一出口
        # 编译；materialize 只投影文案与关键词。
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000948", 2)
            fill_path = product / "output/attribute-fill-input.json"
            fill_input = json.loads(fill_path.read_text(encoding="utf-8"))
            fill_input["ozon_attributes"] = [
                {
                    "attribute_id": 2001,
                    "attribute_name": "Материал",
                    "type": "String",
                    "required": True,
                    "is_aspect": False,
                    "allowed_values": [{"value": "Пластик", "dictionary_value_id": 11}],
                },
                {
                    "attribute_id": 10097,
                    "attribute_name": "Название цвета",
                    "type": "String",
                    "required": True,
                    "is_aspect": True,
                    "allowed_values": [],
                },
            ]
            fill_path.write_text(json.dumps(fill_input, ensure_ascii=False), encoding="utf-8")
            design = build_design(product, skus)
            design["attribute_decisions"] = {
                "input_hash": fill_input["input_hash"],
                "common_attributes": [{
                    "attribute_id": 2001,
                    "attribute_name": "Материал",
                    "scope": "common",
                    "decision_status": "filled",
                    "raw_semantic_value": "пластик",
                    "ozon_value": "Пластик",
                    "dictionary_value_id": 11,
                    "source_refs": [f"products/{product.name}/input/source.json"],
                }],
                "attributes_by_sku": {
                    "sku-1": [{
                        "attribute_id": 10097,
                        "attribute_name": "Название цвета",
                        "scope": "sku",
                        "decision_status": "filled",
                        "raw_semantic_value": "красный",
                        "ozon_value": "красный",
                        "dictionary_value_id": None,
                        "source_refs": [skus[0]["local_image_path"]],
                    }],
                    "sku-2": [{
                        "attribute_id": 10097,
                        "attribute_name": "Название цвета",
                        "scope": "sku",
                        "decision_status": "filled",
                        "raw_semantic_value": "черный",
                        "ozon_value": "черный",
                        "dictionary_value_id": None,
                        "source_refs": [skus[1]["local_image_path"]],
                    }],
                },
                "coverage_summary": {
                    "total_realtime_attributes": 2,
                    "decided_attributes": 3,
                    "unknown_high_risk_attribute_ids": [],
                    "skipped_optional_attribute_ids": [],
                },
            }

            materialize(product, design)

            self.assertFalse((product / "output/ozon-draft.json").exists())
            self.assertFalse((product / "output/ozon-attributes-final.json").exists())

    def test_weak_annotation_attribute_is_repaired_from_listing_seo(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000945", 1)
            fill_path = product / "output/attribute-fill-input.json"
            fill_input = json.loads(fill_path.read_text())
            fill_input["ozon_attributes"] = [{
                "attribute_id": 4191,
                "attribute_name": "Аннотация",
                "required": False,
                "type": "String",
            }]
            fill_path.write_text(json.dumps(fill_input, ensure_ascii=False), encoding="utf-8")
            design = build_design(product, skus)
            design["attribute_decisions"]["common_attributes"] = [{
                "attribute_id": 4191,
                "attribute_name": "Аннотация",
                "scope": "common",
                "decision_status": "filled",
                "ozon_value": "По текущей карточке выбранный вариант ZOL формат.",
                "raw_semantic_value": "По текущей карточке выбранный вариант ZOL формат.",
                "dictionary_value_id": None,
                "source_refs": [f"products/{product.name}/output/product-analysis.json"],
                "confidence": 0.4,
            }]

            materialize(product, design)

            # 设计修复仍然属于 materialize 的归一化职责（编译产物由 field_completion 生成）。
            saved_design = json.loads((product / "output/ozon-ecommerce-design.json").read_text())
            saved_decision = saved_design["attribute_decisions"]["common_attributes"][0]
            self.assertEqual(saved_decision["mapping_method"], "seo_annotation_projection")
            self.assertFalse((product / "output/ozon-attributes-final.json").exists())

    def test_materialize_repairs_internal_chinese_residue_before_russian_copy_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000948", 1)
            design = build_design(product, skus)
            contaminated = (
                "Товар помогает организовать рабочую зону и держать детали под рукой. "
                "Материал и сертификаты в текущей采集资料 не подтверждены, поэтому в карточке они не заявляются."
            )
            design["listing"]["description_ru"] = "\n\n".join([contaminated] * 4)
            design["listing"]["description_sections"]["notices"] = contaminated

            materialize(product, design)

            cjk = re.compile(r"[\u3400-\u9fff]")
            description = json.loads((product / "output/description-ru.json").read_text())["description_ru"]
            copy_value = json.loads((product / "output/copy-ru.json").read_text())
            self.assertIn("в текущих данных", description)
            self.assertFalse(cjk.search(description))
            self.assertFalse(cjk.search(copy_value["description_ru"]))

    def test_repair_existing_buyer_copy_projection_ignores_stale_attribute_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000949", 1)
            design = build_design(product, skus)
            contaminated = "Материал и сертификаты в текущей采集资料 не подтверждены."
            design["attribute_decisions"]["input_hash"] = "stale-input-hash"
            design["listing"]["description_ru"] = contaminated
            (product / "output/ozon-ecommerce-design.json").write_text(
                json.dumps(design, ensure_ascii=False),
                encoding="utf-8",
            )
            (product / "output/title-ru.json").write_text(
                json.dumps({"title_ru": design["listing"]["seo_title_ru"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (product / "output/description-ru.json").write_text(
                json.dumps({"description_ru": contaminated}, ensure_ascii=False),
                encoding="utf-8",
            )
            (product / "output/copy-ru.json").write_text(
                json.dumps({"description_ru": contaminated}, ensure_ascii=False),
                encoding="utf-8",
            )
            (product / "output/ozon-draft.json").write_text(
                json.dumps({
                    "description": contaminated,
                    "images": [{"slot": "main-sku-1"}],
                    "skus": [{"source_sku_id": skus[0]["sku_id"]}],
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            changed = repair_existing_buyer_copy_projection(product)

            cjk = re.compile(r"[\u3400-\u9fff]")
            description = json.loads((product / "output/description-ru.json").read_text())["description_ru"]
            copy_value = json.loads((product / "output/copy-ru.json").read_text())
            draft = json.loads((product / "output/ozon-draft.json").read_text())
            saved_design = json.loads((product / "output/ozon-ecommerce-design.json").read_text())
            self.assertTrue(changed)
            self.assertIn("в текущих данных", description)
            self.assertFalse(cjk.search(description))
            self.assertFalse(cjk.search(copy_value["description_ru"]))
            self.assertFalse(cjk.search(draft["description"]))
            self.assertEqual(draft["images"], [{"slot": "main-sku-1"}])
            self.assertEqual(len(draft["skus"]), 1)
            self.assertEqual(saved_design["attribute_decisions"]["input_hash"], "stale-input-hash")

    def test_repair_existing_buyer_copy_projection_allows_chinese_in_evidence_refs(self):
        with tempfile.TemporaryDirectory() as directory:
            product, _skus = make_product(Path(directory), "P000950", 1)
            (product / "output/copy-ru.json").write_text(
                json.dumps({
                    "title_ru": "Стеклянная банка для ферментации",
                    "short_title": "Банка для ферментации",
                    "description_ru": "Стеклянная банка подходит для домашних заготовок.",
                    "selling_points": [
                        {
                            "text_ru": "Стеклянный корпус помогает видеть содержимое.",
                            "evidence": ["products/P000950/input/source.json#product_attributes[材质]"],
                        }
                    ],
                    "bullets_ru": [
                        {
                            "text_ru": "Крышка помогает закрыть банку.",
                            "evidence": ["products/P000950/input/source.json#product_attributes[形状]"],
                        }
                    ],
                    "keywords_ru": ["банка_для_ферментации"],
                    "hashtags_ru": ["#банка"],
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            changed = repair_existing_buyer_copy_projection(product)

            self.assertFalse(changed)

    def test_single_sku_materialize_keeps_copy_schema_without_fake_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000946", 1)
            design = build_design(product, skus)

            materialize(product, design)

            copy_value = json.loads((product / "output/copy-ru.json").read_text())
            self.assertTrue(copy_value["image_copy_ru"]["comparison"])

    def test_tool_organizer_tags_do_not_expand_to_kitchen_storage_terms(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000947", 1)
            source_ref = f"products/{product.name}/input/source.json"
            design = build_design(product, skus)
            design["product_understanding"]["product_type_ru"] = "магнитный коврик органайзер для инструмента"
            design["listing"]["seo_title_ru"] = "Магнитный коврик для инструмента, набор 3 шт"
            design["listing"]["short_title_ru"] = "Магнитный коврик для инструмента"
            design["listing"]["keywords"] = {
                "primary": [keyword("магнитный коврик для инструмента", source_ref)],
                "long_tail": [keyword("органайзер для крепежа в гараже", source_ref)],
                "scene": [keyword("для верстака", source_ref)],
                "excluded": [keyword("органайзер для кухни", source_ref)],
            }

            materialize(product, design)

            # 2026-08-14 双写合并：标签净化（含禁词过滤）由 field_completion 单一出口负责。
            self.assertFalse((product / "output/ozon-tags.json").exists())

    def test_dynamic_n_mains_plus_exactly_eight_shared_details(self):
        for offset, sku_count in enumerate((1, 3, 4, 10), start=1):
            with self.subTest(sku_count=sku_count), tempfile.TemporaryDirectory() as directory:
                product, skus = make_product(Path(directory), f"P00093{offset}", sku_count)
                design = build_design(product, skus)
                self.assertEqual(validate_design(product, design), [])
                self.assertEqual(len(design["main_images"]), sku_count)
                self.assertEqual(len(design["detail_images"]), 8)
                self.assertEqual(len(design["main_images"]) + len(design["detail_images"]), sku_count + 8)
                for sku, main in zip(skus, design["main_images"]):
                    self.assertEqual(main["source_references"], [sku["local_image_path"]])
                self.assertTrue(all("sku_id" not in item for item in design["detail_images"]))

    def test_missing_sku_image_can_use_user_bound_current_product_main_image(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000941", 1)
            main_path = product / "input/main-images/main-001.png"
            main_path.write_bytes(b"current-product-main-image")
            source_path = product / "input/source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["main_images"] = [{
                "local_path": "products/P000941/input/main-images/main-001.png",
                "source_url": "https://cbu01.alicdn.com/main-001.png",
            }]
            source["skus"][0].update({
                "local_image_path": "unknown",
                "variant_local_image_path": "unknown",
                "sku_image_missing": True,
            })
            source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            write_source_manifest(product)
            binding = save_sku_image_binding(
                product,
                "sku-1",
                "products/P000941/input/main-images/main-001.png",
                bound_by="tester",
            )
            self.assertEqual(binding["binding_kind"], "user_bound_reference_image")
            self.assertEqual(binding["source_type"], "main_gallery_reference")

            design_sku = dict(skus[0])
            design_sku["local_image_path"] = "products/P000941/input/main-images/main-001.png"
            design = build_design(product, [design_sku])
            self.assertEqual(validate_design(product, design), [])

    def test_single_spec_missing_sku_image_uses_first_current_product_main_image(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000942", 1)
            main_path = product / "input/main-images/main-001.png"
            main_path.write_bytes(b"current-product-main-image")
            source_path = product / "input/source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["main_images"] = [{"local_path": "products/P000942/input/main-images/main-001.png"}]
            source["skus"][0].update({
                "sku_identity_type": "single_specification",
                "sku_name": "单规格",
                "option_values": [],
                "local_image_path": "unknown",
                "variant_local_image_path": "unknown",
                "sku_image_missing": True,
            })
            source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            write_source_manifest(product)
            design_sku = dict(skus[0])
            design_sku.update({
                "sku_identity_type": "single_specification",
                "sku_name": "单规格",
                "option_values": [],
                "local_image_path": "products/P000942/input/main-images/main-001.png",
                "sku_image_missing": True,
            })
            design = build_design(product, [design_sku])
            errors = validate_design(product, design)
            self.assertEqual(errors, [])
            self.assertEqual(
                design["main_images"][0]["source_references"][0],
                "products/P000942/input/main-images/main-001.png",
            )

    def test_multi_sku_missing_sku_image_without_binding_stops_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000940", 2)
            main_path = product / "input/main-images/main-001.png"
            main_path.write_bytes(b"current-product-main-image")
            source_path = product / "input/source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["main_images"] = [{"local_path": "products/P000940/input/main-images/main-001.png"}]
            for sku in source["skus"]:
                sku.update({
                    "local_image_path": "unknown",
                    "variant_local_image_path": "unknown",
                    "sku_image_missing": True,
                })
            source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            write_source_manifest(product)
            design_skus = []
            for sku in skus:
                item = dict(sku)
                item["local_image_path"] = "products/P000940/input/main-images/main-001.png"
                item["sku_image_missing"] = True
                design_skus.append(item)
            errors = validate_design(product, build_design(product, design_skus))
            self.assertTrue(any("缺少参考图" in item for item in errors), errors)

    def test_sku_image_binding_rejects_output_images(self):
        with tempfile.TemporaryDirectory() as directory:
            product, _ = make_product(Path(directory), "P000943", 1)
            output_path = product / "output/generated-images/detail/detail-001.png"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"generated-output")
            with self.assertRaises(ValueError):
                save_sku_image_binding(
                    product,
                    "sku-1",
                    "products/P000943/output/generated-images/detail/detail-001.png",
                    bound_by="tester",
                )

    def test_output_image_reference_hard_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000939", 3)
            generated = product / "output/generated-images/detail/old.png"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(b"old-output")
            design = build_design(product, skus)
            design["detail_images"][0]["source_references"] = [str(generated)]
            errors = validate_design(product, design)
            self.assertTrue(any("product reference" in item or "output" in item for item in errors), errors)

    def test_deterministic_detail_operation_is_normalized_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000944", 1)
            design = build_design(product, skus)
            detail = next(item for item in design["detail_images"] if item["layout_type"] == "purchase_notice")
            detail["operation"] = "edit_real_image"
            errors = validate_design(product, design)
            self.assertFalse(any("reference-guided composition" in item for item in errors), errors)
            self.assertEqual(detail["operation"], "generate_from_reference")

    def test_missing_art_direction_and_broken_step_order_are_repaired_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000938", 2)
            design = build_design(product, skus)
            design["main_images"][0].pop("art_direction")
            design["decision_trace"]["steps"][0], design["decision_trace"]["steps"][1] = (
                design["decision_trace"]["steps"][1], design["decision_trace"]["steps"][0]
            )
            design["decision_trace"]["compliance_status"] = "FAIL"
            design["decision_trace"]["violations"] = ["main visual typography needs director revision"]
            errors = validate_design(product, design)
            self.assertFalse(any("art_direction" in item for item in errors), errors)
            self.assertIn("art_direction", design["main_images"][0])
            self.assertFalse(any("decision trace" in item for item in errors), errors)
            self.assertEqual(
                [item["name"] for item in design["decision_trace"]["steps"]],
                ["product_evidence", "buyer_analysis", "selling_point_ranking", "image_sequence", "per_slot_art_direction", "prompt_completion", "pre_generation_validation"],
            )
            warnings = design["processing"].get("validation_warnings") or []
            self.assertTrue(any("decision_trace" in item for item in warnings), warnings)

    def test_legacy_list_decision_trace_is_normalized_before_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000936", 2)
            design = build_design(product, skus)
            design["decision_trace"] = [
                {"stage": item["name"], "status": item["status"], "evidence": item["evidence"]}
                for item in design["decision_trace"]["steps"]
            ]
            errors = validate_design(product, design)
            self.assertFalse(any("decision trace" in item for item in errors), errors)
            self.assertEqual(
                [item["name"] for item in design["decision_trace"]["steps"]],
                ["product_evidence", "buyer_analysis", "selling_point_ranking", "image_sequence", "per_slot_art_direction", "prompt_completion", "pre_generation_validation"],
            )

    def test_text_free_or_missing_russian_prompt_is_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000937", 2)
            design = build_design(product, skus)
            design["main_images"][0]["prompt"] = "Create a faithful text-free product scene. Generate no text."
            errors = validate_design(product, design)
            self.assertFalse(any("forbidden text-free" in item for item in errors), errors)
            self.assertFalse(any("every exact Russian text" in item for item in errors), errors)
            prompt = design["main_images"][0]["prompt"].casefold()
            self.assertNotIn("text-free", prompt)
            self.assertIn("сохрани форму", prompt)
            self.assertIn("не плакат", prompt)
            self.assertIn("привязан к доказательству товара", prompt)
            for text in design["main_images"][0]["russian_text"]:
                self.assertIn(text, design["main_images"][0]["prompt"])

    def test_missing_overlay_defaults_to_high_contrast_and_product_dominance(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000932", 1)
            design = build_design(product, skus)
            main = design["main_images"][0]
            main["overlay_plan"] = []
            main["art_direction"] = {}
            self.assertEqual(validate_design(product, design), [])
            repaired = design["main_images"][0]
            self.assertEqual(repaired["art_direction"]["product_scale_percent"], 72)
            self.assertIn("крупнейшую визуальную площадь", repaired["art_direction"]["composition"])
            self.assertIn("текст живет в естественном свободном месте", repaired["art_direction"]["background"])
            self.assertTrue(all(item["background_style"] == "translucent" for item in repaired["overlay_plan"]))
            self.assertTrue(all(item["background_color"] == "#111827" for item in repaired["overlay_plan"]))
            self.assertTrue(all(item["text_color"] == "#F8FAFC" for item in repaired["overlay_plan"]))

    def test_oversized_dark_overlay_is_normalized_to_restrained_chips(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000937", 1)
            design = build_design(product, skus)
            main = design["main_images"][0]
            original_text = list(main["russian_text"])
            main["overlay_plan"][0]["background_style"] = "solid"
            main["overlay_plan"][0]["background_color"] = "#111827"
            main["overlay_plan"][0]["text_color"] = "#FFFFFF"
            main["overlay_plan"][0]["font_size_ratio"] = 0.078
            main["overlay_plan"][0]["box"] = [0.06, 0.05, 0.86, 0.12]

            errors = validate_design(product, design)

            self.assertEqual(errors, [])
            repaired = design["main_images"][0]["overlay_plan"][0]
            self.assertEqual(main["russian_text"], original_text)
            self.assertLessEqual(repaired["box"][2], 0.5)
            self.assertLessEqual(repaired["box"][3], 0.07)
            self.assertEqual(repaired["background_style"], "none")
            self.assertEqual(repaired["background_color"], "#F8FAFC")
            self.assertEqual(repaired["text_color"], "#111827")

    def test_pill_overlay_is_removed_before_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000938", 1)
            design = build_design(product, skus)
            main = design["main_images"][0]
            main["overlay_plan"][0]["background_style"] = "pill"
            main["overlay_plan"][0]["background_color"] = "#2563EB"
            main["overlay_plan"][0]["text_color"] = "#FFFFFF"

            errors = validate_design(product, design)

            self.assertEqual(errors, [])
            repaired = design["main_images"][0]["overlay_plan"][0]
            self.assertEqual(repaired["background_style"], "none")

    def test_legacy_poster_template_modules_are_removed_before_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000931", 1)
            design = build_design(product, skus)
            main = design["main_images"][0]
            main["overlay_modules"] = ["product_name", "capacity_badge", "benefit_section", "icon_chips"]
            main["prompt"] = (
                "Create a marketing poster with a huge headline, capacity badge and three-card benefit row. "
                "Render these exact lines once, correctly and legibly: "
                + "; ".join(main["russian_text"])
            )
            main["overlay_plan"][0]["role"] = "headline"
            main["overlay_plan"][1]["role"] = "sku_badge"

            errors = validate_design(product, design)

            self.assertEqual(errors, [])
            self.assertFalse({"capacity_badge", "benefit_section", "icon_chips"} & set(main["overlay_modules"]))
            self.assertNotIn("headline", [item["role"] for item in main["overlay_plan"]])
            self.assertNotIn("sku_badge", [item["role"] for item in main["overlay_plan"]])
            self.assertNotIn("huge headline", main["prompt"].casefold())
            self.assertNotIn("capacity badge", main["prompt"].casefold())
            self.assertNotIn("three-card benefit row", main["prompt"].casefold())

    def test_repeated_detail_overlay_layouts_are_diversified_at_source(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000958", 3)
            design = build_design(product, skus)
            repeated_boxes = [[0.055, 0.055, 0.46, 0.058], [0.055, 0.132, 0.42, 0.05]]
            for item in design["detail_images"]:
                for index, instruction in enumerate(item["overlay_plan"]):
                    instruction["box"] = list(repeated_boxes[min(index, 1)])
                item["art_direction"]["composition"] = "same front product plus side text composition"
                if item["layout_type"] == "structure_callout":
                    item["must_preserve"] = [
                        "gold metal frame", "colored beads", "transparent rhinestones",
                    ]
                    item["russian_text"] = ["Цветные бусины", "Стразы по контуру", "Металлическая рамка"]

            materialize(product, design)

            saved = json.loads((product / "output/ozon-ecommerce-design.json").read_text())
            signatures = {
                tuple(tuple(round(float(part), 3) for part in instruction["box"]) for instruction in item["overlay_plan"][:2])
                for item in saved["detail_images"]
            }
            prompts = [item["prompt"] for item in saved["detail_images"]]
            self.assertLessEqual(len(signatures), 2)
            self.assertTrue(all("Set-level diversity execution for this slot:" in prompt for prompt in prompts))
            self.assertTrue(all("choose a distinct scene, camera distance, angle, crop and Russian text placement" in prompt for prompt in prompts))
            self.assertTrue(all("do not impose fixed left/right positions, coordinates, background or palette" in prompt for prompt in prompts))
            self.assertFalse(any("camera family" in prompt for prompt in prompts))
            self.assertEqual(len(saved["main_images"]), 3)
            self.assertEqual(len(saved["detail_images"]), 8)

    def test_visual_world_and_reference_editing_rule_are_materialized(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000959", 2)
            design = build_design(product, skus)
            for key in (
                "photography_world", "lens_plan", "reference_editing_rule",
                "material_value_signal", "scene_variety_rule",
            ):
                design["visual_system"].pop(key, None)
            for item in design["detail_images"]:
                item["art_direction"]["scene"] = "clean kitchen counter with product and side text"
                item["art_direction"]["composition"] = "same front product plus side text composition"
                item["prompt"] += " Reuse the supplier reference photo as the final canvas and add Russian text."

            materialize(product, design)

            saved = json.loads((product / "output/ozon-ecommerce-design.json").read_text())
            visual_system = saved["visual_system"]
            for key in (
                "photography_world", "lens_plan", "reference_editing_rule",
                "material_value_signal", "scene_variety_rule",
            ):
                self.assertGreaterEqual(len(visual_system[key]), 30)
            prompts = [item["prompt"] for item in saved["main_images"] + saved["detail_images"]]
            self.assertTrue(all("Product-specific photographic world:" in prompt for prompt in prompts))
            self.assertTrue(all("Reference image is an identity anchor only:" in prompt for prompt in prompts))
            self.assertTrue(any("do not paste the supplier/reference image as the canvas" in prompt for prompt in prompts))
            self.assertTrue(any("Do not reuse a default clean kitchen counter" in json.dumps(item["art_direction"], ensure_ascii=False) for item in saved["detail_images"]))
            schema = json.loads(
                (Path(__file__).resolve().parents[1] / "templates/ozon-ecommerce-design.schema.json").read_text()
            )
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(saved)), [])

    def test_sku_main_poster_like_wording_is_not_a_hard_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000936", 1)
            design = build_design(product, skus)
            main = design["main_images"][0]
            main["prompt"] = (
                "Создай рекламный плакат для маркетплейса с крупным заголовком. "
                "Render these exact lines once, correctly and legibly: "
                + "; ".join(main["russian_text"])
            )
            main["overlay_plan"][0]["font_size_ratio"] = 0.078
            main["overlay_plan"][0]["box"] = [0.06, 0.05, 0.86, 0.12]
            errors = validate_design(product, design)
            self.assertFalse([item for item in errors if "poster" in item or "плакат" in item or "headline" in item], errors)

    def test_sku_main_accepts_product_card_infographic_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000934", 1)
            design = build_design(product, skus)
            errors = validate_design(product, design)
            self.assertFalse([item for item in errors if "sku_main" in item], errors)


if __name__ == "__main__":
    unittest.main()
