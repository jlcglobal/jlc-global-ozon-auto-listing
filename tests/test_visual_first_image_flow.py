import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.image_qc import assessment_from_hard_gate
from scripts.image_slot_scheduler import pending_slots
from scripts.marketplace_content_generator import ozon_draft_variant_kind
from scripts.style_selector import ROOT

sys.path.insert(0, str(ROOT / "ozon-uploader"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "ozon-field-completion"))
from ozon_field_completion import build_package  # noqa: E402
from ozon_uploader.service import build_import_items  # noqa: E402


@unittest.skipUnless((ROOT / "products/P000011/input/source.json").is_file(), "optional runtime product fixture is not installed")
class VisualFirstImageFlowTest(unittest.TestCase):
    def setUp(self):
        self.product = ROOT / "products/P000011"
        self.profile = json.loads((self.product / "output/style-profile.json").read_text(encoding="utf-8"))
        self.plan = json.loads((self.product / "output/image-plan.json").read_text(encoding="utf-8"))

    def test_product_specific_plan_has_one_main_per_sku_and_dynamic_details(self):
        self.assertEqual(len(self.plan["main_images"]), 2)
        self.assertEqual(len(self.plan["detail_images"]), 8)
        self.assertEqual(len(self.plan["disclaimer_images"]), 0)
        self.assertEqual(
            {item["source_sku_id"] for item in self.plan["main_images"]},
            {"5872460434730", "5872460434733"},
        )
        detail_types = [item["image_type"] for item in self.plan["detail_images"]]
        self.assertEqual(len(detail_types), len(set(detail_types)))
        self.assertTrue(all("plain white" in item["prompt"] for item in self.plan["detail_images"]))
        self.assertEqual(self.plan["generator_contract"]["quality_gate"], "hard_failures_only")
        self.assertTrue(self.plan["generator_contract"]["main_images_first"])
        self.assertEqual(self.plan["generator_contract"]["target_total_seconds"], 300)

    def test_creative_direction_is_for_this_product_not_a_category_template(self):
        creative = self.profile["creative_direction"]
        self.assertIn("食品储藏罐", creative["product_visual_thesis"])
        self.assertIn("不得套用", creative["product_visual_thesis"])
        self.assertIn("固定黑色文字框", creative["typography"])
        self.assertIn("同属一个类目", creative["anti_template_rule"])

    def test_scheduler_releases_all_sku_mains_before_details(self):
        schedule = pending_slots(self.product, 3)
        self.assertEqual([item["image_type"] for item in schedule["waves"][0]], ["main", "main"])
        self.assertTrue(all(item["image_type"] != "main" for wave in schedule["waves"][1:] for item in wave))

    def test_current_profile_and_plan_match_schemas(self):
        profile_schema = json.loads((ROOT / "templates/style-profile.schema.json").read_text(encoding="utf-8"))
        plan_schema = json.loads((ROOT / "templates/image-plan.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(profile_schema).iter_errors(self.profile)), [])
        self.assertEqual(list(Draft202012Validator(plan_schema).iter_errors(self.plan)), [])

    def test_hard_gate_only_requires_every_slot_and_six_blocking_error_types(self):
        slots = [
            item["slot"]
            for key in ("main_images", "detail_images", "disclaimer_images")
            for item in self.plan[key]
        ]
        assessment = assessment_from_hard_gate(self.product, {
            "mode": "hard_failures_only",
            "checked_slots": slots,
            "critical_failures": [],
            "issues": [],
        })
        self.assertEqual(set(assessment["image_slots"]), set(slots))
        self.assertEqual(assessment["critical_failures"], [])

    def test_seller_only_image_variant_is_not_sent_as_ozon_aspect_kind(self):
        self.assertEqual(ozon_draft_variant_kind("seller_specification"), "not_applicable")
        self.assertEqual(ozon_draft_variant_kind("color"), "color")

    def test_final_attribute_schema_preserves_human_override_provenance(self):
        schema = json.loads((ROOT / "templates/ozon-attributes-final.schema.json").read_text(encoding="utf-8"))
        sources = schema["$defs"]["attribute"]["properties"]["source"]["enum"]
        self.assertIn("human_override", sources)

    def test_current_product_upload_items_never_include_inventory_fields(self):
        output = self.product / "output"
        draft = json.loads((output / "ozon-draft.json").read_text(encoding="utf-8"))
        config = json.loads((output / "ozon-upload-config.json").read_text(encoding="utf-8"))
        attributes = json.loads((output / "ozon-attributes-final.json").read_text(encoding="utf-8"))
        grouping = json.loads((output / "variant-grouping-result.json").read_text(encoding="utf-8"))
        items = build_import_items(
            draft,
            config,
            ["https://images.example.test/shared.png"],
            final_attributes=attributes,
            variant_grouping=grouping,
            variant_main_image_urls={
                str(sku["source_sku_id"]): f"https://images.example.test/{sku['source_sku_id']}.png"
                for sku in draft["skus"]
            },
        )

        def forbidden_keys(value):
            if isinstance(value, dict):
                return {
                    key
                    for key, nested in value.items()
                    if key in {"stock", "warehouse_id"}
                } | set().union(*(forbidden_keys(nested) for nested in value.values()))
            if isinstance(value, list):
                return set().union(*(forbidden_keys(item) for item in value))
            return set()

        self.assertEqual(forbidden_keys({"items": items}), set())
        self.assertEqual(config["stock_mode"], "not_set")

    def test_safe_optional_attributes_are_filled_without_inventing_risky_facts(self):
        package = build_package(self.product, write=False)
        by_id = {
            item["attribute_id"]: item
            for item in package["ozon-attributes-final.json"]["attributes"]
        }
        self.assertEqual(by_id[23249]["value"], 1)
        self.assertEqual(by_id[8414]["value"], 15.0)
        self.assertEqual(by_id[6829]["value"], "Крышка в комплекте")
        self.assertEqual(by_id[9024]["value"], "P000011")
        self.assertEqual(by_id[6814]["value"], "Прямоугольник")
        self.assertEqual(by_id[23372]["value"], "Банка для сыпучих продуктов")
        self.assertEqual(by_id[8513]["value"], 1)
        # A risky fact may only become non-unknown after an explicit workbench
        # override.  The current real product has such a material override;
        # field completion must preserve it without treating it as an AI fact.
        material = by_id[6383]
        if material["value"] != "unknown":
            self.assertEqual(material["source"], "human_override")
            self.assertIn("workbench-draft.attributes", material["evidence"])
        for attribute_id in (12829, 6788, 22232):
            self.assertEqual(by_id[attribute_id]["value"], "unknown")


if __name__ == "__main__":
    unittest.main()
