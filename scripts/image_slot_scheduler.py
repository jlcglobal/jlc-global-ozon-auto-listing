#!/usr/bin/env python3
"""Build bounded parallel waves for one product's image slots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
BUILT_IN_IMAGE_GENERATION_SOURCE = "built_in_image_tool"


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


def requested_slots_from_request(value: Dict[str, Any] | None) -> set[str]:
    """Return every slot explicitly requested for regeneration.

    Older recovery files used ``failed_slots``.  Manual/host recovery can also
    write ``requested_slots`` to distinguish a prelaunch/programming fault from
    a real image-quality failure.  Both represent an explicit bounded recovery
    request and must be honored by the scheduler.
    """
    if not isinstance(value, dict):
        return set()
    names: set[str] = set()
    for key in ("requested_slots", "failed_slots", "slots"):
        names.update(requested_slot_names(value.get(key)))
    return names


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def has_builtin_image_source(value: Dict[str, Any] | None) -> bool:
    return bool(value) and (
        str(value.get("generation_source") or "").strip() == BUILT_IN_IMAGE_GENERATION_SOURCE
        and value.get("designer_prompt_followed") is True
        and value.get("local_script_generation") is False
    )


def is_main_slot(slot: Any) -> bool:
    return str(slot or "").strip().startswith("main-")


def visual_acceptance_passes(value: Dict[str, Any] | None, slot: Any) -> bool:
    if not is_main_slot(slot):
        return True
    if not isinstance(value, dict):
        return False
    acceptance = value.get("visual_acceptance")
    if not isinstance(acceptance, dict):
        return True
    checks = acceptance.get("checks")
    if not isinstance(checks, dict):
        return True
    # Treat main-image visual acceptance as quality telemetry, not as a
    # subjective regeneration gate. Product/SKU/structure/text failures are
    # still blocked by receipt.status, hard_failures, fact-lock and QC.
    return checks.get("product_visually_dominant") is not False


def resolve_planned_output_path(product_dir: Path, output_path: str) -> Path:
    """Resolve an image-plan output path against the product data directory.

    The code checkout can be a temporary runtime copy while ``products`` points
    at the formal project data directory.  Therefore plan paths must not assume
    they live below this script's ``ROOT``.
    """
    raw = str(output_path or "").strip()
    if not raw:
        return Path("")
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    parts = path.parts
    if len(parts) >= 2 and parts[0] == "products" and parts[1] == product_dir.name:
        return (product_dir.parent.parent / path).resolve()
    if parts and parts[0] == "output":
        return (product_dir / path).resolve()
    return (ROOT / path).resolve()


def checked_slot_is_current(item: Dict[str, Any] | None, output: Path) -> bool:
    if not item or str(item.get("status") or "").strip().lower() != "pass" or not output.is_file():
        return False
    if not has_builtin_image_source(item):
        return False
    if not visual_acceptance_passes(item, item.get("slot")):
        return False
    expected = str(item.get("sha256") or "")
    return bool(expected) and file_sha256(output) == expected


def locked_slot_is_current(manifest_path: Path, output: Path) -> bool:
    if not manifest_path.is_file() or not output.is_file():
        return False
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if str((manifest.get("audit") or {}).get("status") or "").strip().lower() != "pass":
        return False
    expected = str((manifest.get("output") or {}).get("sha256") or manifest.get("sha256") or "")
    return not expected or file_sha256(output) == expected


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.casefold() == "unknown":
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def receipt_slot_is_current(
    product_dir: Path,
    slot: str,
    output: Path,
    *,
    not_before: datetime | None = None,
) -> bool:
    """Treat an isolated PASS receipt as a valid checkpoint.

    The parent process can be interrupted after a child writes its per-slot
    receipt but before the parent merges it into ``image-hard-gate.json``.
    In that state the PNG plus matching receipt is still completed work and
    must not be regenerated.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(slot)).strip("-") or "unknown"
    receipt_path = product_dir / "output/image-slot-results" / f"{safe}.json"
    if not receipt_path.is_file() or not output.is_file():
        return False
    try:
        receipt = load_json(receipt_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if receipt.get("product_id") != product_dir.name or str(receipt.get("slot") or "") != str(slot):
        return False
    if str(receipt.get("status") or "").strip().lower() != "pass" or receipt.get("hard_failures"):
        return False
    if not has_builtin_image_source(receipt):
        return False
    if not visual_acceptance_passes(receipt, slot):
        return False
    lock_path = product_dir / "output/product-fact-lock.json"
    if lock_path.is_file():
        try:
            fact_lock_hash = str(load_json(lock_path).get("lock_hash") or "")
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        if fact_lock_hash and (receipt.get("fact_lock_checked") is not True or str(receipt.get("fact_lock_hash") or "") != fact_lock_hash):
            return False
    if not_before is not None:
        checked_at = parse_timestamp(receipt.get("checked_at"))
        if checked_at is None or checked_at < not_before:
            return False
    expected = str(receipt.get("sha256") or "")
    return bool(expected) and file_sha256(output) == expected


def pending_slots(product_dir: Path, concurrency: int) -> Dict[str, Any]:
    concurrency = max(1, min(int(concurrency), 3))
    plan = load_json(product_dir / "output/image-plan.json")
    requires_lock = (plan.get("generator_contract") or {}).get("product_pixel_lock_required") is True
    requires_verified_checkpoint = (
        (plan.get("generator_contract") or {}).get("true_parallel_slot_executor") is True
    )
    hard_gate_path = product_dir / "output/image-hard-gate.json"
    try:
        hard_gate = {
            str(item.get("slot")): item
            for item in (load_json(hard_gate_path).get("checked_slots") or [])
            if item.get("slot")
        }
    except (OSError, ValueError, json.JSONDecodeError):
        hard_gate = {}
    requested_path = product_dir / "output/image-regeneration-request.json"
    request = load_json(requested_path) if requested_path.is_file() else {}
    requested = requested_slots_from_request(request)
    requested_at = parse_timestamp(request.get("requested_at"))
    planned_by_slot = {
        str(item.get("slot") or ""): item
        for key in ("main_images", "detail_images", "disclaimer_images")
        for item in plan.get(key) or []
    }

    def requested_checkpoint_is_current(slot: str) -> bool:
        if slot not in planned_by_slot:
            return False
        output = resolve_planned_output_path(
            product_dir,
            str(planned_by_slot[slot].get("output_path") or ""),
        )
        if requested_at is not None:
            return receipt_slot_is_current(
                product_dir,
                slot,
                output,
                not_before=requested_at,
            )
        return (
            checked_slot_is_current(hard_gate.get(slot), output)
            or receipt_slot_is_current(product_dir, slot, output)
            or locked_slot_is_current(
                product_dir / "output/product-lock" / f"{slot}.json",
                output,
            )
        )

    if requested and all(requested_checkpoint_is_current(slot) for slot in requested):
        requested = set()
    main_slots: List[Dict[str, Any]] = []
    detail_slots: List[Dict[str, Any]] = []
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key) or []:
            slot = str(item.get("slot") or "")
            if not slot:
                continue
            # needs_human_input is a source-data problem, not an image retry.
            # Normal failed/needs_review image slots stay schedulable even
            # without a manual regeneration request, so an unattended batch can
            # recover failed slots before moving to later commercial images.
            if item.get("operation") == "needs_human_input":
                continue
            if requested and slot not in requested:
                continue
            output = resolve_planned_output_path(product_dir, str(item.get("output_path") or ""))
            manifest = product_dir / "output/product-lock" / f"{slot}.json"
            if requested and slot in requested:
                slot_current = requested_checkpoint_is_current(slot)
            else:
                slot_current = output.is_file() and (
                    checked_slot_is_current(hard_gate.get(slot), output)
                    or receipt_slot_is_current(product_dir, slot, output)
                    or (requires_lock and locked_slot_is_current(manifest, output))
                    or (not requested and not requires_verified_checkpoint and not requires_lock)
                )
            if slot_current:
                continue
            target = main_slots if key == "main_images" else detail_slots
            target.append({
                "slot": slot,
                "image_type": item.get("image_type"),
                "operation": item.get("operation"),
                "output_path": item.get("output_path"),
            })
    # Deterministic real-image compositions finish quickly and create a real
    # checkpoint before slower AI reference edits.  This prevents a healthy
    # detail batch from looking idle merely because its first AI call is slow.
    detail_slots.sort(key=lambda item: 0 if item.get("operation") == "compose_from_real_images" else 1)
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
