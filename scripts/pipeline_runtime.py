"""Shared checkpoint state for the full product pipeline."""
from __future__ import annotations

import json
import tempfile
import uuid
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from scripts.production_input_guard import (
        ProductionInputError,
        source_snapshot_binding,
        validate_formal_product_input,
    )
    from scripts.image_asset_boundaries import write_asset_contract
except ModuleNotFoundError:
    from production_input_guard import ProductionInputError, source_snapshot_binding, validate_formal_product_input
    from image_asset_boundaries import write_asset_contract

PHASE_A_STEPS = [
    "validate_source", "product_analysis", "category_match", "variant_rules",
    "measurements", "offer_exists_check", "upload_feasibility",
]
PHASE_B_STEPS = [
    "product_positioning", "ecommerce_design", "russian_copy", "marketplace_content", "field_completion",
    "style_selector", "image_plan", "image_generation", "image_qc",
    "final_upload_check", "ozon_upload",
]
PIPELINE_STEPS = [*PHASE_A_STEPS, *PHASE_B_STEPS]
MAX_SELECTED_SKUS_PER_PRODUCT = 10
# HANDED_OFF_TO_OZON is terminal locally: task_id was accepted by Ozon and the
# operator continues the product card in Ozon. No local remote poll is needed.
TERMINAL_STATES = {
    "UPLOADED", "OZON_MODERATION", "ACTIVE", "PENDING_REMOTE",
    "HANDED_OFF_TO_OZON", "WAITING_MANUAL_REVIEW", "FAILED_HARD_BLOCKER",
}
BATCH_TERMINAL_STATES = {
    "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED",
    # The generation batch is finished, but the product remains available for
    # a separate, user-triggered manual upload batch.
    "AWAITING_MANUAL_UPLOAD",
}
STEP_STATUS = {
    "validate_source": "PROCESSING",
    "product_analysis": "PROCESSING",
    "product_positioning": "PROCESSING",
    "ecommerce_design": "PROCESSING",
    "category_match": "CATEGORY_MATCHED",
    "variant_rules": "CATEGORY_MATCHED",
    "russian_copy": "PROCESSING",
    "style_selector": "CONTENT_GENERATED",
    "image_plan": "CONTENT_GENERATED",
    "marketplace_content": "PROCESSING",
    "measurements": "PRICED",
    "offer_exists_check": "PRICED",
    "upload_feasibility": "PRICED",
    "image_generation": "IMAGES_GENERATED",
    "image_qc": "IMAGES_GENERATED",
    "field_completion": "CONTENT_GENERATED",
    "final_upload_check": "OZON_READY",
    "ozon_upload": "UPLOADING",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def normalize_checkpoint(status: Dict[str, Any]) -> Dict[str, Any]:
    status.pop("review", None)
    completed = list(dict.fromkeys(status.get("completed_steps") or ["collect_source"]))
    # These legacy steps only rechecked outputs already produced by the
    # preceding step. Keep old products resumable without scheduling them.
    completed = [
        step for step in completed
        if step == "collect_source" or step in PIPELINE_STEPS
    ]
    # A local, never-submitted product may have checkpoints from the older
    # image-first order. Keep only the contiguous prefix in the new order so
    # final text/attributes are guaranteed to exist before any image prompt.
    remote_or_written = (
        str(status.get("status") or "").upper()
        in {"UPLOADING", "PENDING_REMOTE", "HANDED_OFF_TO_OZON", "OZON_MODERATION", "UPLOADED", "ACTIVE"}
        or int(status.get("api_write_count") or 0) > 0
    )
    if not remote_or_written:
        contiguous = ["collect_source"] if "collect_source" in completed else []
        for step in PIPELINE_STEPS:
            if step not in completed:
                break
            contiguous.append(step)
        completed = contiguous
    pending = [step for step in PIPELINE_STEPS if step not in completed]
    status.update({
        "completed_steps": completed,
        "pending_steps": pending,
        "failed_step": status.get("failed_step") or "unknown",
        "retry_count_by_step": status.get("retry_count_by_step") or {},
        "api_write_count": int(status.get("api_write_count") or 0),
        "last_run_at": status.get("last_run_at") or "unknown",
        "next_action": pending[0] if pending else "complete",
        "task_authorized": bool(status.get("task_authorized", False)),
        "batch_id": status.get("batch_id") or "unknown",
        "ai_service_state": status.get("ai_service_state") or "normal",
        "ai_service_reason": status.get("ai_service_reason") or "unknown",
        "ai_service_retry_after": status.get("ai_service_retry_after") or "unknown",
        "ai_service_retry_count": int(status.get("ai_service_retry_count") or 0),
    })
    return status


def queue_product(product_dir: Path, batch_id: str) -> Dict[str, Any]:
    path = product_dir / "status.json"
    status = normalize_checkpoint(load_json(path))
    if status["status"] not in {"COLLECTED", "FAILED_HARD_BLOCKER", "STOPPED"}:
        status.update({"task_authorized": True, "batch_id": batch_id, "last_run_at": now()})
        write_json_atomic(path, status)
        return status
    source_path = product_dir / "input/source.json"
    source = load_json(source_path)
    selected_sku_count = len(source.get("skus") or [])
    if not 1 <= selected_sku_count <= MAX_SELECTED_SKUS_PER_PRODUCT:
        raise ValueError(
            f"{product_dir.name}: selected SKU count must be between 1 and "
            f"{MAX_SELECTED_SKUS_PER_PRODUCT}, got {selected_sku_count}"
        )
    previous = status["status"]
    old_warnings = list(status.get("warnings") or [])
    if old_warnings:
        status.setdefault("warning_history", []).append({
            "batch_id": status.get("batch_id") or "unknown",
            "archived_at": now(),
            "warnings": old_warnings,
        })
    status.update({
        "status": "QUEUED", "current_step": "queue", "progress": 1,
        "task_authorized": True, "batch_id": batch_id, "last_run_at": now(),
        "failed_step": "unknown", "next_action": status["pending_steps"][0],
        "warnings": [], "retry_count_by_step": {},
        "host_recovery_state": "normal", "host_recovery_reason": "unknown",
        "ai_service_state": "normal", "ai_service_reason": "unknown",
        "ai_service_retry_after": "unknown", "ai_service_retry_count": 0,
    })
    status.setdefault("history", []).append({
        "from": previous, "to": "QUEUED", "at": now(),
        "reason": f"User started batch task {batch_id}; no per-product review is required.",
    })
    write_json_atomic(path, status)
    return status


def mark_hard_failure(product_dir: Path, step: str, reason: str) -> Dict[str, Any]:
    path = product_dir / "status.json"
    status = normalize_checkpoint(load_json(path))
    previous = status.get("status")
    retry_count = int((status.get("retry_count_by_step") or {}).get(step, 0))
    status.update({
        "status": "FAILED_HARD_BLOCKER",
        "current_step": step if step in PIPELINE_STEPS else "validate_source",
        "failed_step": step,
        "error_code": "PIPELINE_HARD_BLOCKER",
        "error_message": reason,
        "last_run_at": now(),
        "next_action": "retry_failed_step",
    })
    status.setdefault("warnings", []).append(reason)
    status.setdefault("retry_count_by_step", {})[step] = retry_count
    status.setdefault("steps", []).append({
        "name": status["current_step"],
        "status": "failed",
        "started_at": now(),
        "finished_at": now(),
        "retry_count": retry_count,
        "retryable": True,
        "error": {
            "step": status["current_step"],
            "reason": reason,
            "occurred_at": now(),
            "retryable": True,
        },
    })
    status.setdefault("history", []).append({
        "from": previous,
        "to": "FAILED_HARD_BLOCKER",
        "at": now(),
        "reason": reason,
    })
    write_json_atomic(path, status)
    maybe_create_operator_question(product_dir, step, reason)
    return status


def maybe_create_operator_question(product_dir: Path, step: str, reason: str) -> Optional[Dict[str, Any]]:
    """Pause only for critical product identity ambiguity, never optional fields."""
    text = str(reason or "")
    lowered = text.casefold()
    critical_signals = (
        "sku对应", "sku mapping", "sku mismatch", "变体对应", "商品结构", "product structure",
        "配件数量", "accessory count", "错商品", "product identity", "参考图", "reference image",
        "颜色对应", "color mapping",
    )
    ambiguity_signals = (
        "无法确认", "不能确认", "不明确", "歧义", "缺少", "不一致", "冲突",
        "unknown", "ambiguous", "missing", "mismatch", "conflict", "cannot determine",
    )
    if not any(signal in lowered for signal in critical_signals):
        return None
    if not any(signal in lowered for signal in ambiguity_signals):
        return None
    path = product_dir / "input/pending-question.json"
    if path.is_file():
        current = load_json(path)
        if str(current.get("status") or "").upper() == "OPEN":
            return current
    question = {
        "schema_version": "1.0.0",
        "question_id": f"Q-{uuid.uuid4().hex[:12].upper()}",
        "product_id": product_dir.name,
        "status": "OPEN",
        "step": step,
        "question": "系统无法确认商品结构、SKU对应关系或配件数量。请用简单中文说明正确情况。",
        "reason": text,
        "created_at": now(),
        "answer": "unknown",
        "answered_at": "unknown",
        "answered_by": "unknown",
    }
    write_json_atomic(path, question)
    return question


def complete_step(product_dir: Path, step: str) -> Dict[str, Any]:
    if step not in PIPELINE_STEPS:
        raise ValueError(f"Unknown pipeline step: {step}")
    path = product_dir / "status.json"
    status = normalize_checkpoint(load_json(path))
    if step in status["completed_steps"]:
        return status
    previous = status.get("status")
    status["completed_steps"].append(step)
    status["completed_steps"] = list(dict.fromkeys(status["completed_steps"]))
    status["pending_steps"] = [item for item in PIPELINE_STEPS if item not in status["completed_steps"]]
    target = STEP_STATUS[step]
    if step == "ozon_upload" and status.get("status") in {
        "UPLOADED", "OZON_MODERATION", "ACTIVE", "PENDING_REMOTE", "HANDED_OFF_TO_OZON", "FAILED_HARD_BLOCKER"
    }:
        target = status["status"]
    status.update({
        "status": target,
        "current_step": step,
        "progress": 100 if target in {"UPLOADED", "OZON_MODERATION", "ACTIVE", "PENDING_REMOTE", "HANDED_OFF_TO_OZON"} else min(
            99, round(100 * len(status["completed_steps"]) / (len(PIPELINE_STEPS) + 1))
        ),
        "failed_step": "unknown",
        "error_code": "unknown",
        "error_message": "unknown",
        "last_run_at": now(),
        "next_action": status["pending_steps"][0] if status["pending_steps"] else "complete",
        "active_step": None,
        "ai_service_state": "normal",
        "ai_service_reason": "unknown",
        "ai_service_retry_after": "unknown",
    })
    status.setdefault("steps", []).append({
        "name": step,
        "status": "completed",
        "started_at": now(),
        "finished_at": now(),
        "retry_count": int((status.get("retry_count_by_step") or {}).get(step, 0)),
        "retryable": True,
        "error": None,
    })
    if previous != target:
        status.setdefault("history", []).append({
            "from": previous,
            "to": target,
            "at": now(),
            "reason": f"Pipeline step {step} completed and its output was validated.",
        })
    write_json_atomic(path, status)
    return status


def request_single_image_regeneration(product_dir: Path, slots: List[str], reason: str) -> Dict[str, Any]:
    """Re-open only failed image slots; passed image files stay untouched."""
    path = product_dir / "status.json"
    status = normalize_checkpoint(load_json(path))
    retries = status.setdefault("retry_count_by_step", {})
    retries["image_generation"] = int(retries.get("image_generation") or 0) + 1
    if retries["image_generation"] > 1:
        return mark_hard_failure(product_dir, "image_generation", reason)
    write_json_atomic(product_dir / "output/image-regeneration-request.json", {
        "product_id": product_dir.name,
        "failed_slots": slots,
        "attempt": retries["image_generation"],
        "reason": reason,
        "requested_at": now(),
        "preserve_passed_images": True,
    })
    status["completed_steps"] = [
        step for step in status["completed_steps"] if step not in {"image_generation", "image_qc"}
    ]
    status["pending_steps"] = [step for step in PIPELINE_STEPS if step not in status["completed_steps"]]
    status.update({
        "status": "PROCESSING", "current_step": "image_qc", "next_action": "image_generation",
        "error_code": "unknown", "error_message": "unknown", "failed_step": "unknown", "last_run_at": now(),
    })
    status.setdefault("warnings", []).append(
        f"只自动重试未完成图片一次：{', '.join(slots)}。"
        if slots else
        "图片文件已保留，只自动重试生图收尾检查一次。"
    )
    write_json_atomic(path, status)
    return status


def collected_products(root: Path) -> List[Path]:
    """Return every collected product; the 10-SKU limit applies per product, not per batch."""
    latest_by_source: Dict[str, Path] = {}
    for product_dir in sorted((root / "products").glob("P[0-9]*")):
        status_path = product_dir / "status.json"
        source_path = product_dir / "input/source.json"
        if not status_path.is_file() or not source_path.is_file():
            continue
        if load_json(status_path).get("status") != "COLLECTED":
            continue
        source_url = str(load_json(source_path).get("source_url") or "")
        parsed = urllib.parse.urlparse(source_url)
        offer_match = parsed.path.rstrip("/").split("/")[-1].removesuffix(".html")
        canonical = offer_match if offer_match.isdigit() else (source_url or product_dir.name)
        latest_by_source[canonical] = product_dir
    return sorted(latest_by_source.values())


def retryable_products(root: Path) -> List[Path]:
    latest_by_source: Dict[str, Path] = {}
    for product_dir in sorted((root / "products").glob("P[0-9]*")):
        status_path = product_dir / "status.json"
        source_path = product_dir / "input/source.json"
        if not status_path.is_file() or not source_path.is_file():
            continue
        status = load_json(status_path)
        source = load_json(source_path)
        source_url = str(source.get("source_url") or "")
        if status.get("status") not in {"COLLECTED", "FAILED_HARD_BLOCKER"} or "1688.com/offer/" not in source_url:
            continue
        parsed = urllib.parse.urlparse(source_url)
        offer_match = parsed.path.rstrip("/").split("/")[-1].removesuffix(".html")
        canonical = offer_match if offer_match.isdigit() else source_url
        latest_by_source[canonical] = product_dir
    return sorted(latest_by_source.values())


def batch_path(root: Path, batch_id: str) -> Path:
    return root / "batches" / batch_id / "batch.json"


def batch_result_path(root: Path, batch_id: str) -> Path:
    return root / "batches" / batch_id / "batch-result.json"


def create_batch(
    root: Path,
    product_ids: List[str] | None = None,
    target_store_ids: List[str] | None = None,
    auto_upload: bool = False,
    product_store_overrides: Dict[str, List[str]] | None = None,
) -> Dict[str, Any]:
    """Freeze the current inbox into a batch before processing begins."""
    batch_id = f"B-{uuid.uuid4().hex[:12].upper()}"
    created_at = now()
    product_entries = []
    target_store_ids = list(dict.fromkeys(target_store_ids or []))
    product_store_overrides = product_store_overrides or {}
    # A normal "Run tasks" click freezes only newly collected products.
    # Failed products require an explicit product id so an old failure cannot
    # silently enter a later production batch.
    selected = collected_products(root)
    if product_ids is not None:
        selected = []
        for product_id in product_ids:
            product_dir = root / "products" / product_id
            source_path = product_dir / "input/source.json"
            status_path = product_dir / "status.json"
            if not source_path.is_file() or not status_path.is_file():
                raise ValueError(f"Requested product does not exist: {product_id}")
            status = load_json(status_path)
            if status.get("status") in TERMINAL_STATES and not (
                status.get("status") == "FAILED_HARD_BLOCKER"
                and int(status.get("api_write_count") or 0) == 0
            ):
                raise ValueError(f"Requested product is already terminal: {product_id}")
            if "1688.com/offer/" not in str(load_json(source_path).get("source_url") or ""):
                raise ValueError(f"Requested product is not a 1688 capture: {product_id}")
            selected.append(product_dir)
    prepared: List[tuple[Path, Dict[str, Any]]] = []
    review_mode = "automatic" if auto_upload else "manual"
    for product_dir in selected:
        try:
            validate_formal_product_input(product_dir)
        except ProductionInputError as exc:
            raise ValueError(
                f"{product_dir.name}: only the current workbench collection may enter a formal batch: {exc}"
            ) from exc
        source_path = product_dir / "input/source.json"
        sku_count = len(load_json(source_path).get("skus") or []) if source_path.is_file() else 0
        product_stores = list(dict.fromkeys(product_store_overrides.get(product_dir.name, target_store_ids)))
        binding = source_snapshot_binding(product_dir)
        entry = {
            "product_id": product_dir.name,
            "selected_sku_count": sku_count,
            "status": "QUEUED",
            "current_step": "queue",
            "started_at": "unknown",
            "completed_at": "unknown",
            "warnings": [],
            "errors": [],
            "target_store_ids": product_stores,
            "publication_count": len(product_stores),
            "collection_id": binding["collection_id"],
            "source_manifest_sha256": binding["source_manifest_sha256"],
            "source_snapshot_binding": binding,
            "auto_upload": bool(auto_upload),
            "review_mode": review_mode,
            "manual_confirmation_required": not bool(auto_upload),
        }
        product_entries.append(entry)
        prepared.append((product_dir, entry))
    batch = {
        "schema_version": "1.0.0",
        "batch_id": batch_id,
        "status": "QUEUED",
        "created_at": created_at,
        "started_at": "unknown",
        "completed_at": "unknown",
        "product_count": len(product_entries),
        "sku_count": sum(item["selected_sku_count"] for item in product_entries),
        "processing_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "progress": 0,
        "target_store_ids": target_store_ids,
        "auto_upload": bool(auto_upload),
        "review_mode": review_mode,
        "manual_upload_required": not bool(auto_upload),
        "inventory_submission_enabled": False,
        "products": product_entries,
    }
    write_json_atomic(batch_path(root, batch_id), batch)
    # All products and their store selections have already passed validation.
    # Freeze one identical mode/source contract for single- and multi-product
    # batches only after the batch record has been created successfully.
    for product_dir, entry in prepared:
        status_path = product_dir / "status.json"
        status = load_json(status_path)
        status.update({
            "batch_id": batch_id,
            "review_mode": entry["review_mode"],
            "auto_upload": entry["auto_upload"],
            "manual_confirmation_required": entry["manual_confirmation_required"],
            "source_snapshot_binding": entry["source_snapshot_binding"],
        })
        write_json_atomic(status_path, status)
        write_asset_contract(
            product_dir,
            collection_id=entry["collection_id"],
            manual_confirmation_required=entry["manual_confirmation_required"],
        )
    return batch
