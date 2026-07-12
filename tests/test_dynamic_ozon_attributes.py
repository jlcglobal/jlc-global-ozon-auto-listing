import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_batch import upload_feasibility  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def product_fixture(root: Path, name: str, category_id: int, type_id: int, required_ids: list[int]) -> Path:
    product = root / name
    output = product / "output"
    write_json(product / "input/source.json", {
        "skus": [{"sku_id": "real-1", "purchase_price": 10, "local_image_path": "unknown"}],
        "main_images": [{"local_path": f"products/{name}/input/main-images/main.jpg"}],
    })
    write_json(output / "ozon-category.json", {
        "category_id": category_id, "type_id": type_id, "category_name": name,
        "metadata_source": "ozon_seller_api", "match_status": "api_confirmed",
        "confidence": 0.95,
    })
    write_json(output / "ozon-category-tree.json", {
        "categories": [{
            "category_id": category_id, "type_id": type_id,
            "category_name": name, "disabled": False,
        }],
    })
    metadata = [{
        "attribute_id": attribute_id,
        "attribute_name": f"required-{attribute_id}",
        "required": True,
    } for attribute_id in required_ids]
    write_json(output / "ozon-category-attributes.json", {
        "category_id": category_id, "type_id": type_id, "attributes": metadata,
    })
    write_json(output / "ozon-attributes.json", {
        "category_id": category_id,
        "type_id": type_id,
        "attributes": [{
            **item, "value": f"value-{item['attribute_id']}", "validation_status": "valid",
        } for item in metadata],
        "invalid_values": [],
    })
    write_json(output / "pricing-result.json", {
        "sku_pricing": [{"sku_id": "real-1", "selling_price_cny": 30}],
    })
    write_json(output / "cost-analysis.json", {
        "product_weight": {"value": 100},
        "package_weight": {"value": 130},
        "product_dimensions": {"length": 10, "width": 8, "height": 2},
        "package_dimensions": {"length": 11, "width": 9, "height": 3},
    })
    write_json(output / "offer-id-precheck.json", {"status": "ok"})
    return product


class DynamicOzonAttributeGateTest(unittest.TestCase):
    def test_three_categories_use_their_own_required_attribute_sets(self):
        cases = (
            ("P100001", 71001, 81001, [31001, 31002]),  # home
            ("P100002", 72001, 82001, [42001, 42002, 42003]),  # kitchen
            ("P100003", 73001, 83001, [53001]),  # electronics
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seen = []
            for product_id, category_id, type_id, required_ids in cases:
                product = product_fixture(root, product_id, category_id, type_id, required_ids)
                upload_feasibility(product)
                result = json.loads((product / "output/upload-feasibility.json").read_text())
                self.assertEqual(result["status"], "PASS")
                self.assertEqual(result["required_attribute_ids"], required_ids)
                self.assertEqual(result["missing_required_attribute_ids"], [])
                seen.append(set(result["required_attribute_ids"]))
            self.assertTrue(all(left.isdisjoint(right) for index, left in enumerate(seen) for right in seen[index + 1:]))

    def test_missing_required_live_attribute_blocks_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            product = product_fixture(Path(directory), "P100004", 74001, 84001, [64001, 64002])
            mapped_path = product / "output/ozon-attributes.json"
            mapped = json.loads(mapped_path.read_text())
            mapped["attributes"][1]["value"] = "unknown"
            mapped["attributes"][1]["validation_status"] = "unknown"
            write_json(mapped_path, mapped)
            with self.assertRaisesRegex(RuntimeError, "Upload feasibility failed"):
                upload_feasibility(product)
            result = json.loads((product / "output/upload-feasibility.json").read_text())
            self.assertEqual(result["missing_required_attribute_ids"], [64002])

    def test_category_type_mismatch_blocks_cross_product_attribute_pollution(self):
        with tempfile.TemporaryDirectory() as directory:
            product = product_fixture(Path(directory), "P100005", 75001, 85001, [65001])
            mapped_path = product / "output/ozon-attributes.json"
            mapped = json.loads(mapped_path.read_text())
            mapped["category_id"] = 99999
            write_json(mapped_path, mapped)
            with self.assertRaisesRegex(RuntimeError, "Upload feasibility failed"):
                upload_feasibility(product)
            result = json.loads((product / "output/upload-feasibility.json").read_text())
            self.assertFalse(result["checks"]["attribute_schema_identity"])

    def test_low_confidence_category_stops_before_content_and_images(self):
        with tempfile.TemporaryDirectory() as directory:
            product = product_fixture(Path(directory), "P100006", 76001, 86001, [66001])
            category_path = product / "output/ozon-category.json"
            category = json.loads(category_path.read_text())
            category.update({"match_status": "api_match_needs_review", "confidence": 0.82})
            write_json(category_path, category)
            with self.assertRaisesRegex(RuntimeError, "Upload feasibility failed"):
                upload_feasibility(product)
            result = json.loads((product / "output/upload-feasibility.json").read_text())
            self.assertFalse(result["checks"]["category"])

    def test_category_type_pair_must_exist_in_same_tree_leaf(self):
        with tempfile.TemporaryDirectory() as directory:
            product = product_fixture(Path(directory), "P100007", 77001, 87001, [67001])
            write_json(product / "output/ozon-category-tree.json", {
                "categories": [{
                    "category_id": 77001, "type_id": 99999,
                    "category_name": "wrong type", "disabled": False,
                }],
            })
            with self.assertRaisesRegex(RuntimeError, "Upload feasibility failed"):
                upload_feasibility(product)
            result = json.loads((product / "output/upload-feasibility.json").read_text())
            self.assertFalse(result["checks"]["category_type_pair"])


if __name__ == "__main__":
    unittest.main()
