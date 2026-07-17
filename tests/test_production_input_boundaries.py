import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.image_asset_boundaries import validate_product_reference
from scripts.image_asset_boundaries import write_asset_contract
from scripts.production_input_guard import (
    ProductionInputError,
    source_snapshot_binding,
    validate_formal_product_input,
    write_source_manifest,
)
from scripts.pipeline_runtime import create_batch


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc000000301010018dd8db40000000049454e44ae426082"
)


def make_formal_product(root: Path, product_id: str, sku_count: int = 1) -> Path:
    product = root / "products" / product_id
    for relative in ("input/sku-images", "input/main-images", "input/detail-images", "output"):
        (product / relative).mkdir(parents=True, exist_ok=True)
    collection_id = f"COL-{product_id}-BOUNDARY"
    skus = []
    for index in range(1, sku_count + 1):
        path = product / "input/sku-images" / f"same-name-{index}.png"
        path.write_bytes(PNG + bytes([index]))
        relative = f"products/{product_id}/input/sku-images/{path.name}"
        skus.append({
            "sku_id": f"sku-{index}",
            "sku_name": f"SKU {index}",
            "selection_order": index,
            "local_image_path": relative,
            "source_data": {"image_sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        })
    source = {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "collection_id": collection_id,
        "source_kind": "workbench_collection",
        "source_path": f"products/{product_id}/input/source.json",
        "source_url": f"https://detail.1688.com/offer/{product_id}.html",
        "title_cn": "边界测试商品",
        "collected_at": "2026-07-16T12:00:00+08:00",
        "captured_at": "2026-07-16T12:00:00+08:00",
        "raw_capture_file": f"products/{product_id}/input/raw-snapshot.json",
        "skus": skus,
        "main_images": [],
        "detail_images": [],
    }
    (product / "input/source.json").write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    (product / "input/raw-snapshot.json").write_text(json.dumps({"product_id": product_id}), encoding="utf-8")
    (product / "input/category-selection.json").write_text(json.dumps({
        "product_id": product_id, "category_id": 1, "type_id": 2,
    }), encoding="utf-8")
    (product / "status.json").write_text(json.dumps({"product_id": product_id, "status": "COLLECTED"}), encoding="utf-8")
    write_source_manifest(product)
    return product


class ProductionInputBoundaryTests(unittest.TestCase):
    def test_manual_test_identity_is_rejected_from_formal_production(self):
        with tempfile.TemporaryDirectory() as directory:
            product = make_formal_product(Path(directory), "P000911")
            source_path = product / "input/source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["source_kind"] = "manual_test"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ProductionInputError, "manual_test"):
                validate_formal_product_input(product)

    def test_manual_test_cannot_create_batch_or_queue_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_formal_product(root, "P000917")
            source_path = product / "input/source.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["source_kind"] = "manual_test"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "current workbench collection"):
                create_batch(root, ["P000917"])
            self.assertFalse((root / "batches").exists())
            self.assertFalse((root / "runtime/queue").exists())

    def test_product_a_cannot_read_product_b_even_with_same_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product_a = make_formal_product(root, "P000912")
            product_b = make_formal_product(root, "P000913")
            own = product_a / "input/sku-images/same-name-1.png"
            other = product_b / "input/sku-images/same-name-1.png"
            self.assertEqual(validate_product_reference(product_a, own), own.resolve())
            with self.assertRaisesRegex(ValueError, "current product|当前商品|product reference"):
                validate_product_reference(product_a, other)
            self.assertNotEqual(own.resolve(), other.resolve())

    def test_generated_output_cannot_become_a_product_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            product = make_formal_product(Path(directory), "P000914")
            generated = product / "output/generated-images/detail/detail-001.png"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(PNG)
            with self.assertRaisesRegex(ValueError, "product reference"):
                validate_product_reference(product, generated)

    def test_unregistered_or_mutated_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            product = make_formal_product(Path(directory), "P000915")
            injected = product / "input/sku-images/injected.png"
            injected.write_bytes(PNG)
            with self.assertRaisesRegex(ValueError, "未登记"):
                validate_product_reference(product, injected)
            registered = product / "input/sku-images/same-name-1.png"
            registered.write_bytes(PNG + b"changed")
            with self.assertRaisesRegex(ProductionInputError, "已变化|哈希"):
                validate_formal_product_input(product)

    def test_archived_product_cannot_supply_a_new_run(self):
        with tempfile.TemporaryDirectory() as directory:
            product = make_formal_product(Path(directory), "P000916")
            (product / "status.json").write_text(json.dumps({"status": "ARCHIVED"}), encoding="utf-8")
            with self.assertRaisesRegex(ProductionInputError, "归档"):
                validate_formal_product_input(product)

    def test_p9_identity_is_reserved_and_cannot_enter_formal_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_formal_product(root, "P900123")
            with self.assertRaisesRegex(ProductionInputError, "测试/审计"):
                validate_formal_product_input(product)
            with self.assertRaisesRegex(ValueError, "current workbench collection"):
                create_batch(root, ["P900123"])
            self.assertFalse((root / "batches").exists())

    def test_batch_freezes_mode_and_source_manifest_for_every_product(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = make_formal_product(root, "P000921")
            second = make_formal_product(root, "P000922")
            batch = create_batch(root, [first.name, second.name], auto_upload=True)
            for entry in batch["products"]:
                self.assertTrue(entry["auto_upload"])
                self.assertEqual(entry["review_mode"], "automatic")
                self.assertFalse(entry["manual_confirmation_required"])
                self.assertEqual(entry["source_snapshot_binding"]["source_manifest_sha256"], entry["source_manifest_sha256"])
                status = json.loads((root / "products" / entry["product_id"] / "status.json").read_text())
                contract = json.loads((root / "products" / entry["product_id"] / "output/image-asset-contract.json").read_text())
                self.assertEqual(status["batch_id"], batch["batch_id"])
                self.assertEqual(status["review_mode"], "automatic")
                self.assertFalse(contract["manual_confirmation_required"])

    def test_run_time_manual_mode_overrides_collection_time_auto_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_formal_product(root, "P000925")
            write_asset_contract(product, collection_id="COL-P000925-BOUNDARY", manual_confirmation_required=False)
            batch = create_batch(root, [product.name], auto_upload=False)
            entry = batch["products"][0]
            contract = json.loads((product / "output/image-asset-contract.json").read_text())
            self.assertEqual(entry["review_mode"], "manual")
            self.assertFalse(entry["auto_upload"])
            self.assertTrue(contract["manual_confirmation_required"])
            # A later global UI setting change cannot rewrite this frozen entry.
            saved_entry = json.loads(
                (root / "batches" / batch["batch_id"] / "batch.json").read_text()
            )["products"][0]
            self.assertEqual(saved_entry["review_mode"], "manual")
            self.assertFalse(saved_entry["auto_upload"])

    def test_validation_failure_does_not_modify_another_products_asset_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = make_formal_product(root, "P000923")
            invalid = make_formal_product(root, "P900923")
            with self.assertRaises(ValueError):
                create_batch(root, [valid.name, invalid.name], auto_upload=True)
            self.assertFalse((valid / "output/image-asset-contract.json").exists())
            self.assertFalse((root / "batches").exists())

    def test_whole_manifest_rewrite_is_blocked_after_batch_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_formal_product(root, "P000924")
            create_batch(root, [product.name], auto_upload=False)
            frozen = source_snapshot_binding(product)
            raw = product / "input/raw-snapshot.json"
            raw.write_text(json.dumps({"product_id": product.name, "rewritten": True}), encoding="utf-8")
            write_source_manifest(product)
            self.assertNotEqual(frozen["source_manifest_sha256"], source_snapshot_binding(product)["source_manifest_sha256"])
            with self.assertRaisesRegex(ProductionInputError, "重新采集"):
                validate_formal_product_input(product)


if __name__ == "__main__":
    unittest.main()
