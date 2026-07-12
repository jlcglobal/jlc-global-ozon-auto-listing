#!/usr/bin/env python3
"""Run one frozen collection-inbox batch with checkpoints and bounded concurrency."""
from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from pipeline_runtime import (  # noqa: E402
    MAX_SELECTED_SKUS_PER_PRODUCT,
    PIPELINE_STEPS,
    TERMINAL_STATES,
    batch_path,
    batch_result_path,
    complete_step,
    create_batch,
    load_json,
    mark_hard_failure,
    now,
    queue_product,
    request_single_image_regeneration,
    write_json_atomic,
)
from pipeline_observability import (  # noqa: E402
    cache_hit,
    cache_store,
    input_hash,
    performance_finish,
    performance_start,
    prune_shared_analysis_cache,
    shared_analysis_cache_restore,
    shared_analysis_cache_store,
    shared_analysis_input_hash,
)
from image_cache_cleanup import cleanup_images  # noqa: E402
from ozon_metadata_prewarm import prewarm_category_tree  # noqa: E402
from product_deletion import deletion_requested  # noqa: E402

SETTINGS_PATH = ROOT / "config/pipeline-settings.json"
BATCH_LOCK_PATH = ROOT / "logs/.batch.lock"
BATCH_PID_PATH = ROOT / "logs/batch-runner.pid"
SAFE_STOP_REQUEST_PATH = ROOT / "logs/safe-stop-request.json"
IMAGE_GENERATION_STEPS = {"image_generation"}
IMAGE_QC_STEPS = {"image_qc"}
OZON_STEPS = {"ozon_upload"}
STEP_START_PROGRESS = {
    step: max(2, round(index * 95 / max(len(PIPELINE_STEPS), 1)))
    for index, step in enumerate(PIPELINE_STEPS)
}
SUCCESS_STATES = {"UPLOADED", "OZON_MODERATION", "ACTIVE"}
_batch_write_lock = threading.Lock()
_codex_semaphore = threading.BoundedSemaphore(2)


class ImageRegenerationRequested(RuntimeError):
    pass


class ProductDeletionRequested(RuntimeError):
    pass


class BatchSafeStopRequested(RuntimeError):
    """Raised after the active child process is stopped at its latest file checkpoint."""

    pass


def terminate_process_group(process: subprocess.Popen, grace_seconds: float = 5.0) -> None:
    """Stop a registered worker and every subprocess it launched."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        process.kill()
    process.wait(timeout=5)


def product_deleted(product_dir: Path) -> bool:
    runtime_root = product_dir.parent.parent if product_dir.parent.name == "products" else ROOT
    return deletion_requested(runtime_root, product_dir.name) or not product_dir.is_dir()


def product_worker_path(product_id: str) -> Path:
    return ROOT / "logs/product-workers" / f"{product_id}.json"


def run_registered_process(
    command: List[str], product_dir: Path, output: Any, timeout_seconds: int,
    env: Optional[Dict[str, str]] = None,
    completion_check: Optional[Callable[[], bool]] = None,
    completion_poll_seconds: float = 0.5,
) -> subprocess.Popen:
    if product_deleted(product_dir):
        raise ProductDeletionRequested(product_dir.name)
    process = subprocess.Popen(
        command, cwd=ROOT, env=env or os.environ.copy(), stdout=output,
        stderr=subprocess.STDOUT, text=True, start_new_session=True, close_fds=True,
    )
    worker_path = product_worker_path(product_dir.name)
    write_json_atomic(worker_path, {
        "product_id": product_dir.name, "pid": process.pid,
        "command": command[:3], "started_at": now(),
    })
    artifact_completed_early = False
    try:
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            status_path = product_dir / "status.json"
            status = load_json(status_path) if status_path.is_file() else {}
            batch_id = str(status.get("batch_id") or "")
            if batch_id and safe_stop_requested(batch_id):
                terminate_process_group(process)
                raise BatchSafeStopRequested(
                    f"Batch {batch_id} stopped during {status.get('current_step') or 'current step'}; "
                    "completed file checkpoints were preserved."
                )
            if completion_check is not None:
                try:
                    artifact_ready = bool(completion_check())
                except Exception:
                    artifact_ready = False
                if artifact_ready:
                    artifact_completed_early = True
                    terminate_process_group(process, grace_seconds=10)
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            time.sleep(min(max(completion_poll_seconds, 0.05), remaining))
    except subprocess.TimeoutExpired:
        terminate_process_group(process, grace_seconds=10)
        raise
    finally:
        worker_path.unlink(missing_ok=True)
    if product_deleted(product_dir):
        raise ProductDeletionRequested(product_dir.name)
    setattr(process, "artifact_completed_early", artifact_completed_early)
    return process


def file_signature(path: Path) -> Optional[tuple[int, int, int]]:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


def selected_sku_count(product_dir: Path) -> int:
    source_path = product_dir / "input/source.json"
    if not source_path.is_file():
        return 0
    return len(load_json(source_path).get("skus") or [])


def result_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    value = result.get("items") or result.get("offers") or []
    return value if isinstance(value, list) else []


def result_row(product_dir: Path) -> Dict[str, Any]:
    status = load_json(product_dir / "status.json")
    result_path = product_dir / "output/ozon-result.json"
    result = load_json(result_path) if result_path.is_file() else {}
    items = result_items(result)
    ozon = status.get("ozon") or {}
    exists_path = product_dir / "output/product-exists-check.json"
    exists = load_json(exists_path) if exists_path.is_file() else {}
    action = result.get("upload_action") or exists.get("action") or result.get("status") or "unknown"
    return {
        "product_id": product_dir.name,
        "selected_sku_count": selected_sku_count(product_dir),
        "status": status.get("status", "unknown"),
        "upload_action": action,
        "offer_ids": [item.get("offer_id") for item in items if item.get("offer_id")],
        "ozon_product_ids": [
            item.get("ozon_product_id") or item.get("product_id")
            for item in items
            if item.get("ozon_product_id") or item.get("product_id")
        ],
        "moderation_status": result.get("moderation_status", "unknown"),
        "warnings": status.get("warnings", []),
        "failed_step": status.get("failed_step", "unknown"),
        "errors": result.get("errors", ozon.get("errors", [])),
        "api_write_count": int(status.get("api_write_count") or 0),
    }


def batch_performance(rows: List[Dict[str, Any]], root: Path, batch_id: str) -> Dict[str, Any]:
    reports = []
    all_steps = []
    for row in rows:
        path = root / "products" / row["product_id"] / "output/performance-report.json"
        if path.is_file():
            report = load_json(path)
            current_steps = [
                item for item in report.get("steps") or []
                if item.get("batch_id") == batch_id
            ]
            if current_steps:
                reports.append((
                    row["product_id"],
                    sum(float(item.get("duration_seconds") or 0) for item in current_steps),
                ))
                all_steps.extend(current_steps)
    slowest_product = max(reports, key=lambda item: item[1], default=(None, 0.0))
    slowest_step = max(all_steps, key=lambda item: float(item.get("duration_seconds") or 0), default=None)
    image_times = [float(item.get("image_generation_seconds") or 0) for item in all_steps if item.get("step") == "image_generation"]
    ozon_times = [float(item.get("network_wait_seconds") or 0) for item in all_steps if item.get("step") in {"offer_exists_check", "ozon_upload", "ozon_status"}]
    cache_steps = [item for item in all_steps if "cache_hit" in item]
    return {
        "average_product_seconds": round(sum(value for _, value in reports) / len(reports), 3) if reports else 0.0,
        "slowest_product_id": slowest_product[0],
        "slowest_product_seconds": round(slowest_product[1], 3),
        "slowest_step": slowest_step.get("step") if slowest_step else None,
        "average_image_generation_seconds": round(sum(image_times) / len(image_times), 3) if image_times else 0.0,
        "category_rematch_count": sum(1 for item in all_steps if item.get("step") == "category_match" and not item.get("cache_hit")),
        "cache_hit_rate": round(sum(1 for item in cache_steps if item.get("cache_hit")) / len(cache_steps), 3) if cache_steps else 0.0,
        "ozon_api_average_seconds": round(sum(ozon_times) / len(ozon_times), 3) if ozon_times else 0.0,
    }


def safe_stop_requested(batch_id: str) -> bool:
    if not SAFE_STOP_REQUEST_PATH.is_file():
        return False
    try:
        request = load_json(SAFE_STOP_REQUEST_PATH)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return request.get("batch_id") == batch_id


def stop_batch_at_checkpoint(root: Path, batch: Dict[str, Any]) -> Dict[str, Any]:
    stopped_at = now()
    for entry in batch.get("products") or []:
        product_dir = root / "products" / str(entry.get("product_id") or "")
        status_path = product_dir / "status.json"
        if not status_path.is_file():
            continue
        status = load_json(status_path)
        if status.get("status") in TERMINAL_STATES:
            continue
        previous = status.get("status")
        status.update({
            "status": "STOPPED", "active_step": None,
            "last_run_at": stopped_at, "completed_at": "unknown",
        })
        status.setdefault("history", []).append({
            "from": previous, "to": "STOPPED", "at": stopped_at,
            "reason": "用户请求安全停止；已完成步骤和当前断点均已保存。",
        })
        write_json_atomic(status_path, status)
    batch = sync_batch(root, batch)
    rows = [
        result_row(root / "products" / entry["product_id"])
        for entry in batch.get("products") or []
        if (root / "products" / entry["product_id"]).is_dir()
    ]
    batch.update({
        "status": "STOPPED", "completed_at": stopped_at,
        "processing_count": 0,
    })
    report = {
        "schema_version": "1.0.0", "batch_id": batch["batch_id"],
        "status": "STOPPED", "started_at": batch["started_at"], "completed_at": stopped_at,
        "product_count": len(rows), "sku_count": sum(row["selected_sku_count"] for row in rows),
        "success_count": sum(row["status"] in SUCCESS_STATES for row in rows),
        "failed_count": sum(row["status"] == "FAILED_HARD_BLOCKER" for row in rows),
        "submitted_count": sum(row["api_write_count"] > 0 for row in rows),
        "pending_remote_count": sum(row["status"] == "PENDING_REMOTE" for row in rows),
        "uploaded_count": sum(row["status"] in {"UPLOADED", "ACTIVE"} for row in rows),
        "moderation_count": sum(row["status"] == "OZON_MODERATION" for row in rows),
        "create_count": sum(str(row["upload_action"]).lower() in {"create", "created"} for row in rows),
        "update_count": sum(str(row["upload_action"]).lower() in {"update", "updated"} for row in rows),
        "api_write_count": sum(row["api_write_count"] for row in rows),
        "inventory_api_called": False,
        "performance": batch_performance(rows, root, batch["batch_id"]),
        "products": rows,
    }
    write_json_atomic(batch_path(root, batch["batch_id"]), batch)
    write_json_atomic(batch_result_path(root, batch["batch_id"]), report)
    write_json_atomic(root / "batch-result.json", report)
    SAFE_STOP_REQUEST_PATH.unlink(missing_ok=True)
    return report


def acquire_batch_lock() -> int:
    BATCH_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(BATCH_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            existing_pid = int(BATCH_LOCK_PATH.read_text(encoding="utf-8").strip())
            os.kill(existing_pid, 0)
            raise RuntimeError(f"batch runner already active with pid {existing_pid}")
        except (OSError, ValueError):
            BATCH_LOCK_PATH.unlink(missing_ok=True)
            fd = os.open(BATCH_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    return fd


def transition_to_processing(product_dir: Path) -> Dict[str, Any]:
    status_path = product_dir / "status.json"
    status = load_json(status_path)
    if status.get("status") != "QUEUED":
        return status
    status.update({
        "status": "PROCESSING",
        "current_step": "validate_source",
        "progress": max(int(status.get("progress") or 0), 2),
        "started_at": status.get("started_at") if status.get("started_at") not in {None, "unknown"} else now(),
        "last_run_at": now(),
        "next_action": status.get("next_action") or "validate_source",
    })
    status.setdefault("history", []).append({
        "from": "QUEUED", "to": "PROCESSING", "at": now(),
        "reason": "Batch worker started the product pipeline.",
    })
    write_json_atomic(status_path, status)
    return status


def codex_command(settings: Dict[str, Any]) -> str:
    configured = str(settings.get("codex_command") or "")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("codex")
    if not found:
        raise RuntimeError("Codex executable is unavailable")
    return found


def codex_reasoning_effort(settings: Dict[str, Any], step: str) -> str:
    configured = settings.get("codex_reasoning_effort_by_step") or {}
    effort = str(configured.get(step) or configured.get("default") or "high").strip().lower()
    return effort if effort in {"minimal", "low", "medium", "high", "xhigh"} else "high"


def codex_exec_command(settings: Dict[str, Any], step: str, prompt: str) -> List[str]:
    """Build an unattended child command without unrelated MCP startup waits."""
    return [
        codex_command(settings), "exec", "-C", str(ROOT), "--skip-git-repo-check",
        "--ephemeral", "--disable", "chronicle",
        "-s", "danger-full-access", "-c", 'approval_policy="never"',
        "-c", "mcp_servers={}",
        "-c", f'model_reasoning_effort="{codex_reasoning_effort(settings, step)}"',
        prompt,
    ]


def load_shop_environment(settings: Dict[str, Any]) -> None:
    shop_name = str(settings.get("shop_name") or "zhonglian1")
    env_path = ROOT / "ozon-adapter" / f".env.{shop_name}"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def start_ozon_metadata_prewarm(settings: Dict[str, Any]) -> threading.Thread | None:
    """Warm the read-only category tree cache while product analysis runs."""
    if not settings.get("ozon_metadata_prewarm_enabled", True) or app_mode(settings) != "production":
        return None

    def worker() -> None:
        log_path = ROOT / "logs/ozon-metadata-prewarm.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = prewarm_category_tree(settings)
            message = json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"{now()} {message}\n")

    thread = threading.Thread(target=worker, name="ozon-metadata-prewarm", daemon=True)
    thread.start()
    return thread


def step_timeout(settings: Dict[str, Any], step: str) -> int:
    values = settings.get("timeouts_seconds") or {}
    aliases = {
        "product_analysis": "product_analysis", "category_match": "category_match",
        "image_generation": "image_generation", "image_qc": "image_qc",
        "ozon_upload": "ozon_api",
        "offer_exists_check": "ozon_api",
    }
    return int(values.get(aliases.get(step, step), 300))


def product_step_timeout(product_dir: Path, settings: Dict[str, Any], step: str) -> int:
    per_unit = step_timeout(settings, step)
    if step != "image_generation":
        return per_unit
    plan_path = product_dir / "output/image-plan.json"
    if not plan_path.is_file():
        return per_unit
    plan = load_json(plan_path)
    planned_paths = {
        str(item.get("output_path"))
        for key in ("main_images", "detail_images", "disclaimer_images")
        for item in (plan.get(key) or [])
        if item.get("output_path")
    }
    remaining = sum(1 for path in planned_paths if not (ROOT / path).is_file())
    regeneration_path = product_dir / "output/image-regeneration-request.json"
    if regeneration_path.is_file():
        remaining = max(
            remaining,
            len(load_json(regeneration_path).get("failed_slots") or []),
        )
    calculated = per_unit * max(1, remaining)
    return min(calculated, int(settings.get("image_generation_run_max_seconds", 1200)))


def completed_image_slot_count(product_dir: Path) -> int:
    plan_path = product_dir / "output/image-plan.json"
    if not plan_path.is_file():
        return 0
    plan = load_json(plan_path)
    completed = 0
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key) or []:
            output_value = str(item.get("output_path") or "")
            slot = str(item.get("slot") or "")
            if not output_value or not slot:
                continue
            output_path = ROOT / output_value
            manifest_path = product_dir / "output/product-lock" / f"{slot}.json"
            if not output_path.is_file() or not manifest_path.is_file():
                continue
            try:
                manifest = load_json(manifest_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if (manifest.get("audit") or {}).get("status") == "pass":
                completed += 1
    return completed


def app_mode(settings: Dict[str, Any]) -> str:
    mode = os.environ.get("APP_MODE", str(settings.get("app_mode") or "development")).strip().lower()
    if mode not in {"development", "production"}:
        raise RuntimeError("APP_MODE must be development or production")
    return mode


def run_checked(command: List[str], log_path: Path, timeout_seconds: int, product_dir: Path) -> None:
    with log_path.open("a", encoding="utf-8") as output:
        try:
            completed = run_registered_process(command, product_dir, output, timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Step timed out after {timeout_seconds}s: {' '.join(command[:3])}") from exc
    if completed.returncode != 0:
        try:
            lines = [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            detail = " | ".join(lines[-3:])
        except OSError:
            detail = ""
        suffix = f"; {detail}" if detail else ""
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(command[:3])}{suffix}"
        )


def require_files(product_dir: Path, relative_paths: List[str]) -> None:
    missing = [value for value in relative_paths if not (product_dir / value).is_file()]
    if missing:
        raise RuntimeError("Missing validated step outputs: " + ", ".join(missing))


def validate_source_step(product_dir: Path) -> None:
    source_path = product_dir / "input/source.json"
    require_files(product_dir, ["input/source.json", "input/raw-snapshot.json", "input/category-selection.json", "status.json"])
    source = load_json(source_path)
    selection = load_json(product_dir / "input/category-selection.json")
    schema = load_json(ROOT / "templates/source.schema.json")
    errors = sorted(Draft202012Validator(schema).iter_errors(source), key=lambda item: list(item.path))
    if errors:
        raise RuntimeError("source.json schema failed: " + "; ".join(error.message for error in errors[:10]))
    title = str(source.get("title_cn") or "").strip()
    if not title or title.lower() in {"unknown", "skuprops", "skuinfo", "skumap"}:
        raise RuntimeError(f"Invalid collected product title: {title or 'empty'}")
    if "1688.com/offer/" not in str(source.get("source_url") or ""):
        raise RuntimeError("source_url is not a real 1688 offer URL")
    skus = source.get("skus") or []
    if not 1 <= len(skus) <= MAX_SELECTED_SKUS_PER_PRODUCT:
        raise RuntimeError(f"Selected SKU count must be 1-{MAX_SELECTED_SKUS_PER_PRODUCT}")
    for sku in skus:
        if not str(sku.get("sku_id") or "").strip() or sku.get("purchase_price") is None:
            raise RuntimeError("Every selected SKU requires a real sku_id and purchase price")
    if (
        not isinstance(selection.get("category_id"), int)
        or not isinstance(selection.get("type_id"), int)
        or selection.get("allow_runtime_rematch") is not False
        or not (selection.get("rules_snapshot") or {}).get("attributes")
    ):
        raise RuntimeError("Collector final Ozon category and official rule snapshot are required")


def offer_ids(product_dir: Path) -> List[str]:
    return [f"{product_dir.name}-{sku['sku_id']}" for sku in load_json(product_dir / "input/source.json")["skus"]]


def offer_exists_check(product_dir: Path, settings: Dict[str, Any]) -> None:
    output = product_dir / "output"
    identifiers = offer_ids(product_dir)
    mode = app_mode(settings)
    if mode == "development":
        write_json_atomic(output / "offer-id-precheck.json", {
            "product_id": product_dir.name, "offer_ids": identifiers,
            "status": "development_skipped", "action": "create", "conflicts": [],
            "checked_at": now(),
        })
        return
    sys.path.insert(0, str(ROOT / "ozon-adapter"))
    sys.path.insert(0, str(ROOT / "ozon-uploader"))
    from ozon_adapter import OzonConfig
    from ozon_uploader import OzonWriteClient
    client = OzonWriteClient(OzonConfig.from_shop(str(settings.get("shop_name") or "zhonglian1"), ROOT / "ozon-adapter/shops.json"))
    response = client.get_products_info(identifiers)
    items = response.get("items") or response.get("result", {}).get("items") or []
    existing = {str(item.get("offer_id")) for item in items if item.get("offer_id")}
    if existing and len(existing) != len(identifiers):
        status, action = "blocked_mixed_conflict", "blocked"
    elif existing:
        status, action = "ok", "update"
    else:
        status, action = "ok", "create"
    write_json_atomic(output / "offer-id-precheck.json", {
        "product_id": product_dir.name, "offer_ids": identifiers, "status": status,
        "action": action, "existing_offer_ids": sorted(existing),
        "conflicts": [] if status == "ok" else ["mixed existing and new offer IDs"],
        "checked_at": now(),
    })
    if status != "ok":
        raise RuntimeError("Offer ID conflict cannot be safely resolved")


def upload_feasibility(product_dir: Path) -> None:
    output = product_dir / "output"
    source = load_json(product_dir / "input/source.json")
    category = load_json(output / "ozon-category.json")
    metadata = load_json(output / "ozon-category-attributes.json")
    mapped_attributes = load_json(output / "ozon-attributes.json")
    pricing = load_json(output / "pricing-result.json")
    cost = load_json(output / "cost-analysis.json")
    offers = load_json(output / "offer-id-precheck.json")
    tree_path = output / "ozon-category-tree.json"
    category_pair_in_tree = False
    if tree_path.is_file():
        tree = load_json(tree_path)
        category_pair_in_tree = any(
            item.get("category_id") == category.get("category_id")
            and item.get("type_id") == category.get("type_id")
            and item.get("disabled") is not True
            for item in tree.get("categories") or []
        )
    metadata_identity_matches = (
        metadata.get("category_id") == category.get("category_id")
        and metadata.get("type_id") == category.get("type_id")
        and mapped_attributes.get("category_id") == category.get("category_id")
        and mapped_attributes.get("type_id") == category.get("type_id")
    )
    required_ids = {
        int(item["attribute_id"])
        for item in metadata.get("attributes", [])
        if item.get("required") is True
    }
    mapped_by_id = {
        int(item["attribute_id"]): item
        for item in mapped_attributes.get("attributes", [])
    }
    metadata_by_id = {
        int(item["attribute_id"]): item
        for item in metadata.get("attributes", [])
    }
    deferred_role_names = {
        "бренд", "тип товара", "название модели", "модель",
        "название модели (для объединения в одну карточку)",
    }
    deferred_required = sorted(
        attribute_id
        for attribute_id in required_ids
        if str(metadata_by_id.get(attribute_id, {}).get("attribute_name") or "").strip().casefold()
        in deferred_role_names
    )
    missing_required = sorted(
        attribute_id
        for attribute_id in required_ids
        if attribute_id not in deferred_required and (
            attribute_id not in mapped_by_id
            or mapped_by_id[attribute_id].get("validation_status") != "valid"
        )
    )
    invalid_values = sorted({
        int(item["attribute_id"])
        for item in mapped_attributes.get("invalid_values", [])
    })
    product_weight = cost.get("product_weight") or {}
    package_weight = cost.get("package_weight") or cost.get("weight") or {}
    product_dimensions = cost.get("product_dimensions") or {}
    package_dimensions = cost.get("package_dimensions") or cost.get("dimensions") or {}
    measurement_hierarchy = (
        float(package_weight.get("value") or 0) > float(product_weight.get("value") or 0) > 0
        and all(
            float(package_dimensions.get(key) or 0) > float(product_dimensions.get(key) or 0) > 0
            for key in ("length", "width", "height")
        )
    )
    checks = {
        "category": isinstance(category.get("category_id"), int)
        and isinstance(category.get("type_id"), int)
        and category.get("metadata_source") == "ozon_seller_api"
        and category.get("match_status") == "api_confirmed"
        and float(category.get("confidence") or 0) >= 0.90,
        "category_type_pair": category_pair_in_tree,
        "attribute_schema_identity": metadata_identity_matches,
        "required_attributes": not missing_required,
        "attribute_values": not invalid_values,
        "sku_structure": 1 <= len(source.get("skus") or []) <= MAX_SELECTED_SKUS_PER_PRODUCT,
        "pricing": all(float(item.get("selling_price_cny") or 0) > 0 for item in pricing.get("sku_pricing", [])),
        "measurement_hierarchy": measurement_hierarchy,
        "image": bool(source.get("main_images") or any(sku.get("local_image_path") not in {None, "unknown"} for sku in source.get("skus") or [])),
        "offer_conflict": offers.get("status") in {"ok", "development_skipped"},
    }
    value = {
        "product_id": product_dir.name, "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "required_attribute_ids": sorted(required_ids),
        "deferred_required_attribute_ids": deferred_required,
        "missing_required_attribute_ids": missing_required,
        "invalid_attribute_ids": invalid_values,
        "category_id": category.get("category_id"), "type_id": category.get("type_id"),
        "checked_at": now(),
    }
    write_json_atomic(output / "upload-feasibility.json", value)
    if value["status"] != "PASS":
        raise RuntimeError("Upload feasibility failed: " + json.dumps(value, ensure_ascii=False))


def run_local_step(product_dir: Path, step: str, settings: Dict[str, Any], log_path: Path) -> bool:
    product_root = product_dir.parent.parent if product_dir.parent.name == "products" else ROOT
    if deletion_requested(product_root, product_dir.name) or not product_dir.is_dir():
        raise ProductDeletionRequested(product_dir.name)
    python = sys.executable
    timeout = step_timeout(settings, step)
    if step == "validate_source":
        validate_source_step(product_dir)
    elif step == "product_positioning":
        run_checked([python, "scripts/product_positioning_agent.py", str(product_dir), "--write"], log_path, timeout, product_dir)
        require_files(product_dir, ["output/product-positioning.json"])
    elif step == "category_match":
        run_checked([python, "scripts/ozon_metadata_matcher.py", str(product_dir), "--write"], log_path, timeout, product_dir)
        run_checked([
            python, "ozon-adapter/cli.py", str(product_dir), "--fetch", "--shop",
            str(settings.get("shop_name") or "zhonglian1"),
        ], log_path, timeout, product_dir)
        require_files(product_dir, ["output/ozon-category.json", "output/ozon-category-attributes.json"])
        category = load_json(product_dir / "output/ozon-category.json")
        category_id = category.get("category_id")
        type_id = category.get("type_id")
        if (
            category.get("metadata_source") != "ozon_seller_api"
            or not isinstance(category_id, int)
            or not isinstance(type_id, int)
            or category_id <= 0
            or type_id <= 0
            or category.get("match_status") not in {"api_confirmed", "api_match_needs_review"}
        ):
            raise RuntimeError(
                "类目匹配未确认真实 category_id/type_id；近义类目搜索也未得到可用结果，"
                "禁止进入后续属性、定价、文案或生图步骤。"
            )
    elif step == "variant_rules":
        run_checked([python, "variant-compatibility-checker/cli.py", str(product_dir)], log_path, timeout, product_dir)
        require_files(product_dir, ["output/platform-grouping-result.json"])
    elif step == "style_selector":
        run_checked([python, "scripts/style_selector.py", str(product_dir)], log_path, timeout, product_dir)
        require_files(product_dir, ["output/style-profile.json"])
    elif step == "image_plan":
        style_path = product_dir / "output/style-profile.json"
        if not style_path.is_file() or load_json(style_path).get("classification_status") != "selected":
            run_checked([python, "scripts/style_selector.py", str(product_dir)], log_path, timeout, product_dir)
        if load_json(style_path).get("classification_status") != "selected":
            raise RuntimeError("已锁定类目仍无法选择图片风格，禁止生成无依据图片规划")
        run_checked([python, "scripts/image_source_preflight.py", str(product_dir)], log_path, timeout, product_dir)
        require_files(product_dir, ["output/image-source-preflight.json"])
        run_checked([python, "scripts/image_planner.py", str(product_dir), "--write"], log_path, timeout, product_dir)
        require_files(product_dir, ["output/image-plan.json"])
    elif step == "image_qc":
        if not (product_dir / "output/image-qc-report.json").is_file():
            run_checked([python, "scripts/image_qc.py", str(product_dir), "--hard-gate", "--write"], log_path, timeout, product_dir)
        run_checked([python, "scripts/image_qc.py", str(product_dir), "--verify-report"], log_path, timeout, product_dir)
        report = load_json(product_dir / "output/image-qc-report.json")
        if report.get("decision") != "pass" or report.get("critical_failures"):
            slots = sorted({slot for issue in report.get("issues", []) for slot in issue.get("image_slots", [])})
            if not slots:
                slots = [item["slot"] for item in report.get("images_checked", [])]
            request_single_image_regeneration(product_dir, slots, "Image QC failed; only listed slots require regeneration.")
            raise ImageRegenerationRequested("Image QC requested a single-slot regeneration retry")
        (product_dir / "output/image-regeneration-request.json").unlink(missing_ok=True)
    elif step == "measurements":
        run_checked([python, "pricing-engine/cli.py", str(product_dir), "--write"], log_path, timeout, product_dir)
        require_files(product_dir, ["output/cost-analysis.json", "output/pricing-result.json", "output/profit-analysis.json"])
        pricing = load_json(product_dir / "output/pricing-result.json")
        if any(item.get("selling_price_rub") is None for item in pricing.get("sku_pricing", [])):
            raise RuntimeError("At least one selected SKU has no calculated RUB selling price")
    elif step == "offer_exists_check":
        offer_exists_check(product_dir, settings)
    elif step == "upload_feasibility":
        upload_feasibility(product_dir)
    elif step == "marketplace_content":
        content_input = product_dir / "output/marketplace-content-input.json"
        require_files(product_dir, ["output/marketplace-content-input.json"])
        run_checked([
            python, "scripts/marketplace_content_generator.py", str(product_dir),
            "--content-input", str(content_input), "--write",
        ], log_path, timeout, product_dir)
        require_files(product_dir, ["output/title-ru.json", "output/description-ru.json", "output/keywords-ru.json", "output/ozon-draft.json"])
    elif step == "field_completion":
        run_checked([python, "ozon-field-completion/cli.py", product_dir.name], log_path, timeout, product_dir)
        require_files(product_dir, ["output/ozon-tags.json", "output/rich-content.json", "output/final-upload-check.json"])
        tags = load_json(product_dir / "output/ozon-tags.json")
        if tags.get("count") != 30 or len(tags.get("tags") or []) != 30:
            raise RuntimeError("Ozon tags must contain exactly 30 entries")
        check = load_json(product_dir / "output/final-upload-check.json")
        if check.get("status") != "PASS" or check.get("upload_allowed") is not True:
            raise RuntimeError("Final upload check did not pass: " + "; ".join(check.get("errors") or []))
    elif step == "ozon_upload":
        if app_mode(settings) != "production":
            raise RuntimeError("APP_MODE=development prohibits real Ozon batch uploads")
        status = load_json(product_dir / "status.json")
        if int(status.get("api_write_count") or 0) > 0 and (product_dir / "output/ozon-write-receipt.json").is_file():
            # The write was accepted earlier. Move to read-only task polling instead of resubmitting.
            from pipeline_runtime import complete_step
            complete_step(product_dir, step)
            return True
        retry_stores = [str(value) for value in status.get("target_store_ids_for_run") or []]
        command = [python, "scripts/multi_store_upload.py", str(product_dir), "--execute"]
        for store_id in retry_stores:
            command.extend(["--only-store", store_id])
        run_checked(command, log_path, timeout, product_dir)
        publications = load_json(product_dir / "output/store-publications.json")
        if not any(item.get("selected") for item in (publications.get("stores") or {}).values()):
            raise RuntimeError("No selected Ozon store publication was found")
    else:
        return False
    if product_deleted(product_dir):
        raise ProductDeletionRequested(product_dir.name)
    from pipeline_runtime import complete_step
    complete_step(product_dir, step)
    return True


def product_analysis_output_is_complete(product_dir: Path) -> bool:
    """Treat a valid analysis artifact as success when Codex timed out during final narration."""
    output_path = product_dir / "output/product-analysis.json"
    schema_path = ROOT / "templates/product-analysis.schema.json"
    if not output_path.is_file() or not schema_path.is_file():
        return False
    try:
        analysis = load_json(output_path)
        schema = load_json(schema_path)
        errors = list(Draft202012Validator(schema).iter_errors(analysis))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return (
        not errors
        and analysis.get("product_id") == product_dir.name
        and (analysis.get("processing") or {}).get("step") == "product_analysis"
        and (analysis.get("processing") or {}).get("status") == "completed"
    )


def russian_copy_output_is_complete(product_dir: Path) -> bool:
    """Accept the Russian-copy step as soon as all three validated artifacts exist."""
    output_dir = product_dir / "output"
    copy_path = output_dir / "copy-ru.json"
    content_path = output_dir / "marketplace-content-input.json"
    keyword_path = output_dir / "keyword-research-ru.json"
    required_paths = [copy_path, content_path, keyword_path]
    if not all(path.is_file() for path in required_paths):
        return False
    try:
        copy_value = load_json(copy_path)
        content_value = load_json(content_path)
        keyword_value = load_json(keyword_path)
        copy_schema = load_json(ROOT / "templates/copy-ru.schema.json")
        keyword_schema = load_json(ROOT / "templates/keyword-research-ru.schema.json")
        content_rules = load_json(ROOT / "rules/marketplace_content_rules.json")
        from marketplace_content_generator import validate_content_input

        copy_errors = list(Draft202012Validator(copy_schema).iter_errors(copy_value))
        keyword_errors = list(Draft202012Validator(keyword_schema).iter_errors(keyword_value))
        validate_content_input(content_value, product_dir.name, content_rules)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False
    return (
        not copy_errors
        and not keyword_errors
        and copy_value.get("product_id") == product_dir.name
        and keyword_value.get("product_id") == product_dir.name
        and (copy_value.get("processing") or {}).get("step") == "russian_copy"
        and (copy_value.get("processing") or {}).get("status") == "completed"
    )


def complete_embedded_image_qc(
    product_dir: Path,
    settings: Dict[str, Any],
    log_path: Path,
) -> bool:
    """Verify a QC report produced by the image-generation Codex invocation."""
    if not settings.get("merge_image_generation_and_qc", True):
        return False
    if not (product_dir / "output/image-qc-report.json").is_file():
        return False
    if not run_local_step(product_dir, "image_qc", settings, log_path):
        return False
    (product_dir / "output/image-regeneration-request.json").unlink(missing_ok=True)
    complete_step(product_dir, "image_qc")
    cache_store(product_dir, "image_qc", input_hash(product_dir, "image_qc"))
    return True


def run_one_step(product_dir: Path, settings: Dict[str, Any]) -> Dict[str, Any]:
    if product_deleted(product_dir):
        return {"product_id": product_dir.name, "outcome": "deleted"}
    lock_path = product_dir / ".pipeline.lock"
    if lock_path.is_file():
        try:
            owner_pid = int(lock_path.read_text(encoding="utf-8").strip())
            os.kill(owner_pid, 0)
        except (OSError, TypeError, ValueError):
            lock_path.unlink(missing_ok=True)
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode())
        os.close(lock_fd)
    except FileExistsError:
        return {"product_id": product_dir.name, "outcome": "locked"}
    try:
        status = transition_to_processing(product_dir)
        if status.get("status") in TERMINAL_STATES:
            return {"product_id": product_dir.name, "outcome": "terminal"}
        step = status.get("next_action") or next(
            (item for item in PIPELINE_STEPS if item not in status.get("completed_steps", [])),
            "complete",
        )
        if step != "complete":
            status["current_step"] = step
            status["progress"] = max(int(status.get("progress") or 0), STEP_START_PROGRESS.get(step, 2))
            status["active_step"] = {"name": step, "started_at": now()}
            status["last_run_at"] = now()
            write_json_atomic(product_dir / "status.json", status)
        cache_key = input_hash(product_dir, step)
        local_cache_hit = cache_hit(product_dir, step, cache_key)
        shared_cache_key = shared_analysis_input_hash(product_dir) if step == "product_analysis" else None
        shared_cache_hit = bool(
            step == "product_analysis"
            and not local_cache_hit
            and shared_cache_key
            and shared_analysis_cache_restore(product_dir, shared_cache_key)
        )
        started = performance_start(product_dir, step, local_cache_hit or shared_cache_hit)
        if local_cache_hit or shared_cache_hit:
            if product_deleted(product_dir):
                raise ProductDeletionRequested(product_dir.name)
            from pipeline_runtime import complete_step
            complete_step(product_dir, step)
            if shared_cache_hit:
                cache_store(product_dir, step, cache_key)
            performance_finish(product_dir, step, started, True, "cache_hit")
            return {
                "product_id": product_dir.name,
                "outcome": "shared_image_recognition_cache_hit" if shared_cache_hit else "cache_hit",
                "step": step,
            }
        before_completed = list(status.get("completed_steps") or [])
        before_api_writes = int(status.get("api_write_count") or 0)
        before_image_slots = completed_image_slot_count(product_dir) if step == "image_generation" else 0
        artifact_paths = {
            "product_analysis": [product_dir / "output/product-analysis.json"],
            "russian_copy": [
                product_dir / "output/copy-ru.json",
                product_dir / "output/marketplace-content-input.json",
                product_dir / "output/keyword-research-ru.json",
            ],
        }.get(step, [])
        artifact_signatures_before = {
            path: file_signature(path) for path in artifact_paths
        }

        def new_step_artifacts_are_complete() -> bool:
            if not artifact_paths or not any(
                file_signature(path) != artifact_signatures_before[path]
                for path in artifact_paths
            ):
                return False
            if step == "product_analysis":
                return product_analysis_output_is_complete(product_dir)
            if step == "russian_copy":
                return russian_copy_output_is_complete(product_dir)
            return False
        image_slot_concurrency = max(1, min(int(settings.get("image_slot_concurrency", 3)), 4))
        fast_analysis_instruction = (
            "product_analysis使用快速提取模式：只读取当前商品目录中与后续流程直接相关的有效事实，"
            "只生成output/product-analysis.json，不生成俄文文案、类目、图片、Rich Content或长篇解释；"
            "必须保留product_type、category、facts、selling_points、unknowns、risks和recommendation，"
            "每个列表最多保留8项，卖点每项一句话，风险每项一句话；未知写unknown并说明原因；"
            "完成JSON写入和校验后立即结束，不要继续阅读无关文件，不调用搜索Skill，不调用生图Skill。"
            "不要输出分析过程、解释、计划、复盘或Markdown；最终只输出一行：DONE product_analysis。"
            if step == "product_analysis" else ""
        )
        fast_copy_instruction = (
            "russian_copy使用截图式快速生成模式：只读取已经完成的product-analysis.json、product-positioning.json、"
            "ozon-category.json、pricing-result.json和source.json；不要重新分析全部图片，不要重新执行商品理解；"
            "一次性生成标题、短标题、描述、卖点、关键词文件、marketplace-content-input.json和30个俄文标签；"
            "关键词只使用商品事实、类目词和已有本地缓存，不单独调用搜索Skill；每个标签单独保存且不超过30个字符；"
            "不要输出分析过程、解释、计划或Markdown，文件全部校验成功后最终只输出一行：DONE russian_copy。"
            if step == "russian_copy" else ""
        )
        prompt = (
            f"调用$full-product-pipeline，product_id={product_dir.name}。"
            f"本轮只完成status.json中的next_action步骤：{step}，完成后立即校验输出并更新断点，不执行后续步骤。"
            "需求已经完全确定：事实只读取当前商品目录；未知可选字段写unknown；不得向用户提问。"
            + fast_analysis_instruction
            + "product_analysis必须逐项读取1688标题、结构化属性、SKU文字和详情图中的明确文字；例如标题明确写硅胶时材质不能仍为unknown。"
            "商品重量尺寸和包装重量尺寸缺失时允许由measurement模块估算并保存estimated与confidence；包装重量及长宽高必须分别严格大于商品本体值，运费只使用包装值。"
            "材质、认证、功能、承重和配件不得估算。尺寸图必须使用measurement模块的商品本体尺寸；"
            "商品尺寸为估算值时必须在图中标注俄文Примерные размеры（约），禁止把包装尺寸画成商品尺寸。"
            "工作室默认规则：无可靠品牌时填Нет бренда；1688来源商品原产国填中国；未提供包装数量时每件商品数量、计量单位数量和原厂包装数量均填1。"
            "所有Ozon属性ID必须从当前category_id/type_id实时元数据按字段含义动态取得，不得复制其他类目或100分样板商品的属性ID。"
            + fast_copy_instruction
            + "product_analysis必须生成product-analysis.json；非快速模式下russian_copy才允许以只读建议模式调用$keyword-research，市场限定Ozon俄罗斯，"
            "不得提问、不得写全局Skill记忆、不得虚构搜索量或难度；只使用商品事实、实时类目和可验证Ozon公开搜索词，"
            "输出output/keyword-research-ru.json，并据此生成俄文文案以及output/marketplace-content-input.json；"
            "image_generation必须调用$image-generator，但不得调用外部品牌、营销、摄影或生图Skill。"
            "生成前只运行一次image_source_preflight.py；SKU原图短边不足600px时先尝试恢复1688原尺寸，仍不足则停止，严禁放大、像素复制或强行抠图。"
            "尺寸、颜色和SKU对比图只用真实原图确定性裁切排版，禁止AI重画；主图和生活场景图可使用内置参考图编辑，但必须保持真实结构、颜色、比例和配件。"
            f"image_generation先运行scripts/image_slot_scheduler.py并按每波最多{image_slot_concurrency}张处理；"
            "每张图片保存后只检查错商品、错SKU/颜色、额外配件功能、明显变形、中文乱码和不可读俄文；"
            "失败时仅当场重做失败单图一次；不做整套美学评分，不等待全套生成后才显示。"
            "最终图不得包含中文、1688水印、供应商装饰文字、错误俄文或变形商品；失败图不得显示为成品或进入上传。"
            "不得人工确认；不得提交stock、warehouse_id或调用任何库存接口。"
            "已成功的Ozon写入绝对不得重复。若本步骤出现可重试错误，保存具体原因后退出本轮。"
        )
        if step == "product_analysis":
            prompt = (
                f"调用$full-product-pipeline，product_id={product_dir.name}。"
                "本轮只完成product_analysis并生成output/product-analysis.json，校验后立即结束。"
                "只读取input/source.json中的标题、有效结构化商品属性、所选SKU文字，以及确有必要的主图/SKU图；"
                "如果存在input/manual-confirmation.json，必须同时读取；其中尺寸、重量、材质和人民币进价是用户在本批次唯一一次确认后保存的补充信息，"
                "应标记为estimated_human_approved并优先于普通AI推测，但不得改写input/source.json；"
                "没有详情图时直接记录unknown，不扫描无关文件。"
                "只读取input/category-selection.json顶层的category_id、type_id、中文/俄文类目名和路径；"
                "禁止展开读取rules_snapshot、属性字典和allowed_values，因为类目已经由用户锁定。"
                "不得运行类目匹配、测量、俄文文案、关键词、定价、生图、图片质检、Rich Content或上传。"
                "明确来源事实必须保留引用；未知材质、认证、承重、功能和配件写unknown，不得猜测。"
                "品牌无可靠来源时按项目规则写Нет бренда，1688来源国写中国，未提供包装数量时写1。"
                "输出必须符合templates/product-analysis.schema.json；列表每项一句、每类最多8项。"
                "完成JSON原子写入后运行断点校验，不输出分析过程；最终只输出DONE product_analysis。"
                "不得提交stock、warehouse_id，不调用库存或任何Ozon写接口。"
            )
        elif step == "russian_copy":
            prompt = (
                f"调用$full-product-pipeline，product_id={product_dir.name}。"
                "本轮只完成russian_copy；只读取已完成的product-analysis.json、product-positioning.json、"
                "ozon-category.json、pricing-result.json和source.json中的所选SKU。"
                "不要重新读图片、重新分析商品、重新匹配类目或运行生图。"
                "一次性生成并校验标题、短标题、描述、卖点、关键词、marketplace-content-input.json和30个俄文标签；"
                "关键词仅使用可追溯商品事实、已锁定类目词和本地缓存，不调用外部搜索。"
                "完成文件和断点后立即结束，最终只输出DONE russian_copy。"
                "不得提交stock、warehouse_id，不调用库存或任何Ozon写接口。"
            )
        elif step == "image_generation":
            prompt = (
                f"调用$full-product-pipeline和$image-generator，product_id={product_dir.name}。"
                "这是用户点击运行任务后已授权的无人值守批次；禁止向用户提问、请求确认或等待回复，直接执行并保存结果。"
                "本轮只完成image_generation；只读取image-plan.json、style-profile.json、product-analysis.json、"
                "copy-ru.json、platform-grouping-result.json和计划中列出的真实参考图。"
                "不要重跑商品分析、类目、属性、定价或文案。"
                "先读取并确认现有image-source-preflight.json已通过；报告通过且参考图哈希未变化时直接复用，不重复运行检查。低清SKU缩略图禁止放大、像素复制或自动抠图。"
                f"再运行image_slot_scheduler.py，每波最多{image_slot_concurrency}张；"
                "每个所选SKU必须有一张独立真实关联主图，并且所有SKU主图必须先生成；随后按当前商品专属方案生成6至8张共享详情图。"
                "禁止普通白底图，禁止固定类目模板，禁止只更换背景却重复相同构图；每张图必须回答不同购买问题。"
                "严格按image-plan.json的operation分流：compose_from_real_images只做真实原图确定性排版；edit_real_image使用真实参考图进行AI场景编辑并保持商品身份、颜色、结构、比例和配件。"
                "调用内置图片工具前，必须把计划中的所有products/...参考路径转换为以项目根目录开头的绝对路径；禁止把相对路径传给图片工具。"
                "每次图片工具调用最多传5张参考图；SKU主图只传自己的SKU图，共享图优先SKU原图再选清晰主图。"
                "AI生成与商品专属构图一致的无文字视觉和有意留出的排版空间；随后用scripts/image_text_overlay.py --kind main或detail写入准确俄文。"
                "文字必须融入画面，禁止固定黑色文字框；主图只用一条大字卖点，详情图允许更多信息。"
                "不得调用$ecommerce-branding或其他外部Skill，不得把空背景、残缺抠图或像素块保存为商品图。"
                "每张保存后只做硬错误快检并增量更新output/image-hard-gate.json；仅失败单图允许当场重做一次，不做整套评分。"
                "全部计划槽位检查完成后运行scripts/image_qc.py --hard-gate --write生成兼容报告。目标整套在5分钟内完成。"
                "完成文件和断点后立即结束，最终只输出DONE image_generation。"
                "不得提交stock、warehouse_id，不调用库存或任何Ozon写接口。"
            )
        log_path = product_dir / "logs/full-pipeline.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if run_local_step(product_dir, step, settings, log_path):
            if product_deleted(product_dir):
                return {"product_id": product_dir.name, "outcome": "deleted", "step": step}
            cache_store(product_dir, step, cache_key)
            performance_finish(product_dir, step, started, False, "completed")
            return {"product_id": product_dir.name, "outcome": "completed", "step": step}
        env = dict(os.environ, UPLOAD_MODE="production" if app_mode(settings) == "production" else "dry-run")
        with log_path.open("a", encoding="utf-8") as output:
            try:
                with _codex_semaphore:
                    completed = run_registered_process(
                        codex_exec_command(settings, step, prompt),
                        product_dir, output, product_step_timeout(product_dir, settings, step), env,
                        completion_check=(
                            new_step_artifacts_are_complete
                            if step in {"product_analysis", "russian_copy"}
                            else None
                        ),
                        completion_poll_seconds=float(settings.get("artifact_poll_interval_seconds", 0.5)),
                    )
            except subprocess.TimeoutExpired as exc:
                if new_step_artifacts_are_complete():
                    from pipeline_runtime import complete_step
                    complete_step(product_dir, step)
                    cache_store(product_dir, step, cache_key)
                    performance_finish(product_dir, step, started, False, "completed_after_timeout")
                    with log_path.open("a", encoding="utf-8") as output:
                        output.write(f"\n[recovery] valid {step} artifacts detected; timeout treated as completed step.\n")
                    return {
                        "product_id": product_dir.name,
                        "outcome": "completed_after_timeout",
                        "step": step,
                    }
                after_image_slots = completed_image_slot_count(product_dir) if step == "image_generation" else 0
                if step == "image_generation" and after_image_slots > before_image_slots:
                    partial = load_json(product_dir / "status.json")
                    partial.setdefault("warnings", []).append(
                        f"Image generation saved {after_image_slots - before_image_slots} new locked slots before the execution window ended; remaining slots will continue automatically."
                    )
                    partial["last_run_at"] = now()
                    partial["next_action"] = "image_generation"
                    write_json_atomic(product_dir / "status.json", partial)
                    performance_finish(product_dir, step, started, False, "partial_checkpoint")
                    return {
                        "product_id": product_dir.name,
                        "outcome": "partial_checkpoint",
                        "step": step,
                        "completed_image_slots": after_image_slots,
                    }
                raise RuntimeError(
                    f"Step {step} timed out after {product_step_timeout(product_dir, settings, step)}s"
                ) from exc
        if new_step_artifacts_are_complete():
            from pipeline_runtime import complete_step
            complete_step(product_dir, step)
            if shared_cache_key:
                shared_analysis_cache_store(product_dir, shared_cache_key)
            cache_store(product_dir, step, cache_key)
            performance_finish(product_dir, step, started, False, "completed_from_artifact")
            with log_path.open("a", encoding="utf-8") as output:
                output.write(f"\n[fast-path] valid {step} artifacts detected; child process stopped and checkpoint advanced.\n")
            return {
                "product_id": product_dir.name,
                "outcome": "completed_from_artifact",
                "step": step,
            }
        if product_deleted(product_dir):
            return {"product_id": product_dir.name, "outcome": "deleted", "step": step}
        after = load_json(product_dir / "status.json")
        after_api_writes = int(after.get("api_write_count") or 0)
        if after_api_writes > before_api_writes:
            if step not in (after.get("completed_steps") or []):
                from pipeline_runtime import complete_step
                complete_step(product_dir, step)
            cache_store(product_dir, step, cache_key)
            performance_finish(product_dir, step, started, False, "api_submitted", network_wait=time.monotonic() - started)
            return {"product_id": product_dir.name, "outcome": "api_submitted", "step": step}
        made_progress = (
            after.get("status") in TERMINAL_STATES
            or list(after.get("completed_steps") or []) != before_completed
            or after_api_writes != before_api_writes
            or after.get("next_action") != step
        )
        if completed.returncode == 0 and made_progress:
            merged_qc = False
            if (
                step == "image_generation"
                and step in (after.get("completed_steps") or [])
            ):
                merged_qc = complete_embedded_image_qc(product_dir, settings, log_path)
            if step == "product_analysis" and shared_cache_key:
                shared_analysis_cache_store(product_dir, shared_cache_key)
            cache_store(product_dir, step, cache_key)
            performance_finish(
                product_dir, step, started, False,
                "completed_with_image_qc" if merged_qc else "completed",
            )
            return {
                "product_id": product_dir.name,
                "outcome": "completed_with_image_qc" if merged_qc else "completed",
                "step": step,
            }
        retries = after.setdefault("retry_count_by_step", {})
        retries[step] = int(retries.get(step) or 0) + 1
        after["last_run_at"] = now()
        after.setdefault("warnings", []).append(
            f"Step {step} made no verified progress (exit={completed.returncode}); retry {retries[step]}."
        )
        write_json_atomic(product_dir / "status.json", after)
        if retries[step] > int(settings.get("step_retry_limit", 1)):
            mark_hard_failure(
                product_dir,
                step,
                f"Step {step} failed after one automatic retry; see logs/full-pipeline.log.",
            )
            performance_finish(product_dir, step, started, False, "failed", retries[step])
            return {"product_id": product_dir.name, "outcome": "failed", "step": step}
        performance_finish(product_dir, step, started, False, "retry", retries[step])
        return {"product_id": product_dir.name, "outcome": "retry", "step": step}
    except BatchSafeStopRequested as exc:
        if "started" in locals() and "step" in locals():
            performance_finish(product_dir, step, started, False, "safe_stop")
        status = load_json(product_dir / "status.json")
        status["active_step"] = None
        status["last_run_at"] = now()
        status.setdefault("warnings", []).append(
            "安全停止已中断当前子任务；此前逐张保存的有效图片断点已保留。"
        )
        write_json_atomic(product_dir / "status.json", status)
        return {
            "product_id": product_dir.name,
            "outcome": "safe_stop",
            "step": locals().get("step", "unknown"),
            "error": str(exc),
        }
    except ImageRegenerationRequested as exc:
        performance_finish(product_dir, step, locals().get("started", time.monotonic()), False, "single_slot_retry", 1)
        return {"product_id": product_dir.name, "outcome": "retry", "step": "image_generation", "error": str(exc)}
    except ProductDeletionRequested:
        return {"product_id": product_dir.name, "outcome": "deleted", "step": locals().get("step", "unknown")}
    except Exception as exc:
        if product_deleted(product_dir):
            return {"product_id": product_dir.name, "outcome": "deleted", "step": locals().get("step", "unknown")}
        status = load_json(product_dir / "status.json")
        step = status.get("next_action") or "validate_source"
        retries = status.setdefault("retry_count_by_step", {})
        retries[step] = int(retries.get(step) or 0) + 1
        write_json_atomic(product_dir / "status.json", status)
        if retries[step] > int(settings.get("step_retry_limit", 1)):
            mark_hard_failure(product_dir, step, f"{type(exc).__name__}: {exc}")
        performance_finish(product_dir, step, locals().get("started", time.monotonic()), False, "error", retries[step])
        return {"product_id": product_dir.name, "outcome": "error", "step": step, "error": str(exc)}
    finally:
        lock_path.unlink(missing_ok=True)


def step_group(status: Dict[str, Any]) -> str:
    step = status.get("next_action") or "validate_source"
    if step in IMAGE_GENERATION_STEPS:
        return "image_generation"
    if step in IMAGE_QC_STEPS:
        return "image_qc"
    if step in OZON_STEPS:
        return "ozon"
    if step == "category_match":
        return "category"
    if step == "measurements":
        return "pricing"
    if step in {"russian_copy", "product_positioning", "style_selector", "image_plan", "marketplace_content", "field_completion"}:
        return "copy"
    return "analysis"


def sync_batch(root: Path, batch: Dict[str, Any]) -> Dict[str, Any]:
    products = []
    for entry in batch["products"]:
        product_dir = root / "products" / entry["product_id"]
        if deletion_requested(root, entry["product_id"]) or not product_dir.is_dir():
            continue
        status = load_json(product_dir / "status.json")
        products.append({
            "product_id": entry["product_id"],
            "selected_sku_count": selected_sku_count(product_dir),
            "status": status.get("status", "unknown"),
            "current_step": status.get("current_step", "none"),
            "started_at": status.get("started_at", "unknown"),
            "completed_at": status.get("completed_at", "unknown"),
            "warnings": status.get("warnings", []),
            "errors": ([status.get("error_message")] if status.get("error_message") not in {None, "unknown"} else []),
        })
    batch["products"] = products
    batch["processing_count"] = sum(
        item["status"] not in TERMINAL_STATES | {"QUEUED", "COLLECTED"}
        for item in products
    )
    batch["success_count"] = sum(item["status"] in SUCCESS_STATES for item in products)
    batch["failed_count"] = sum(item["status"] == "FAILED_HARD_BLOCKER" for item in products)
    progress_values = []
    for item in products:
        product_status = load_json(root / "products" / item["product_id"] / "status.json")
        progress_values.append(int(product_status.get("progress") or 0))
    batch["progress"] = round(sum(progress_values) / len(progress_values)) if progress_values else 100
    with _batch_write_lock:
        write_json_atomic(batch_path(root, batch["batch_id"]), batch)
    return batch


def finalize_batch(root: Path, batch: Dict[str, Any]) -> Dict[str, Any]:
    rows = [
        result_row(root / "products" / entry["product_id"])
        for entry in batch["products"]
        if not deletion_requested(root, entry["product_id"])
        and (root / "products" / entry["product_id"]).is_dir()
    ]
    failed = sum(row["status"] == "FAILED_HARD_BLOCKER" for row in rows)
    succeeded = sum(row["status"] in SUCCESS_STATES for row in rows)
    if not rows:
        batch_status = "COMPLETED"
    elif failed and succeeded:
        batch_status = "COMPLETED_WITH_ERRORS"
    elif failed:
        batch_status = "COMPLETED_WITH_ERRORS"
    else:
        batch_status = "COMPLETED"
    report = {
        "schema_version": "1.0.0",
        "batch_id": batch["batch_id"],
        "status": batch_status,
        "started_at": batch["started_at"],
        "completed_at": now(),
        "product_count": len(rows),
        "sku_count": sum(row["selected_sku_count"] for row in rows),
        "success_count": succeeded,
        "failed_count": failed,
        "submitted_count": sum(row["api_write_count"] > 0 for row in rows),
        "pending_remote_count": sum(row["status"] == "PENDING_REMOTE" for row in rows),
        "uploaded_count": sum(row["status"] in {"UPLOADED", "ACTIVE"} for row in rows),
        "moderation_count": sum(row["status"] == "OZON_MODERATION" for row in rows),
        "create_count": sum(str(row["upload_action"]).lower() in {"create", "created"} for row in rows),
        "update_count": sum(str(row["upload_action"]).lower() in {"update", "updated"} for row in rows),
        "api_write_count": sum(row["api_write_count"] for row in rows),
        "inventory_api_called": False,
        "performance": batch_performance(rows, root, batch["batch_id"]),
        "products": rows,
    }
    batch.update({
        "status": batch_status,
        "completed_at": report["completed_at"],
        "processing_count": 0,
        "success_count": succeeded,
        "failed_count": failed,
        "progress": 100,
    })
    write_json_atomic(batch_path(root, batch["batch_id"]), batch)
    write_json_atomic(batch_result_path(root, batch["batch_id"]), report)
    write_json_atomic(root / "batch-result.json", report)
    return report


def recover_remote_pending_queue(root: Path, batch: Dict[str, Any], settings: Dict[str, Any]) -> None:
    """Perform one read-only final pass for imports that did not finish in this batch."""
    if app_mode(settings) != "production":
        return
    pending = [
        root / "products" / entry["product_id"]
        for entry in batch["products"]
        if not deletion_requested(root, entry["product_id"])
        and (root / "products" / entry["product_id"] / "status.json").is_file()
        and load_json(root / "products" / entry["product_id"] / "status.json").get("status") == "PENDING_REMOTE"
    ]
    if not pending:
        return
    sys.path.insert(0, str(ROOT / "ozon-adapter"))
    sys.path.insert(0, str(ROOT / "ozon-uploader"))
    from ozon_adapter import OzonConfig
    from ozon_uploader import OzonWriteClient, recover_remote_import
    shop = str(settings.get("shop_name") or "zhonglian1")
    client = OzonWriteClient(OzonConfig.from_shop(shop, ROOT / "ozon-adapter/shops.json"))
    for product_dir in pending:
        try:
            recover_remote_import(product_dir, client, timeout_seconds=1, poll_interval_seconds=30)
        except Exception as exc:
            status = load_json(product_dir / "status.json")
            status.setdefault("warnings", []).append(f"Read-only Ozon recovery query failed: {exc}")
            write_json_atomic(product_dir / "status.json", status)


def execute_batch(root: Path, batch: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    global _codex_semaphore
    _codex_semaphore = threading.BoundedSemaphore(max(1, int(settings.get("codex_concurrency", 2))))
    batch.update({"status": "RUNNING", "started_at": now(), "completed_at": "unknown"})
    write_json_atomic(batch_path(root, batch["batch_id"]), batch)
    prune_shared_analysis_cache(int(settings.get("image_recognition_cache_days", 10)))
    start_ozon_metadata_prewarm(settings)
    valid_product_dirs = []
    for entry in batch["products"]:
        product_dir = root / "products" / entry["product_id"]
        if deletion_requested(root, entry["product_id"]) or not product_dir.is_dir():
            continue
        count = selected_sku_count(product_dir)
        if not 1 <= count <= MAX_SELECTED_SKUS_PER_PRODUCT:
            mark_hard_failure(
                product_dir,
                "validate_source",
                f"Selected SKU count must be 1-{MAX_SELECTED_SKUS_PER_PRODUCT}, got {count}.",
            )
            continue
        try:
            validate_source_step(product_dir)
            queue_product(product_dir, batch["batch_id"])
            valid_product_dirs.append(product_dir)
        except Exception as exc:
            mark_hard_failure(product_dir, "validate_source", str(exc))
    sync_batch(root, batch)

    while True:
        if safe_stop_requested(batch["batch_id"]):
            return stop_batch_at_checkpoint(root, batch)
        active = []
        groups: Dict[str, List[Path]] = {
            "analysis": [], "category": [], "pricing": [], "copy": [],
            "image_generation": [], "image_qc": [], "ozon": [],
        }
        for product_dir in valid_product_dirs:
            if deletion_requested(root, product_dir.name) or not product_dir.is_dir():
                continue
            status = load_json(product_dir / "status.json")
            if status.get("status") == "OZON_READY" and not batch.get("auto_upload", False):
                continue
            if status.get("next_action") == "ozon_upload" and not batch.get("auto_upload", False):
                status.update({
                    "status": "OZON_READY", "current_step": "field_completion",
                    "progress": max(95, int(status.get("progress") or 0)),
                    "completed_at": now(), "next_action": "manual_ozon_upload",
                })
                status.setdefault("warnings", []).append("批次已完成加工，等待用户明确点击上传")
                write_json_atomic(product_dir / "status.json", status)
                continue
            if status.get("status") in TERMINAL_STATES:
                continue
            active.append(product_dir)
            groups[step_group(status)].append(product_dir)
        if not active:
            break
        scheduled: List[Path] = []
        scheduled.extend(groups["analysis"][: int(settings.get("analysis_concurrency", 3))])
        scheduled.extend(groups["category"][: int(settings.get("category_concurrency", 3))])
        scheduled.extend(groups["pricing"][: int(settings.get("pricing_concurrency", 5))])
        scheduled.extend(groups["copy"][: int(settings.get("copy_concurrency", 3))])
        scheduled.extend(groups["image_generation"][: int(settings.get("image_generation_concurrency", 1))])
        scheduled.extend(groups["image_qc"][: int(settings.get("image_qc_concurrency", 2))])
        scheduled.extend(groups["ozon"][: int(settings.get("ozon_write_concurrency", 1))])
        if not scheduled:
            for product_dir in active:
                mark_hard_failure(product_dir, "validate_source", "No schedulable pipeline step was found.")
            break
        with ThreadPoolExecutor(max_workers=len(scheduled)) as executor:
            futures = [executor.submit(run_one_step, product_dir, settings) for product_dir in scheduled]
            for future in as_completed(futures):
                future.result()
        sync_batch(root, batch)
        if safe_stop_requested(batch["batch_id"]):
            return stop_batch_at_checkpoint(root, batch)
    recover_remote_pending_queue(root, batch, settings)
    return finalize_batch(root, batch)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id")
    parser.add_argument("--enqueue-only", action="store_true")
    parser.add_argument("--product-id", help="Run one eligible product as a controlled validation batch")
    args = parser.parse_args()
    lock_fd = acquire_batch_lock()
    try:
        BATCH_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
        settings = load_json(SETTINGS_PATH)
        cleanup_images(ROOT, settings)
        load_shop_environment(settings)
        os.environ["OZON_IMAGE_CHANNEL_MAX_HOURS"] = str(settings.get("image_channel_max_hours", 24))
        os.environ["OZON_IMAGE_CHANNEL_CONCURRENCY"] = str(settings.get("image_channel_concurrency", 4))
        os.environ["OZON_METADATA_CACHE_HOURS"] = str(settings.get("ozon_metadata_cache_hours", 24))
        mode = app_mode(settings)
        os.environ["UPLOAD_MODE"] = "production" if mode == "production" else "dry-run"
        if args.batch_id and args.product_id:
            parser.error("Use either --batch-id or --product-id, not both")
        if args.batch_id:
            batch = load_json(batch_path(ROOT, args.batch_id))
        else:
            batch = create_batch(ROOT, [args.product_id] if args.product_id else None)
        if args.enqueue_only:
            for entry in batch["products"]:
                queue_product(ROOT / "products" / entry["product_id"], batch["batch_id"])
            print(json.dumps(sync_batch(ROOT, batch), ensure_ascii=False, indent=2))
            return 0
        report = execute_batch(ROOT, batch, settings)
        cleanup_images(ROOT, settings)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        os.close(lock_fd)
        BATCH_LOCK_PATH.unlink(missing_ok=True)
        BATCH_PID_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
