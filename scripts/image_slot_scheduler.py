#!/usr/bin/env python3
"""Build bounded parallel waves for one product's image slots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def requested_slot_names(value: Any) -> set[str]:
    names = set()
    for item in value or []:
        if isinstance(item, dict):
            item = item.get("slot") or item.get("image_slot")
        slot = str(item or "").strip()
        if slot:
            names.add(slot)
    return names


def pending_slots(product_dir: Path, concurrency: int) -> Dict[str, Any]:
    concurrency = max(1, min(int(concurrency), 4))
    plan = load_json(product_dir / "output/image-plan.json")
    requested_path = product_dir / "output/image-regeneration-request.json"
    requested = requested_slot_names(load_json(requested_path).get("failed_slots")) if requested_path.is_file() else set()
    planned_by_slot = {
        str(item.get("slot") or ""): item
        for key in ("main_images", "detail_images", "disclaimer_images")
        for item in plan.get(key) or []
    }
    if requested and all(
        slot in planned_by_slot
        and (ROOT / str(planned_by_slot[slot].get("output_path") or "")).is_file()
        and (product_dir / "output/product-lock" / f"{slot}.json").is_file()
        for slot in requested
    ):
        requested = set()
    main_slots: List[Dict[str, Any]] = []
    detail_slots: List[Dict[str, Any]] = []
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key) or []:
            slot = str(item.get("slot") or "")
            if not slot or item.get("status") == "needs_review":
                continue
            if requested and slot not in requested:
                continue
            output = ROOT / str(item.get("output_path") or "")
            manifest = product_dir / "output/product-lock" / f"{slot}.json"
            if not requested and output.is_file() and manifest.is_file():
                continue
            target = main_slots if key == "main_images" else detail_slots
            target.append({
                "slot": slot,
                "image_type": item.get("image_type"),
                "output_path": item.get("output_path"),
            })
    waves = [
        *[main_slots[index:index + concurrency] for index in range(0, len(main_slots), concurrency)],
        *[detail_slots[index:index + concurrency] for index in range(0, len(detail_slots), concurrency)],
    ]
    return {
        "product_id": product_dir.name,
        "concurrency": concurrency,
        "main_images_first": True,
        "pending_slot_count": len(main_slots) + len(detail_slots),
        "waves": waves,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(
        pending_slots(Path(args.product_dir).resolve(), args.concurrency),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
