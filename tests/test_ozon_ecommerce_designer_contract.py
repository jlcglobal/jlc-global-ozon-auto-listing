import json
import tempfile
import unittest
from pathlib import Path

from scripts.ozon_ecommerce_designer_contract import validate_design
from scripts.production_input_guard import write_source_manifest


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
    (product / "status.json").write_text(json.dumps({"status": "CONTENT_GENERATED"}), encoding="utf-8")
    write_source_manifest(product)
    return product, skus


def keyword(text: str, source_ref: str) -> dict:
    return {"text_ru": text, "intent": "commercial", "source_refs": [source_ref], "metrics": "unknown"}


def image_role(slot: str, layout: str, refs: list[str], operation: str, sku_id: str | None = None) -> dict:
    russian_text = ["ПОНЯТНАЯ ПОЛЬЗА", "ТОЧНЫЕ ХАРАКТЕРИСТИКИ"]
    value = {
        "slot": slot,
        "layout_type": layout,
        "commercial_purpose": "Помочь покупателю принять решение",
        "buyer_question": "Почему этот товар подходит покупателю?",
        "source_references": refs,
        "russian_text": russian_text,
        "prompt": (
            "Create a source-grounded 3:4 Ozon ecommerce visual with the real product dominant, "
            "a distinct buyer-decision purpose, natural scene, faithful proportions, exact SKU identity, "
            "and render the complete final Russian typography in this same image-model call. "
            "Render these exact lines once, correctly and legibly: ПОНЯТНАЯ ПОЛЬЗА; ТОЧНЫЕ ХАРАКТЕРИСТИКИ. "
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
            "role": "headline" if index == 0 else "benefit",
            "text": text,
            "box": [0.05, 0.06 + index * 0.16, 0.62, 0.12],
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
        value["overlay_modules"] = ["product_name", "capacity_badge", "benefit_section"]
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
            "hashtags": [f"#тестовыйтег{index}" for index in range(1, 31)],
        },
        "attribute_plan": [],
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

    def test_missing_art_direction_or_broken_step_order_requires_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000938", 2)
            design = build_design(product, skus)
            design["main_images"][0].pop("art_direction")
            design["decision_trace"]["steps"][0], design["decision_trace"]["steps"][1] = (
                design["decision_trace"]["steps"][1], design["decision_trace"]["steps"][0]
            )
            errors = validate_design(product, design)
            self.assertTrue(any("art_direction" in item for item in errors), errors)
            self.assertTrue(any("required order" in item for item in errors), errors)

    def test_text_free_or_missing_russian_prompt_requires_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            product, skus = make_product(Path(directory), "P000937", 2)
            design = build_design(product, skus)
            design["main_images"][0]["prompt"] = "Create a faithful text-free product scene. Generate no text."
            errors = validate_design(product, design)
            self.assertTrue(any("forbidden text-free" in item for item in errors), errors)
            self.assertTrue(any("every exact Russian text" in item for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
