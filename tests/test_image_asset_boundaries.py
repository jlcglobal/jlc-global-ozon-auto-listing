import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))

from ozon_uploader.service import current_image_completeness  # noqa: E402
from scripts.image_asset_boundaries import (  # noqa: E402
    accept_candidate,
    accepted_manifest_path,
    accepted_counterpart,
    reject_candidate,
    write_asset_contract,
)


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc000000301010018dd8db40000000049454e44ae426082"
)


def write_png(path: Path) -> None:
    Image.new("RGB", (900, 1200), (238, 242, 246)).save(path, format="PNG")


def image_contract_product(
    root: Path,
    sku_count: int = 3,
    *,
    manual_confirmation_required: bool = True,
    planned_with_qc: bool = False,
) -> tuple[Path, list[Path]]:
    product = root / "products/P999921"
    generated = product / "output/generated-images"
    (generated / "variant-main").mkdir(parents=True)
    (generated / "detail").mkdir(parents=True)
    (product / "input/sku-images").mkdir(parents=True)
    write_asset_contract(
        product,
        collection_id="COL-P999921-IMAGES",
        manual_confirmation_required=manual_confirmation_required,
    )
    mains = []
    candidates = []
    draft_skus = []
    for index in range(1, sku_count + 1):
        path = generated / "variant-main" / f"sku-{index}.png"
        write_png(path)
        candidates.append(path)
        mains.append({
            "slot": f"main-sku-{index}", "source_sku_id": f"sku-{index}",
            "output_path": str(path), "status": "planned" if planned_with_qc else "generated",
        })
        draft_skus.append({"source_sku_id": f"sku-{index}"})
    details = []
    for index in range(1, 9):
        path = generated / "detail" / f"detail-{index:03d}.png"
        write_png(path)
        candidates.append(path)
        details.append({
            "slot": f"detail-{index:03d}",
            "output_path": str(path),
            "status": "planned" if planned_with_qc else "generated",
        })
    (product / "output/image-plan.json").write_text(json.dumps({
        "main_images": mains, "detail_images": details,
    }), encoding="utf-8")
    if planned_with_qc:
        checked = [
            {"slot": item["slot"], "path": item["output_path"]}
            for item in [*mains, *details]
        ]
        (product / "output/image-qc-report.json").write_text(json.dumps({
            "schema_version": "1.0.0",
            "decision": "pass",
            "images_checked": checked,
        }), encoding="utf-8")
    (product / "output/ozon-draft.json").write_text(json.dumps({"skus": draft_skus}), encoding="utf-8")
    return product, candidates


class ImageAssetBoundaryTests(unittest.TestCase):
    def test_candidates_are_not_uploadable_until_every_n_plus_eight_image_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            product, candidates = image_contract_product(Path(directory), sku_count=3)
            before = current_image_completeness(product)
            self.assertFalse(before["passed"])
            self.assertTrue(any("accepted" in error for error in before["errors"]))
            for path in candidates:
                accept_candidate(product, path)
            after = current_image_completeness(product)
            self.assertTrue(after["passed"], after["errors"])
            self.assertEqual(len(after["main_images"]), 3)
            self.assertEqual(len(after["detail_images"]), 8)

    def test_auto_image_review_uses_qc_passed_candidates_without_accepted_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            product, _ = image_contract_product(
                Path(directory),
                sku_count=2,
                manual_confirmation_required=False,
                planned_with_qc=True,
            )
            result = current_image_completeness(product)
            self.assertTrue(result["passed"], result["errors"])
            self.assertEqual({item["asset_state"] for item in result["main_images"]}, {"candidate"})
            self.assertFalse((product / "output/accepted-images/manifest.json").exists())

    def test_auto_image_review_still_blocks_missing_detail_image(self):
        with tempfile.TemporaryDirectory() as directory:
            product, _ = image_contract_product(
                Path(directory),
                sku_count=1,
                manual_confirmation_required=False,
                planned_with_qc=True,
            )
            (product / "output/generated-images/detail/detail-008.png").unlink()
            result = current_image_completeness(product)
            self.assertFalse(result["passed"])
            self.assertTrue(any("detail-008" in error for error in result["errors"]))

    def test_reject_moves_only_candidate_and_invalidates_accepted_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            product, candidates = image_contract_product(Path(directory), sku_count=1)
            candidate = candidates[0]
            source = product / "input/sku-images/original.png"
            source.write_bytes(PNG + b"source")
            source_before = source.read_bytes()
            accepted = accept_candidate(product, candidate)
            self.assertEqual(accepted, accepted_counterpart(product, candidate))
            rejected = reject_candidate(product, candidate, group="manual-reject")
            self.assertTrue(rejected.is_file())
            self.assertFalse(candidate.exists())
            self.assertFalse(accepted.exists())
            self.assertEqual(source.read_bytes(), source_before)

    def test_confirmed_image_hash_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            product, candidates = image_contract_product(Path(directory), sku_count=1)
            for path in candidates:
                accept_candidate(product, path, confirmed_by="operator-1")
            manifest = json.loads(accepted_manifest_path(product).read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["entries"]), 9)
            accepted = Path(directory) / manifest["entries"][0]["accepted_path"]
            write_png(accepted)
            accepted.write_bytes(accepted.read_bytes() + b"tampered")
            result = current_image_completeness(product)
            self.assertFalse(result["passed"])
            self.assertTrue(any("发生变化" in error for error in result["errors"]))

    def test_design_change_revokes_all_previous_confirmations(self):
        with tempfile.TemporaryDirectory() as directory:
            product, candidates = image_contract_product(Path(directory), sku_count=1)
            for path in candidates:
                accept_candidate(product, path)
            plan_path = product / "output/image-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["detail_images"][0]["generation_prompt"] = "new design version"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = current_image_completeness(product)
            self.assertFalse(result["passed"])
            self.assertEqual(list((product / "output/accepted-images").rglob("*.png")), [])
            manifest = json.loads(accepted_manifest_path(product).read_text(encoding="utf-8"))
            self.assertEqual(manifest["entries"], [])


if __name__ == "__main__":
    unittest.main()
