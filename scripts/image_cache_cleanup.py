#!/usr/bin/env python3
"""Periodically remove old product images without losing product/Ozon records."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIRECTORIES = (
    "input/main-images",
    "input/sku-images",
    "input/detail-images",
    "output/generated-backgrounds",
    "output/generated-images",
    "output/images",
    "output/main-images",
    "output/detail-images",
    "output/ozon-image-staging",
)
REMOTE_PROCESSING_STATES = {"PENDING_REMOTE", "OZON_MODERATION", "UPLOADING"}
LOCAL_PROCESSING_STATES = {
    "QUEUED", "RUNNING", "PROCESSING", "CATEGORY_MATCHED",
    "CONTENT_GENERATED", "IMAGES_GENERATED", "PRICED", "OZON_READY",
}
SUCCESS_STATES = {"UPLOADED", "ACTIVE"}
FAILED_STATES = {"FAILED", "FAILED_HARD_BLOCKER"}
IMAGE_DEPENDENT_STEPS = {
    "image_generation", "image_qc", "marketplace_content",
    "field_completion", "ozon_upload",
}


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value or value == "unknown":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_time(values: Iterable[Any]) -> Optional[datetime]:
    parsed = [item for item in (_parse_time(value) for value in values) if item is not None]
    return max(parsed) if parsed else None


def _fallback_file_time(*paths: Path) -> Optional[datetime]:
    timestamps = []
    for path in paths:
        try:
            timestamps.append(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
        except OSError:
            continue
    return max(timestamps) if timestamps else None


def _directory_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return total
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _active_remote_products(root: Path) -> set[str]:
    active: set[str] = set()
    for queue_name in ("image-channel-queue.json", "remote-pending-queue.json"):
        queue = _load(root / queue_name, {"items": []}) or {"items": []}
        for item in queue.get("items") or []:
            product_id = item.get("product_id")
            status = str(item.get("status") or "")
            if product_id and status not in {"MEDIA_CONFIRMED", "UPLOADED", "ACTIVE", "FAILED"}:
                active.add(str(product_id))
    return active


def _batch_runner_alive(root: Path) -> bool:
    pid_path = root / "logs/batch-runner.pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, OSError):
        return False


def _cleanup_anchor(product_dir: Path, status: Dict[str, Any], source: Dict[str, Any]) -> tuple[Optional[datetime], str]:
    state = str(status.get("status") or "unknown")
    if state in SUCCESS_STATES:
        transfer = _load(product_dir / "output/ozon-image-transfer.json", {}) or {}
        if transfer.get("status") != "MEDIA_CONFIRMED":
            return None, "success_waiting_for_media_confirmation"
        return _parse_time(transfer.get("checked_at")), "ozon_media_confirmed"
    if state in FAILED_STATES:
        return _latest_time([
            status.get("last_run_at"), status.get("finished_at"), status.get("updated_at"),
            source.get("captured_at"), source.get("collected_at"),
        ]), "failed_last_activity"
    return _latest_time([
        source.get("captured_at"), source.get("collected_at"),
        status.get("started_at"), status.get("last_run_at"),
    ]), "unuploaded_last_activity"


def _invalidate_retry_image_steps(product_dir: Path, status: Dict[str, Any], cleaned_at: datetime) -> None:
    if str(status.get("status") or "") in SUCCESS_STATES:
        return
    completed = status.get("completed_steps")
    if isinstance(completed, list):
        status["completed_steps"] = [step for step in completed if step not in IMAGE_DEPENDENT_STEPS]
    pending = status.get("pending_steps")
    if isinstance(pending, list):
        retained = [step for step in pending if step not in IMAGE_DEPENDENT_STEPS]
        status["pending_steps"] = [*retained, *sorted(IMAGE_DEPENDENT_STEPS - set(retained))]
    status["images_cleaned_at"] = cleaned_at.isoformat(timespec="seconds")
    status["images_require_regeneration_on_retry"] = True
    _write_atomic(product_dir / "status.json", status)

    cache_path = product_dir / "output/pipeline-cache.json"
    cache = _load(cache_path, {}) or {}
    steps = cache.get("steps")
    if isinstance(steps, dict):
        for step in IMAGE_DEPENDENT_STEPS:
            steps.pop(step, None)
        _write_atomic(cache_path, cache)


def _remove_product_images(product_dir: Path) -> tuple[int, list[str]]:
    freed = 0
    removed = []
    for relative in IMAGE_DIRECTORIES:
        target = product_dir / relative
        if not target.is_dir():
            continue
        freed += _directory_size(target)
        shutil.rmtree(target)
        removed.append(relative)
    return freed, removed


def cleanup_images(
    root: Path,
    settings: Dict[str, Any],
    *,
    current_time: Optional[datetime] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Run one safe cleanup pass. Ozon/local processing products are protected."""
    current = (current_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    enabled = bool(settings.get("image_cleanup_enabled", True))
    interval = float(settings.get("image_cleanup_interval_hours", 24))
    retention_days = float(settings.get("image_retention_days", 10))
    state_path = root / "logs/image-cleanup-state.json"
    previous = _load(state_path, {}) or {}
    last_run = _parse_time(previous.get("last_run_at"))
    if not enabled:
        return {"status": "disabled", "deleted_product_count": 0, "freed_bytes": 0}
    if not force and last_run and current - last_run < timedelta(hours=interval):
        return {
            "status": "not_due", "last_run_at": previous.get("last_run_at"),
            "next_run_at": (last_run + timedelta(hours=interval)).isoformat(timespec="seconds"),
            "deleted_product_count": 0, "freed_bytes": 0,
        }

    active_remote = _active_remote_products(root)
    runner_alive = _batch_runner_alive(root)
    cutoff = current - timedelta(days=retention_days)
    results = []
    freed_total = 0
    for product_dir in sorted((root / "products").glob("P[0-9]*")):
        status_path = product_dir / "status.json"
        source_path = product_dir / "input/source.json"
        status = _load(status_path, {}) or {}
        source = _load(source_path, {}) or {}
        state = str(status.get("status") or "unknown")
        if product_dir.name in active_remote or state in REMOTE_PROCESSING_STATES:
            results.append({"product_id": product_dir.name, "action": "protected", "reason": "ozon_processing"})
            continue
        if runner_alive and state in LOCAL_PROCESSING_STATES:
            results.append({"product_id": product_dir.name, "action": "protected", "reason": "local_pipeline_running"})
            continue
        anchor, reason = _cleanup_anchor(product_dir, status, source)
        anchor = anchor or _fallback_file_time(status_path, source_path)
        if anchor is None or anchor > cutoff:
            results.append({
                "product_id": product_dir.name, "action": "kept", "reason": reason,
                "cleanup_after": (anchor + timedelta(days=retention_days)).isoformat(timespec="seconds") if anchor else "unknown",
            })
            continue
        freed, removed = _remove_product_images(product_dir)
        if not removed:
            continue
        freed_total += freed
        _invalidate_retry_image_steps(product_dir, status, current)
        product_report = {
            "product_id": product_dir.name,
            "cleaned_at": current.isoformat(timespec="seconds"),
            "retention_days": retention_days,
            "anchor_at": anchor.isoformat(timespec="seconds"),
            "anchor_reason": reason,
            "removed_directories": removed,
            "freed_bytes": freed,
            "preserved": [
                "source.json and raw diagnostics", "product text and pricing outputs",
                "offer_id, task_id and Ozon responses", "status and error history",
            ],
        }
        _write_atomic(product_dir / "output/image-cleanup-report.json", product_report)
        results.append({"action": "deleted", **product_report})

    report = {
        "status": "completed",
        "last_run_at": current.isoformat(timespec="seconds"),
        "retention_days": retention_days,
        "deleted_product_count": sum(item.get("action") == "deleted" for item in results),
        "freed_bytes": freed_total,
        "results": results,
    }
    _write_atomic(state_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    settings = _load(ROOT / "config/pipeline-settings.json", {}) or {}
    print(json.dumps(cleanup_images(ROOT, settings, force=args.force), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
