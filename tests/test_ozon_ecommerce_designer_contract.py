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
    value = {
        "slot": slot,
        "layout_type": layout,
        "commercial_purpose": "Помочь покупателю принять решение",
        "buyer_question": "Почему этот товар подходит покупателю?",
        "source_references": refs,
        "russian_text": ["ПОНЯТНАЯ ПОЛЬЗА", "ТОЧНЫЕ ХАРАКТЕРИСТИКИ"],
        "prompt": (
            "Create a source-grounded 3:4 Ozon ecommerce visual with the real product dominant, "
            "a distinct buyer-decision purpose, natural scene, faithful proportions, exact SKU identity, "
            "and reserved space for deterministic Russian text, badges, icons and callout modules. "
            "Never redraw the product or invent accessories."
        ),
        "operation": operation,
        "overlay_modules": ["product_name", "benefit_section", "icon_chips"],
        "must_preserve": ["shape", "color", "SKU differences"],
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
        "visual_system": {"style": "product-specific"},
        "main_images": [image_role(
            f"main-{item['sku_id']}", "sku_main", [item["local_image_path"]],
            "edit_real_image", item["sku_id"],
        ) for item in skus],
        "detail_images": details,
        "forbidden": ["invented facts", "cross-product references", "generated output as input"],
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


if __name__ == "__main__":
    unittest.main()
