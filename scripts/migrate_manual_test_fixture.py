#!/usr/bin/env python3
"""Move the P900002 conversation fixture out of formal production storage.

This migration is deliberately local and idempotent.  It never calls Ozon,
never writes the inbox/database/queue and never deletes the three user-provided
SKU images.  Old generated previews remain available as rejected test output.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CASE_ID = "P900002"
OLD = ROOT / "products" / CASE_ID
MANUAL_INPUT = ROOT / "test-data/manual-input" / CASE_ID
MANUAL_OUTPUT = ROOT / "test-data/manual-output" / CASE_ID
REFERENCE = ROOT / "references/fridge-organizer-3sku-2026-07-16"


def move_contents(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in list(source.iterdir()):
        target = destination / item.name
        if target.exists():
            if item.is_dir() and target.is_dir():
                move_contents(item, target)
                item.rmdir()
                continue
            raise RuntimeError(f"migration target already exists: {target}")
        shutil.move(str(item), str(target))


def rewrite(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: rewrite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item) for item in value]
    if isinstance(value, str):
        return value.replace(
            f"products/{CASE_ID}/input", f"test-data/manual-input/{CASE_ID}"
        ).replace(
            f"products/{CASE_ID}/output", f"test-data/manual-output/{CASE_ID}"
        )
    return value


def rewrite_json_files(root: Path) -> None:
    for path in root.rglob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        path.write_text(json.dumps(rewrite(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    MANUAL_INPUT.mkdir(parents=True, exist_ok=True)
    MANUAL_OUTPUT.mkdir(parents=True, exist_ok=True)
    if OLD.is_dir():
        move_contents(OLD / "input", MANUAL_INPUT)
        move_contents(OLD / "output", MANUAL_OUTPUT)
        for item in list(OLD.iterdir()):
            target = MANUAL_OUTPUT / item.name
            if target.exists():
                raise RuntimeError(f"migration target already exists: {target}")
            shutil.move(str(item), str(target))
        OLD.rmdir()

    duplicate_main = MANUAL_INPUT / "main-images"
    audit_main = MANUAL_INPUT / "audit-duplicate-main-images"
    if duplicate_main.is_dir() and any(duplicate_main.iterdir()):
        move_contents(duplicate_main, audit_main)
    duplicate_main.mkdir(parents=True, exist_ok=True)
    (MANUAL_INPUT / "detail-images").mkdir(parents=True, exist_ok=True)

    gold = REFERENCE / "gold-standard.json"
    if gold.is_file() and not (MANUAL_INPUT / "gold-standard.json").exists():
        shutil.move(str(gold), str(MANUAL_INPUT / "gold-standard.json"))
    reference_images = REFERENCE / "source-images"
    audit_refs = MANUAL_INPUT / "audit-reference-duplicates"
    if reference_images.is_dir() and any(reference_images.iterdir()):
        move_contents(reference_images, audit_refs)

    source_path = MANUAL_INPUT / "source.json"
    if source_path.is_file():
        source = rewrite(json.loads(source_path.read_text(encoding="utf-8")))
        source.update({
            "product_id": CASE_ID,
            "collection_id": "MANUAL-P900002-20260716",
            "source_kind": "manual_test",
            "source_path": f"test-data/manual-input/{CASE_ID}/source.json",
            "raw_capture_file": f"test-data/manual-input/{CASE_ID}/raw-snapshot.json",
        })
        source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (MANUAL_OUTPUT / "generated-images/variant-main").mkdir(parents=True, exist_ok=True)
    (MANUAL_OUTPUT / "generated-images/detail").mkdir(parents=True, exist_ok=True)
    (MANUAL_OUTPUT / "accepted-images").mkdir(parents=True, exist_ok=True)
    rewrite_json_files(MANUAL_INPUT)
    rewrite_json_files(MANUAL_OUTPUT)

    report = {
        "case_id": CASE_ID,
        "source_kind": "manual_test",
        "formal_product_exists": OLD.exists(),
        "sku_image_count": len(list((MANUAL_INPUT / "sku-images").glob("*"))),
        "main_image_count": len(list((MANUAL_INPUT / "main-images").glob("*"))),
        "detail_image_count": len(list((MANUAL_INPUT / "detail-images").glob("*"))),
        "rejected_image_count": len(list((MANUAL_OUTPUT / "rejected-generation").rglob("*.png"))),
        "accepted_image_count": len(list((MANUAL_OUTPUT / "accepted-images").rglob("*.png"))),
        "ozon_write_calls": 0,
        "ozon_read_calls": 0,
        "inventory_calls": 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
