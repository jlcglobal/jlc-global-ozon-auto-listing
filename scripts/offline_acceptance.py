#!/usr/bin/env python3
"""Prepare/audit a manual gold fixture without impersonating production.

This utility never writes final Russian copy, a creative brief, an image plan,
candidate images, a formal product, an inbox row, a payload or an Ozon queue.
It only validates the physically isolated manual-test input and reports which
real production stages still need to be exercised with connected Codex.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE = "P900002"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def audit(case_id: str) -> Dict[str, Any]:
    manual_input = ROOT / "test-data/manual-input" / case_id
    manual_output = ROOT / "test-data/manual-output" / case_id
    source_path = manual_input / "source.json"
    errors = []
    if not source_path.is_file():
        errors.append("manual source.json is missing")
        source: Dict[str, Any] = {}
    else:
        source = load(source_path)
    if source.get("source_kind") != "manual_test":
        errors.append("manual fixture must have source_kind=manual_test")
    if source.get("product_id") != case_id:
        errors.append("manual fixture product_id does not match its test case")
    if not str(source.get("source_path") or "").startswith(f"test-data/manual-input/{case_id}/"):
        errors.append("manual fixture source_path escapes manual-input")
    sku_images = sorted(path for path in (manual_input / "sku-images").glob("*") if path.is_file())
    if case_id == "P900002" and [path.name for path in sku_images] != ["sku-3l.png", "sku-5l.png", "sku-6l.png"]:
        errors.append("P900002 must contain only its three canonical SKU references")
    if any((manual_input / name).is_dir() and any((manual_input / name).iterdir()) for name in ("main-images", "detail-images")):
        errors.append("P900002 has no user-provided main/detail scene images")
    if (ROOT / "products" / case_id).exists():
        errors.append("manual fixture leaked into formal products")
    serialized = json.dumps(source, ensure_ascii=False)
    if "products/" in serialized or "/output/generated-images/" in serialized:
        errors.append("manual source contains a formal-product or generated-output reference")

    rejected = sorted((manual_output / "rejected-generation").rglob("*.png"))
    rejected_prior = sorted((manual_output / "rejected-generation" / "2026-07-16-prior-run").rglob("*.png"))
    candidates = sorted((manual_output / "generated-images").rglob("*.png"))
    accepted = sorted((manual_output / "accepted-images").rglob("*.png"))
    return {
        "schema_version": "1.0.0",
        "case_id": case_id,
        "test_identity": "manual_test",
        "manual_input": str(manual_input.relative_to(ROOT)),
        "manual_output": str(manual_output.relative_to(ROOT)),
        "formal_product_exists": (ROOT / "products" / case_id).exists(),
        "sku_images": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in sku_images],
        "user_provided_main_images": 0,
        "user_provided_detail_images": 0,
        "candidate_images": len(candidates),
        "candidate_image_paths": [str(path.relative_to(ROOT)) for path in candidates],
        "accepted_images": len(accepted),
        "rejected_outputs": len(rejected),
        "rejected_prior_outputs": len(rejected_prior),
        "production_stages_proven": [],
        "still_required": [
            "connected Codex product analysis",
            "connected Codex Russian SEO listing",
            "unified ecommerce design",
            "N SKU mains plus 8 shared detail candidates",
            "technical QC and one human review",
        ],
        "quality_status": (
            "WAITING_FOR_HUMAN_SAMPLE_REVIEW"
            if candidates else "WAITING_FOR_REAL_CONNECTED_CODEX_PRODUCTION_TEST"
        ),
        "errors": errors,
        "ozon_write_calls": 0,
        "ozon_read_calls": 0,
        "inventory_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", default=DEFAULT_CASE)
    args = parser.parse_args()
    result = audit(args.case_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
