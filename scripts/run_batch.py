#!/usr/bin/env python3
"""Run one frozen collection-inbox batch with checkpoints and bounded concurrency."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from jsonschema import Draft202012Validator

try:
    from scripts.production_input_guard import validate_formal_product_input
except ModuleNotFoundError:
    from production_input_guard import validate_formal_product_input

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
    mark_needs_attention,
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
from image_qc import file_sha256 as image_file_sha256, read_png_size  # noqa: E402
from image_wave_executor import execute_image_slot_waves  # noqa: E402
from image_slot_scheduler import pending_slots  # noqa: E402
from ozon_metadata_prewarm import prewarm_category_tree  # noqa: E402
from product_deletion import deletion_requested  # noqa: E402
from store_publications import load_publications  # noqa: E402

SETTINGS_PATH = ROOT / "config/pipeline-settings.json"
BATCH_LOCK_PATH = ROOT / "logs/.batch.lock"
BATCH_PID_PATH = ROOT / "logs/batch-runner.pid"
CURRENT_BATCH_PATH = ROOT / "logs/current-batch.json"
SAFE_STOP_REQUEST_PATH = ROOT / "logs/safe-stop-request.json"
IMAGE_GENERATION_STEPS = {"image_generation"}
IMAGE_QC_STEPS = {"image_qc"}
OZON_STEPS = {"ozon_upload"}
BUILT_IN_IMAGE_GENERATION_SOURCE = "built_in_image_tool"
STEP_START_PROGRESS = {
    step: max(2, round(index * 95 / max(len(PIPELINE_STEPS), 1)))
    for index, step in enumerate(PIPELINE_STEPS)
}
SUCCESS_STATES = {"UPLOADED", "OZON_MODERATION", "ACTIVE", "HANDED_OFF_TO_OZON"}
ATTENTION_STATES = {"NEEDS_ATTENTION", "FAILED"}
RUSSIAN_HASHTAG_RE = re.compile(r"^#[А-Яа-яЁё]{2,29}$")
CJK_TEXT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
UPLOAD_IMAGE_PRECHECK_ERROR = "主SKU颜色图仍不满足上传条件"
_batch_write_lock = threading.Lock()
_codex_semaphore = threading.BoundedSemaphore(2)
_image_slot_semaphore = threading.BoundedSemaphore(3)


def captured_shared_purchase_price_cny(source: Dict[str, Any]) -> Optional[float]:
    """Return a usable product-level purchase price when SKU-specific prices are absent.

    1688 sometimes exposes a single product price while SKU rows only contain
    SKU ids/options.  That should not stop an otherwise valid product.  We only
    accept values that come from explicit currency text and ignore stock counts
    and explanatory footnotes that the browser parser may have converted into
    numeric candidates.
    """
    values: List[float] = []
    for item in (source.get("price_information") or {}).get("price_ranges") or []:
        if not isinstance(item, dict):
            continue
        price = item.get("price_cny")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        raw_text = str(item.get("raw_text") or "")
        if "库存" in raw_text or "价格比较" in raw_text or "活动前价格" in raw_text:
            continue
        if not re.search(r"(?:¥|￥|价格\s*¥|价格\s*￥)", raw_text):
            continue
        if price > 10000:
            continue
        values.append(float(price))
    return max(values) if values else None


def codex_worker_env(settings: Dict[str, Any]) -> Dict[str, str]:
    """Keep Codex helper scripts on the same Python runtime as the project."""
    env = dict(
        os.environ,
        UPLOAD_MODE="production" if app_mode(settings) == "production" else "dry-run",
    )
    venv_python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python_bin = venv_python if venv_python.is_file() else Path(sys.executable)
    python_dir = str(python_bin.parent)
    current_path = env.get("PATH", "")
    env["PATH"] = f"{python_dir}{os.pathsep}{current_path}" if current_path else python_dir
    env["CAF_PYTHON_BIN"] = str(python_bin)
    return env


def validate_ozon_tags(product_dir: Path) -> None:
    tags_path = product_dir / "output/ozon-tags.json"
    tags = load_json(tags_path).get("tags") if tags_path.is_file() else None
    if (
        not isinstance(tags, list)
        or len(tags) > 30
        or len({str(value).casefold() for value in tags}) != len(tags)
        or not all(RUSSIAN_HASHTAG_RE.fullmatch(str(value).strip()) for value in tags)
    ):
        raise RuntimeError(
            "Ozon标签最多30个，安全优先；每个只能包含俄文字母；"
            "禁止品牌、数字、下划线、英文和容量数字；过滤后少于30个也允许，为空则不提交标签字段。"
        )


def upload_artifacts_need_refresh(product_dir: Path) -> bool:
    output = product_dir / "output"
    if not (output / "ozon-draft.json").is_file():
        return True
    if not (output / "rich-content.json").is_file():
        return True
    if not (output / "keyword-research-ru.json").is_file():
        return True
    if not (output / "ozon-upload-config.json").is_file():
        return True
    try:
        validate_ozon_tags(product_dir)
    except RuntimeError:
        return True
    return False


def _value_contains_cjk(value: Any) -> bool:
    if isinstance(value, str):
        return bool(CJK_TEXT_RE.search(value))
    if isinstance(value, list):
        return any(_value_contains_cjk(item) for item in value)
    if isinstance(value, dict):
        return any(_value_contains_cjk(item) for item in value.values())
    return False


def upload_visible_copy_contains_cjk(product_dir: Path) -> bool:
    """Detect Chinese only in buyer-visible upload copy artifacts."""
    output = product_dir / "output"
    checks = [
        ("title-ru.json", ("title_ru", "short_title_ru")),
        ("description-ru.json", ("description_ru",)),
        (
            "copy-ru.json",
            (
                "title_ru",
                "short_title",
                "description_ru",
                "selling_points",
                "bullets_ru",
                "keywords_ru",
                "hashtags_ru",
                "image_copy_ru",
            ),
        ),
        ("ozon-draft.json", ("title", "description", "keywords")),
    ]
    for filename, keys in checks:
        path = output / filename
        if not path.is_file():
            continue
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if any(_value_contains_cjk(data.get(key)) for key in keys):
            return True
        if filename == "ozon-draft.json":
            for sku in data.get("skus") or []:
                if isinstance(sku, dict) and _value_contains_cjk(sku.get("display_name_ru")):
                    return True
    return False


class ImageRegenerationRequested(RuntimeError):
    pass


class ImageGenerationStalled(subprocess.TimeoutExpired):
    """Raised when image generation is alive but produces no new slot checkpoint."""


class EarlyArtifactFailure(RuntimeError):
    """Raised when a live worker has already written an unrecoverable artifact."""


class ImageSourcePreflightBlocked(RuntimeError):
    """Raised when SKU reference images are missing before ecommerce design."""


class ProductDeletionRequested(RuntimeError):
    pass


class BatchSafeStopRequested(RuntimeError):
    """Raised after the active child process is stopped at its latest file checkpoint."""

    pass


def terminate_process_group(process: subprocess.Popen, grace_seconds: float = 5.0) -> None:
    """Stop a registered worker and every subprocess it launched."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:
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
    failure_check: Optional[Callable[[], Optional[str]]] = None,
    completion_poll_seconds: float = 0.5,
    stall_seconds: Optional[int] = None,
    worker_path_override: Optional[Path] = None,
) -> subprocess.Popen:
    if product_deleted(product_dir):
        raise ProductDeletionRequested(product_dir.name)
    popen_options: Dict[str, Any] = {"start_new_session": True}
    if os.name == "nt":
        popen_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    process = subprocess.Popen(
        command, cwd=ROOT, env=env or os.environ.copy(), stdout=output,
        stderr=subprocess.STDOUT, text=True, close_fds=True, **popen_options,
    )
    worker_path = worker_path_override or product_worker_path(product_dir.name)
    started_at = now()
    write_json_atomic(worker_path, {
        "product_id": product_dir.name, "pid": process.pid,
        "command": command[:3], "started_at": started_at,
        "last_heartbeat_at": started_at, "last_progress_at": started_at,
    })
    artifact_completed_early = False
    image_signature = max(
        completed_image_slot_count(product_dir),
        generated_image_slot_count(product_dir),
    )
    last_image_progress = time.monotonic()
    last_image_progress_at = started_at
    output_path = Path(str(getattr(output, "name", "")))
    output_signature = file_signature(output_path) if output_path.is_file() else None
    last_output_progress = time.monotonic()
    last_worker_heartbeat = 0.0
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
            if status.get("current_step") == "image_generation":
                try:
                    refresh_live_image_progress(product_dir)
                except (OSError, ValueError, json.JSONDecodeError):
                    # A concurrent child write must never interrupt the worker.
                    pass
                completed_slots = max(
                    completed_image_slot_count(product_dir),
                    generated_image_slot_count(product_dir),
                )
                if completed_slots != image_signature:
                    image_signature = completed_slots
                    last_image_progress = time.monotonic()
                    last_image_progress_at = now()
                if stall_seconds and time.monotonic() - last_image_progress >= stall_seconds:
                    raise ImageGenerationStalled(
                        command, stall_seconds,
                        output=f"图片连续{stall_seconds}秒没有新增完成槽位",
                    )
            elif stall_seconds:
                current_output_signature = file_signature(output_path) if output_path.is_file() else None
                if current_output_signature != output_signature:
                    output_signature = current_output_signature
                    last_output_progress = time.monotonic()
                    last_image_progress_at = now()
                if time.monotonic() - last_output_progress >= stall_seconds:
                    raise subprocess.TimeoutExpired(
                        command, stall_seconds,
                        output=f"步骤日志连续{stall_seconds}秒没有有效进展",
                    )
            if time.monotonic() - last_worker_heartbeat >= 3:
                worker = {
                    "product_id": product_dir.name,
                    "pid": process.pid,
                    "command": command[:3],
                    "step": status.get("current_step") or "unknown",
                    "started_at": started_at,
                    "last_heartbeat_at": now(),
                    "last_progress_at": last_image_progress_at,
                    "completed_image_slots": image_signature,
                    "planned_image_slots": planned_image_slot_count(product_dir),
                }
                write_json_atomic(worker_path, worker)
                last_worker_heartbeat = time.monotonic()
            if completion_check is not None:
                try:
                    artifact_ready = bool(completion_check())
                except Exception:
                    artifact_ready = False
                if artifact_ready:
                    artifact_completed_early = True
                    terminate_process_group(process, grace_seconds=10)
                    break
            if failure_check is not None:
                try:
                    artifact_failure = failure_check()
                except Exception:
                    artifact_failure = None
                if artifact_failure:
                    terminate_process_group(process, grace_seconds=10)
                    raise EarlyArtifactFailure(str(artifact_failure))
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
    publications = load_publications(product_dir)
    selected_publications = [
        record for record in (publications.get("stores") or {}).values()
        if record.get("selected")
    ]
    publication_items = [
        item
        for record in selected_publications
        for item in (record.get("sku_publications") or [])
        if isinstance(item, dict)
    ]
    if not items:
        items = publication_items
    ozon = status.get("ozon") or {}
    offer_map_path = product_dir / "output/store-offer-id-map.json"
    offer_map = load_json(offer_map_path) if offer_map_path.is_file() else {}
    exists_path = product_dir / "output/product-exists-check.json"
    exists = load_json(exists_path) if exists_path.is_file() else {}
    action = (
        result.get("upload_action")
        or ("image_repair" if offer_map.get("requires_image_repair") is True else None)
        or next((item.get("action") for item in publication_items if item.get("action") not in {None, "", "unknown", "UNKNOWN"}), None)
        or exists.get("action")
        or result.get("status")
        or "unknown"
    )
    publication_states = {
        str(record.get("status") or "unknown") for record in selected_publications
    }
    moderation_status = result.get("moderation_status") or (
        "handed_off" if publication_states == {"HANDED_OFF_TO_OZON"} else "unknown"
    )
    api_write_count = max(
        int(status.get("api_write_count") or 0),
        sum(int(record.get("api_write_count") or 0) for record in selected_publications),
    )
    product_status = str(status.get("status", "unknown"))
    product_errors = result.get("errors", ozon.get("errors", []))
    if product_status not in ATTENTION_STATES:
        product_errors = []
    return {
        "product_id": product_dir.name,
        "selected_sku_count": selected_sku_count(product_dir),
        "status": product_status,
        "upload_action": action,
        "offer_ids": [item.get("offer_id") for item in items if item.get("offer_id")],
        "ozon_product_ids": [
            item.get("ozon_product_id") or item.get("product_id")
            for item in items
            if item.get("ozon_product_id") or item.get("product_id")
        ],
        "task_ids": list(dict.fromkeys(
            str(item.get("task_id")) for item in items
            if item.get("task_id") not in {None, "", "unknown", "UNKNOWN"}
        )),
        "moderation_status": moderation_status,
        "warnings": status.get("warnings", []),
        "failed_step": status.get("failed_step", "unknown") if product_status in ATTENTION_STATES else "unknown",
        "errors": product_errors,
        "api_write_count": api_write_count,
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
    if str(request.get("mode") or "") not in {"manual_operator_stop", "system_network_failure"}:
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
        "failed_count": sum(row["status"] in ATTENTION_STATES for row in rows),
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
    manual_upload_resume = (
        status.get("status") == "WAITING_MANUAL_REVIEW"
        and status.get("next_action") == "ozon_upload"
        and status.get("task_authorized") is True
        and (
            int(status.get("api_write_count") or 0) == 0
            or bool(status.get("target_store_ids_for_run") or [])
        )
    )
    if manual_upload_resume:
        status.update({
            "status": "PROCESSING",
            "current_step": "ozon_upload",
            "progress": max(int(status.get("progress") or 0), STEP_START_PROGRESS.get("ozon_upload", 95)),
            "last_run_at": now(),
            "upload_priority_state": "running",
        })
        status.setdefault("history", []).append({
            "from": "WAITING_MANUAL_REVIEW", "to": "PROCESSING", "at": now(),
            "reason": "User confirmed upload; the priority upload worker started.",
        })
        write_json_atomic(status_path, status)
        return status
    if status.get("status") != "QUEUED":
        return status
    next_step = str(status.get("next_action") or "validate_source")
    status.update({
        "status": "PROCESSING",
        "current_step": next_step,
        "progress": max(int(status.get("progress") or 0), STEP_START_PROGRESS.get(next_step, 2)),
        "started_at": status.get("started_at") if status.get("started_at") not in {None, "unknown"} else now(),
        "last_run_at": now(),
        "next_action": next_step,
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
        raise FileNotFoundError("Codex executable is unavailable")
    return found


def codex_reasoning_effort(settings: Dict[str, Any], step: str) -> str:
    configured = settings.get("codex_reasoning_effort_by_step") or {}
    effort = str(configured.get(step) or configured.get("default") or "high").strip().lower()
    return effort if effort in {"minimal", "low", "medium", "high", "xhigh"} else "high"


def codex_exec_command(settings: Dict[str, Any], step: str, prompt: str) -> List[str]:
    """Build an unattended child command without unrelated MCP startup waits."""
    command = [
        codex_command(settings), "exec", "-C", str(ROOT), "--skip-git-repo-check",
        "--ephemeral", "--disable", "chronicle",
        "-s", "danger-full-access", "-c", 'approval_policy="never"',
        "-c", "mcp_servers={}",
        "-c", f'model_reasoning_effort="{codex_reasoning_effort(settings, step)}"',
    ]
    # Product facts, category metadata and approved keyword evidence are
    # already frozen locally before these steps.  Enabling live search here
    # adds startup and browsing latency while allowing the model to wander
    # outside the current-product contract.
    command.append(prompt)
    return command


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
    remaining = sum(
        1 for path in planned_paths
        if not resolve_planned_product_path(product_dir, path).is_file()
    )
    regeneration_path = product_dir / "output/image-regeneration-request.json"
    if regeneration_path.is_file():
        remaining = max(
            remaining,
            len(requested_image_slots_from_request(load_json(regeneration_path))),
        )
    calculated = per_unit * max(1, remaining)
    # The worker already has a separate idle-stall watchdog.  A fixed ten-minute
    # wall-clock limit interrupted healthy 12-slot jobs even while new images
    # were arriving, so keep the total window bounded but long enough for the
    # remaining slots.  The idle watchdog remains separately configurable.
    configured_max = max(600, min(int(settings.get("image_generation_run_max_seconds", 1800)), 3600))
    return min(calculated, configured_max)


def keep_prewrite_ozon_upload_automatic(product_dir: Path, step: str, reason: str) -> Dict[str, Any] | None:
    """Keep a pre-write Ozon upload timeout on the unattended upload path."""
    if step != "ozon_upload":
        return None
    status = load_json(product_dir / "status.json")
    ozon = status.get("ozon") or {}
    if int(status.get("api_write_count") or 0) != 0:
        return None
    if str(ozon.get("task_id") or "unknown") not in {"", "unknown", "UNKNOWN"}:
        return None
    if str(ozon.get("upload_status") or "not_started") not in {"not_started", "failed", "unknown", "UNKNOWN"}:
        return None
    retries = status.setdefault("retry_count_by_step", {})
    retries[step] = int(retries.get(step) or 0) + 1
    retry_limit = max(1, int(os.environ.get("PREWRITE_OZON_UPLOAD_RETRY_LIMIT", "5")))
    if retries[step] > retry_limit:
        return None
    status.update({
        "status": "IMAGES_GENERATED",
        "current_step": "ozon_upload",
        "progress": max(94, int(status.get("progress") or 0)),
        "failed_step": "unknown",
        "error_code": "unknown",
        "error_message": "unknown",
        "human_message": None,
        "attention_required": False,
        "next_action": "ozon_upload",
        "pending_steps": list(dict.fromkeys([*(status.get("pending_steps") or []), "ozon_upload"])),
        "active_step": None,
        "last_run_at": now(),
    })
    ozon.update({"upload_status": "not_started"})
    status["ozon"] = ozon
    warning = f"Ozon上传前未写入，系统自动继续重试 {retries[step]}/{retry_limit}，无需人工操作：{reason}"
    if warning not in status.setdefault("warnings", []):
        status["warnings"].append(warning)
    status.setdefault("history", []).append({
        "from": "ozon_upload_timeout",
        "to": "IMAGES_GENERATED",
        "at": now(),
        "reason": "Pre-write Ozon upload timeout kept on the automatic upload path.",
    })
    write_json_atomic(product_dir / "status.json", status)
    return status


def route_missing_multi_store_assets_automatically(
    product_dir: Path,
    step: str,
    reason: str,
) -> Dict[str, Any] | None:
    """Rebuild absent multi-store variants before any Ozon write."""
    if step != "ozon_upload" or "多店商品缺少" not in reason:
        return None
    design_path = product_dir / "output" / "ozon-ecommerce-design.json"
    try:
        design = load_json(design_path) if design_path.is_file() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        design = {}
    rewind_to = "store_variant_assets" if design.get("store_variants") else "ecommerce_design"
    rewind_index = PIPELINE_STEPS.index(rewind_to)
    invalidated = set(PIPELINE_STEPS[rewind_index:])
    status = load_json(product_dir / "status.json")
    previous = str(status.get("status") or "unknown")
    status["completed_steps"] = [
        item for item in (status.get("completed_steps") or []) if item not in invalidated
    ]
    status["pending_steps"] = [item for item in PIPELINE_STEPS if item not in status["completed_steps"]]
    status.update({
        "status": "IMAGES_GENERATED" if rewind_to == "store_variant_assets" else "PROCESSING",
        "current_step": rewind_to,
        "next_action": rewind_to,
        "failed_step": "unknown",
        "error_code": "unknown",
        "error_message": "unknown",
        "human_message": None,
        "attention_required": False,
        "active_step": None,
        "last_run_at": now(),
    })
    warning = f"多店资料不完整，系统已自动回到 {rewind_to} 补齐各店独立资料和图片；无需人工操作。"
    if warning not in status.setdefault("warnings", []):
        status["warnings"].append(warning)
    status.setdefault("history", []).append({
        "from": previous,
        "to": status["status"],
        "at": now(),
        "reason": f"Automatic multi-store asset repair before upload: {reason}",
    })
    write_json_atomic(product_dir / "status.json", status)
    return status


def continue_store_variant_assets_automatically(
    product_dir: Path,
    step: str,
    reason: str,
) -> Dict[str, Any] | None:
    """Keep a long multi-store image run automatic instead of surfacing a blocker."""
    if step != "store_variant_assets":
        return None
    try:
        try:
            from store_variant_assets import selected_store_ids, variant_asset_dir
        except ModuleNotFoundError:
            from scripts.store_variant_assets import selected_store_ids, variant_asset_dir
        stores = selected_store_ids(product_dir)
        ready = [store for store in stores if (variant_asset_dir(product_dir, store) / "asset-manifest.json").is_file()]
    except (OSError, ValueError, json.JSONDecodeError):
        stores, ready = [], []
    pending = [store for store in stores if store not in ready]
    status = load_json(product_dir / "status.json")
    retries = status.setdefault("retry_count_by_step", {})
    retries[step] = int(retries.get(step) or 0) + 1
    status.update({
        "status": "IMAGES_GENERATED",
        "current_step": step,
        "next_action": step,
        "failed_step": "unknown",
        "error_code": "unknown",
        "error_message": "unknown",
        "human_message": f"多店独立图片正在自动补齐：已完成 {len(ready)}/{len(stores)} 家，待补 {', '.join(pending) or '无'}。",
        "attention_required": False,
        "active_step": None,
        "last_run_at": now(),
        "store_variant_progress": {"total": len(stores), "ready_store_ids": ready, "pending_store_ids": pending},
    })
    warning = f"多店独立图片生成中断，系统将保留已通过图位并从断点自动继续；已完成 {len(ready)}/{len(stores)} 家。"
    if warning not in status.setdefault("warnings", []):
        status["warnings"].append(warning)
    write_json_atomic(product_dir / "status.json", status)
    return status


def refresh_store_variant_progress(product_dir: Path) -> Dict[str, Any]:
    """Persist the manifest-backed store count after a successful variant run."""
    try:
        try:
            from store_variant_assets import selected_store_ids, variant_asset_dir
        except ModuleNotFoundError:
            from scripts.store_variant_assets import selected_store_ids, variant_asset_dir
        stores = selected_store_ids(product_dir)
        ready = []
        for store_id in stores:
            manifest_path = variant_asset_dir(product_dir, store_id) / "asset-manifest.json"
            manifest = load_json(manifest_path) if manifest_path.is_file() else {}
            if str(manifest.get("status") or "").upper() == "PASS":
                ready.append(store_id)
    except (OSError, ValueError, json.JSONDecodeError):
        stores, ready = [], []
    pending = [store_id for store_id in stores if store_id not in ready]
    status = load_json(product_dir / "status.json")
    status["store_variant_progress"] = {
        "total": len(stores), "ready_store_ids": ready, "pending_store_ids": pending,
    }
    write_json_atomic(product_dir / "status.json", status)
    return status


def requested_image_slots_from_request(value: Dict[str, Any] | None) -> set[str]:
    """Read explicit image-regeneration slot names from all supported keys."""
    if not isinstance(value, dict):
        return set()
    names: set[str] = set()
    for key in ("requested_slots", "failed_slots", "slots"):
        for item in value.get(key) or []:
            if isinstance(item, dict):
                item = item.get("slot") or item.get("image_slot")
            slot = str(item or "").strip()
            if slot:
                names.add(slot)
    return names


def completed_image_slot_count(product_dir: Path) -> int:
    plan_path = product_dir / "output/image-plan.json"
    if not plan_path.is_file():
        return 0
    plan = load_json(plan_path)
    requires_lock = (plan.get("generator_contract") or {}).get("product_pixel_lock_required") is True
    requires_verified_checkpoint = (
        (plan.get("generator_contract") or {}).get("true_parallel_slot_executor") is True
    )
    hard_gate = {}
    hard_gate_path = product_dir / "output/image-hard-gate.json"
    if hard_gate_path.is_file():
        try:
            hard_gate = {
                str(item.get("slot")): item
                for item in (load_json(hard_gate_path).get("checked_slots") or [])
                if item.get("slot")
            }
        except (OSError, ValueError, json.JSONDecodeError):
            hard_gate = {}
    completed = 0
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key) or []:
            output_value = str(item.get("output_path") or "")
            slot = str(item.get("slot") or "")
            if not output_value or not slot:
                continue
            output_path = resolve_planned_product_path(product_dir, output_value)
            manifest_path = product_dir / "output/product-lock" / f"{slot}.json"
            if not output_path.is_file():
                continue
            gate_entry = hard_gate.get(slot, {})
            if str(gate_entry.get("status") or "").strip().lower() == "pass" and receipt_has_builtin_image_source(gate_entry):
                expected = str(gate_entry.get("sha256") or "")
                try:
                    if expected and expected == image_file_sha256(output_path):
                        completed += 1
                        continue
                except OSError:
                    pass
            if image_slot_receipt_gate_entry(product_dir, slot, output_value):
                completed += 1
                continue
            # Older plans deliberately used AI reference editing without a
            # compositor lock.  Those plans still produce valid progressive
            # checkpoints; requiring a lock here made the workbench stay at
            # 78% forever even though PNGs had already been saved.
            if not requires_verified_checkpoint and not requires_lock and output_path.is_file():
                completed += 1
                continue
            if manifest_path.is_file():
                try:
                    manifest = load_json(manifest_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if (manifest.get("audit") or {}).get("status") == "pass":
                    completed += 1
    return completed


def generated_image_slot_count(product_dir: Path) -> int:
    """Count planned slots that already have a readable generated image.

    Image workers create the PNG before the isolated receipt. Counting this as
    progress prevents a completed image call from being treated as a stall,
    while final completion still requires the receipt or hard gate.
    """
    plan_path = product_dir / "output/image-plan.json"
    if not plan_path.is_file():
        return 0
    try:
        plan = load_json(plan_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0
    completed = 0
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key) or []:
            output_value = str(item.get("output_path") or "")
            if not output_value:
                continue
            try:
                output_path = ensure_slot_output_path(product_dir, output_value)
                read_png_size(output_path)
            except (OSError, ValueError):
                continue
            completed += 1
    return completed


def planned_image_slot_count(product_dir: Path) -> int:
    plan_path = product_dir / "output/image-plan.json"
    if not plan_path.is_file():
        return 0
    try:
        plan = load_json(plan_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0
    return sum(
        1
        for key in ("main_images", "detail_images", "disclaimer_images")
        for item in (plan.get(key) or [])
        if item.get("output_path") and item.get("slot")
    )


def schedulable_image_slot_count(product_dir: Path, concurrency: int = 3) -> int:
    """Return slots that can still be generated before image QC.

    A product can be interrupted after a few slots pass and before the
    remaining planned slots run.  In that state status may incorrectly point to
    image_qc, but the scheduler still has concrete planned work.  This helper
    intentionally counts only schedulable work.  A failed ``needs_review`` image
    slot remains schedulable so authorized batches can recover without an
    operator clicking through every image failure.
    """
    try:
        pending = pending_slots(product_dir, concurrency)
    except (OSError, ValueError, json.JSONDecodeError, FileNotFoundError):
        return 0
    return int(pending.get("pending_slot_count") or 0)


def route_incomplete_images_back_to_generation(
    product_dir: Path,
    status: Dict[str, Any],
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    """Repair stale checkpoints that try to continue before all slots are generated."""
    concurrency = max(1, min(int(settings.get("image_slot_concurrency", 3)), 3))
    pending_count = schedulable_image_slot_count(product_dir, concurrency)
    if pending_count <= 0:
        return status
    status["status"] = "PROCESSING"
    status["current_step"] = "image_generation"
    status["next_action"] = "image_generation"
    status["failed_step"] = "unknown"
    status["error_code"] = "unknown"
    status["error_message"] = "unknown"
    status["active_step"] = None
    status["last_run_at"] = now()
    status["completed_steps"] = [
        step for step in (status.get("completed_steps") or [])
        if step not in {"image_generation", "image_qc"}
    ]
    status["pending_steps"] = [step for step in PIPELINE_STEPS if step not in status["completed_steps"]]
    status.setdefault("warnings", []).append(
        f"图片还剩{pending_count}个图位未生成，已自动退回继续生图。"
    )
    write_json_atomic(product_dir / "status.json", status)
    cache_path = product_dir / "output/pipeline-cache.json"
    if cache_path.is_file():
        try:
            cache = load_json(cache_path)
        except (OSError, ValueError, json.JSONDecodeError):
            cache = {}
        steps = cache.get("steps")
        if isinstance(steps, dict):
            changed = False
            for name in ("image_generation", "image_qc", "ozon_upload"):
                if name in steps:
                    steps.pop(name, None)
                    changed = True
            if changed:
                write_json_atomic(cache_path, cache)
    return status


def require_all_planned_images_before_upload(product_dir: Path, settings: Dict[str, Any]) -> None:
    total = planned_image_slot_count(product_dir)
    completed = completed_image_slot_count(product_dir)
    if total and completed < total:
        status_path = product_dir / "status.json"
        status = load_json(status_path) if status_path.is_file() else {}
        route_incomplete_images_back_to_generation(product_dir, status, settings)
        raise RuntimeError(f"图片未齐，已退回继续生图：{completed}/{total}")


def route_upload_image_precheck_back_to_image_plan(
    product_dir: Path,
    status: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    """Rebuild the image plan when upload precheck finds missing SKU images."""
    rewind_to = "image_plan"
    rewind_index = PIPELINE_STEPS.index(rewind_to)
    invalidated = set(PIPELINE_STEPS[rewind_index:])
    previous = str(status.get("status") or "unknown")
    status["completed_steps"] = [
        step for step in (status.get("completed_steps") or [])
        if step not in invalidated
    ]
    status["pending_steps"] = [
        step for step in PIPELINE_STEPS if step not in status["completed_steps"]
    ]
    status.update({
        "status": "PROCESSING",
        "current_step": rewind_to,
        "next_action": rewind_to,
        "failed_step": "unknown",
        "error_code": "unknown",
        "error_message": "unknown",
        "human_message": None,
        "attention_required": False,
        "active_step": None,
        "progress": max(int(status.get("progress") or 0), STEP_START_PROGRESS.get(rewind_to, 2)),
        "last_run_at": now(),
    })
    retry_counts = status.setdefault("retry_count_by_step", {})
    for step in invalidated:
        retry_counts.pop(step, None)
    warning = "上传前发现SKU主图未齐，已自动退回图片计划重新生成缺失图片；无需人工继续。"
    warnings = status.setdefault("warnings", [])
    if warning not in warnings:
        warnings.append(warning)
    status.setdefault("history", []).append({
        "from": previous,
        "to": "PROCESSING",
        "at": now(),
        "reason": f"Auto-rewind upload image precheck after missing SKU image: {reason}",
    })
    write_json_atomic(product_dir / "status.json", status)
    (product_dir / "output/image-regeneration-request.json").unlink(missing_ok=True)
    cache_path = product_dir / "output/pipeline-cache.json"
    if cache_path.is_file():
        try:
            cache = load_json(cache_path)
            steps = cache.setdefault("steps", {})
            for step in invalidated:
                steps.pop(step, None)
            write_json_atomic(cache_path, cache)
        except (OSError, ValueError, json.JSONDecodeError):
            cache_path.unlink(missing_ok=True)
    return status


def route_image_qc_failures_back_to_image_plan(
    product_dir: Path,
    status: Dict[str, Any],
    report: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    """Ask the image planner to revise failed slot prompts before regeneration."""
    rewind_to = "image_plan"
    rewind_index = PIPELINE_STEPS.index(rewind_to)
    invalidated = set(PIPELINE_STEPS[rewind_index:])
    previous = str(status.get("status") or "unknown")
    issues = report.get("issues") or []
    failed_slots = sorted({
        str(slot)
        for issue in issues
        for slot in (issue.get("image_slots") or [])
        if str(slot).strip()
    })
    if not failed_slots:
        failed_slots = [
            str(item.get("slot"))
            for item in report.get("images_checked") or []
            if str(item.get("slot") or "").strip()
        ]
    revision_count = int(status.get("image_qc_revision_count") or 0) + 1
    revision_limit = max(1, int(status.get("image_qc_revision_limit") or os.environ.get("IMAGE_QC_REVISION_LIMIT", "10")))
    status["image_qc_revision_count"] = revision_count
    status["image_qc_revision_limit"] = revision_limit
    if revision_count > revision_limit:
        status.update({
            "status": "NEEDS_ATTENTION",
            "current_step": "image_qc",
            "next_action": "retry_failed_step",
            "failed_step": "image_qc",
            "error_code": "IMAGE_QC_REVISION_LIMIT",
            "error_message": reason,
            "human_message": f"图片已自动修复{revision_limit}轮仍未通过，任务已停止，避免重复生图。",
            "attention_required": True,
            "active_step": None,
            "last_run_at": now(),
        })
        status.setdefault("warnings", []).append(
            "图片质检连续失败达到上限，已停止自动循环；已通过图片和现有结果均已保留。"
        )
        (product_dir / "output/image-regeneration-request.json").unlink(missing_ok=True)
        write_json_atomic(product_dir / "status.json", status)
        return status
    requested_at = now()
    slot_issues: Dict[str, List[Dict[str, Any]]] = {slot: [] for slot in failed_slots}
    for issue in issues:
        for slot in issue.get("image_slots") or []:
            slot_name = str(slot).strip()
            if slot_name in slot_issues:
                slot_issues[slot_name].append({
                    "code": issue.get("code") or "image_qc_failure",
                    "severity": issue.get("severity") or "critical",
                    "message": issue.get("message") or issue.get("reason") or reason,
                })
    write_json_atomic(product_dir / "output/image-design-revision-request.json", {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "requested_at": requested_at,
        "source": "image_qc",
        "failed_slots": failed_slots,
        "critical_failures": report.get("critical_failures") or [],
        "slot_issues": slot_issues,
        "reason": reason,
        "revision_contract": (
            "Revise only the failed image slot prompts. Preserve title, description, "
            "tags, attributes, SKU count, image count and upload payload. Keep passed "
            "images untouched and keep product facts locked."
        ),
    })
    write_json_atomic(product_dir / "output/image-regeneration-request.json", {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "requested_at": requested_at,
        "source": "image_qc",
        "requested_slots": failed_slots,
        "failed_slots": failed_slots,
        "preserve_passed_images": True,
        "reason": reason,
    })
    status["completed_steps"] = [
        step for step in (status.get("completed_steps") or [])
        if step not in invalidated
    ]
    status["pending_steps"] = [
        step for step in PIPELINE_STEPS if step not in status["completed_steps"]
    ]
    status.update({
        "status": "PROCESSING",
        "current_step": rewind_to,
        "next_action": rewind_to,
        "failed_step": "unknown",
        "error_code": "unknown",
        "error_message": "unknown",
        "human_message": None,
        "attention_required": False,
        "active_step": None,
        "progress": max(int(status.get("progress") or 0), STEP_START_PROGRESS.get(rewind_to, 2)),
        "last_run_at": now(),
    })
    status.setdefault("retry_count_by_step", {})["image_qc"] = revision_count
    warning = "图片质检发现硬错误，已打回视觉总监只修改失败图位提示词；已通过图片不会重做。"
    warnings = status.setdefault("warnings", [])
    if warning not in warnings:
        warnings.append(warning)
    status.setdefault("history", []).append({
        "from": previous,
        "to": "PROCESSING",
        "at": now(),
        "reason": f"Revise failed image slot prompts only after image QC failures: {reason}",
    })
    write_json_atomic(product_dir / "status.json", status)
    cache_path = product_dir / "output/pipeline-cache.json"
    if cache_path.is_file():
        try:
            cache = load_json(cache_path)
            steps = cache.setdefault("steps", {})
            for step in invalidated:
                steps.pop(step, None)
            write_json_atomic(cache_path, cache)
        except (OSError, ValueError, json.JSONDecodeError):
            cache_path.unlink(missing_ok=True)
    return status


def refresh_live_image_progress(product_dir: Path) -> None:
    """Expose slot-level progress while the Codex image worker is running."""
    with _batch_write_lock:
        status_path = product_dir / "status.json"
        if not status_path.is_file():
            return
        status = load_json(status_path)
        if status.get("current_step") != "image_generation":
            return
        total = planned_image_slot_count(product_dir)
        completed = completed_image_slot_count(product_dir)
        if not total:
            return
        # Keep the existing phase position, but make completed slots visible in
        # the remaining image-generation band (78..90) instead of waiting for the
        # child process to exit before the UI changes.
        # Image progress is an absolute position inside the 78..90 band.
        # Adding the slot fraction to the already-updated value made 3/11
        # appear as 90%, even though most images were still running.
        live = min(90, 78 + round(12 * completed / total))
        if live <= int(status.get("progress") or 0) and completed <= int(status.get("completed_image_slots") or 0):
            return
        status["progress"] = max(int(status.get("progress") or 0), live)
        status["completed_image_slots"] = completed
        generated = generated_image_slot_count(product_dir)
        if generated > completed:
            status["generated_image_slots"] = generated
            status["image_progress_note"] = f"已生成{generated}张图片，其中{completed}张已通过回执/硬检查。"
        else:
            status["generated_image_slots"] = generated
            status["image_progress_note"] = f"已通过{completed}/{total}张图片。"
        status["planned_image_slots"] = total
        status["last_run_at"] = now()
        write_json_atomic(status_path, status)


def initialize_image_generation_progress(product_dir: Path, concurrency: int) -> None:
    """Publish image slot totals before the first worker returns."""
    with _batch_write_lock:
        status_path = product_dir / "status.json"
        if not status_path.is_file():
            return
        status = load_json(status_path)
        total = planned_image_slot_count(product_dir)
        if not total:
            return
        generated = generated_image_slot_count(product_dir)
        completed = completed_image_slot_count(product_dir)
        try:
            schedule = pending_slots(product_dir, max(1, min(int(concurrency), 3)))
        except Exception:
            schedule = {}
        first_wave = list((schedule.get("waves") or [[]])[0] or [])
        active = [str(item.get("slot")) for item in first_wave if item.get("slot")]
        status.update({
            "current_step": "image_generation",
            "planned_image_slots": total,
            "generated_image_slots": generated,
            "completed_image_slots": completed,
            "image_parallelism": max(1, min(int(concurrency), 3)),
            "active_image_slots": active,
            "image_progress_note": (
                f"准备并发生成图片：已通过{completed}/{total}，本轮最多{max(1, min(int(concurrency), 3))}张。"
            ),
            "last_run_at": now(),
        })
        status["progress"] = max(int(status.get("progress") or 0), STEP_START_PROGRESS.get("image_generation", 78))
        write_json_atomic(status_path, status)


def image_slot_states_after_interruption(product_dir: Path, reason_code: str) -> List[str]:
    """Keep valid images and mark only unfinished slots for one bounded retry."""
    plan_path = product_dir / "output/image-plan.json"
    if not plan_path.is_file():
        return []
    image_plan = load_json(plan_path)
    hard_gate_path = product_dir / "output/image-hard-gate.json"
    try:
            passed_slots = {
                str(item.get("slot"))
                for item in (load_json(hard_gate_path).get("checked_slots") or [])
                if (
                    item.get("slot")
                    and str(item.get("status") or "").strip().lower() == "pass"
                    and receipt_has_builtin_image_source(item)
                )
            }
    except (OSError, ValueError, json.JSONDecodeError):
        passed_slots = set()
    missing: List[str] = []
    requires_lock = (image_plan.get("generator_contract") or {}).get("product_pixel_lock_required") is True
    requires_verified_checkpoint = (
        (image_plan.get("generator_contract") or {}).get("true_parallel_slot_executor") is True
    )
    for collection in ("main_images", "detail_images", "disclaimer_images"):
        for item in image_plan.get(collection) or []:
            slot = str(item.get("slot") or "")
            output_path = resolve_planned_product_path(product_dir, str(item.get("output_path") or ""))
            lock_path = product_dir / "output/product-lock" / f"{slot}.json"
            completed = bool(slot) and (
                slot in passed_slots
                or (
                    output_path.is_file()
                    and not requires_verified_checkpoint
                    and (not requires_lock or lock_path.is_file())
                )
            )
            if completed:
                item["status"] = "generated"
                item["failure_reason"] = "unknown"
                continue
            item["status"] = "needs_review"
            item["failure_reason"] = reason_code
            if slot:
                missing.append(slot)
    write_json_atomic(plan_path, image_plan)
    return missing


def notify_mac(title: str, message: str) -> None:
    """Best-effort local notification; notification failure never affects a task."""
    if sys.platform != "darwin":
        return
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", f'display notification "{safe_message}" with title "{safe_title}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        )
    except OSError:
        pass


def recover_interrupted_image_generation(
    product_dir: Path,
    settings: Dict[str, Any],
    reason: str,
    reason_code: str,
) -> Dict[str, Any]:
    """Retry unfinished slots; ongoing forward progress may continue in another host window."""
    missing = image_slot_states_after_interruption(product_dir, reason_code)
    previous_request_path = product_dir / "output/image-regeneration-request.json"
    previous_missing: set[str] = set()
    if previous_request_path.is_file():
        try:
            previous_missing = {
                str(item.get("slot") if isinstance(item, dict) else item)
                for item in (load_json(previous_request_path).get("failed_slots") or [])
                if (item.get("slot") if isinstance(item, dict) else item)
            }
        except (OSError, ValueError, json.JSONDecodeError):
            previous_missing = set()
    current_missing = set(missing)
    made_forward_progress = bool(previous_missing) and current_missing < previous_missing
    if made_forward_progress:
        # This is continuation of a healthy progressive job, not a second
        # retry of the same failed slot.  The no-progress retry limit remains
        # one, while completed images stay immutable.
        status_path = product_dir / "status.json"
        status = load_json(status_path)
        status.setdefault("retry_count_by_step", {})["image_generation"] = 0
        status.setdefault("warnings", []).append(
            f"上一个生图窗口新增了{len(previous_missing) - len(current_missing)}张图片；"
            f"已保留完成图片并继续剩余{len(current_missing)}张。"
        )
        write_json_atomic(status_path, status)
    status = request_single_image_regeneration(product_dir, missing, reason)
    if status.get("status") in ATTENTION_STATES:
        status["host_recovery_state"] = "needs_attention"
        status["host_recovery_reason"] = reason
        write_json_atomic(product_dir / "status.json", status)
        notify_mac("AI Factory 需要处理", f"{product_dir.name} 生图自动修复一次后仍然失败")
        return {
            "product_id": product_dir.name,
            "outcome": "failed",
            "step": "image_generation",
            "failed_slots": missing,
        }
    status["host_recovery_state"] = "recovering"
    status["host_recovery_reason"] = reason
    status.setdefault("warnings", []).append(
        f"生图主机已自动重启一次；保留完成图片，"
        + (f"只继续 {len(missing)} 个未完成槽位。" if missing else "只继续完成生图收尾检查。")
    )
    write_json_atomic(product_dir / "status.json", status)
    return {
        "product_id": product_dir.name,
        "outcome": "retry",
        "step": "image_generation",
        "failed_slots": missing,
    }


def clear_image_host_recovery(product_dir: Path) -> None:
    status_path = product_dir / "status.json"
    if not status_path.is_file():
        return
    status = load_json(status_path)
    status["host_recovery_state"] = "normal"
    status["host_recovery_reason"] = "unknown"
    write_json_atomic(status_path, status)


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
    validate_formal_product_input(product_dir)
    offline_fixture = False
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
    shared_price = captured_shared_purchase_price_cny(source)
    for sku in skus:
        if not str(sku.get("sku_id") or "").strip():
            raise RuntimeError("Every selected SKU requires a real sku_id and purchase price")
        if sku.get("purchase_price") is None and shared_price is None:
            raise RuntimeError("Every selected SKU requires a real sku_id and purchase price")
    if (
        not isinstance(selection.get("category_id"), int)
        or not isinstance(selection.get("type_id"), int)
        or selection.get("allow_runtime_rematch") is not False
        or not (selection.get("rules_snapshot") or {}).get("attributes")
    ):
        raise RuntimeError("Collector final Ozon category and official rule snapshot are required")


def offer_exists_check(product_dir: Path, _settings: Dict[str, Any]) -> None:
    output = product_dir / "output"
    # Offer IDs are now allocated per selected store only after the user has
    # approved the final product. A product-level lookup using the old shared
    # Pxxxxxx-SKU identifier would query the wrong identity and can produce a
    # false UPDATE/mixed-conflict result. The real read-only existence check is
    # still mandatory inside each isolated store upload immediately before the
    # CREATE request.
    write_json_atomic(output / "offer-id-precheck.json", {
        "product_id": product_dir.name,
        "offer_ids": [],
        "status": "deferred_store_specific",
        "action": "create_after_manual_confirmation",
        "existing_offer_ids": [],
        "conflicts": [],
        "reason": "Each selected store/SKU receives a persisted opaque offer_id before upload; live collision check runs per store.",
        "checked_at": now(),
    })


def upload_feasibility_final_artifacts_ready(output: Path) -> bool:
    return all(
        (output / name).is_file()
        for name in (
            "attribute-fill-input.json",
            "ozon-attributes-final.json",
            "ozon-draft.json",
        )
    )


def final_required_attribute_missing_ids(output: Path, required_ids: set[int]) -> List[int]:
    final_path = output / "ozon-attributes-final.json"
    if not final_path.is_file():
        return sorted(required_ids)
    final_attributes = load_json(final_path)
    summary = final_attributes.get("required_summary") or {}
    if isinstance(summary.get("missing_attribute_ids"), list):
        return sorted({
            int(attribute_id)
            for attribute_id in summary.get("missing_attribute_ids") or []
            if int(attribute_id) in required_ids
        })
    if int(summary.get("missing") or 0) == 0 and summary:
        return []

    def filled(item: Dict[str, Any]) -> bool:
        for key in ("target_value", "value", "canonical_value"):
            value = item.get(key)
            if value not in {None, "", "unknown"}:
                return True
        values = item.get("values")
        return isinstance(values, list) and any(
            value not in {None, "", "unknown"} for value in values
        )

    by_id: Dict[int, List[Dict[str, Any]]] = {}
    for item in final_attributes.get("attributes") or []:
        try:
            by_id.setdefault(int(item["attribute_id"]), []).append(item)
        except (KeyError, TypeError, ValueError):
            continue
    for sku_items in (final_attributes.get("attributes_by_sku") or {}).values():
        if isinstance(sku_items, list):
            for item in sku_items:
                try:
                    by_id.setdefault(int(item["attribute_id"]), []).append(item)
                except (KeyError, TypeError, ValueError):
                    continue
    return sorted(
        attribute_id
        for attribute_id in required_ids
        if not any(filled(item) for item in by_id.get(attribute_id, []))
    )


def upload_feasibility(product_dir: Path) -> None:
    output = product_dir / "output"
    source = load_json(product_dir / "input/source.json")
    category = load_json(output / "ozon-category.json")
    metadata = load_json(output / "ozon-category-attributes.json")
    attributes_path = output / "ozon-attributes-final.json"
    if not attributes_path.is_file():
        # Early precheck runs before the attribute compiler; the phase-A
        # matcher artifact is the best available then.
        attributes_path = output / "ozon-attributes.json"
    mapped_attributes = load_json(attributes_path)
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
    final_artifacts_ready = upload_feasibility_final_artifacts_ready(output)
    deferred_role_names = {
        "бренд", "тип товара", "название модели", "модель",
        "название модели (для объединения в одну карточку)",
    }
    compiler_fillable_required = sorted(
        attribute_id
        for attribute_id in required_ids
        if (
            attribute_id not in mapped_by_id
            or mapped_by_id[attribute_id].get("validation_status") != "valid"
        )
    )
    role_deferred_required = sorted(
        attribute_id
        for attribute_id in required_ids
        if str(metadata_by_id.get(attribute_id, {}).get("attribute_name") or "").strip().casefold()
        in deferred_role_names
    )
    if final_artifacts_ready:
        deferred_required = []
        missing_required = final_required_attribute_missing_ids(output, required_ids)
    else:
        deferred_required = sorted(set(role_deferred_required) | set(compiler_fillable_required))
        missing_required = []
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
        "offer_conflict": offers.get("status") in {"ok", "development_skipped", "deferred_store_specific"},
    }
    value = {
        "product_id": product_dir.name, "status": "PASS" if all(checks.values()) else "FAIL",
        "stage": "final" if final_artifacts_ready else "early_precheck",
        "final_artifacts_ready": final_artifacts_ready,
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
    elif step == "product_analysis":
        run_checked([python, "scripts/product_analysis_fast.py", str(product_dir)], log_path, timeout, product_dir)
        require_files(product_dir, ["output/product-analysis.json"])
    elif step == "product_positioning":
        run_checked([python, "scripts/product_positioning_agent.py", str(product_dir), "--write"], log_path, timeout, product_dir)
        require_files(product_dir, ["output/product-positioning.json"])
    elif step == "category_match":
        run_checked([python, "scripts/ozon_metadata_matcher.py", str(product_dir), "--write"], log_path, timeout, product_dir)
        analysis = load_json(product_dir / "output/product-analysis.json")
        category_risks = [
            item for item in (analysis.get("risks") or [])
            if (
                isinstance(item, dict)
                and str(item.get("area") or "") in {"category_fit", "category_identity", "category"}
            )
        ]
        mismatch_markers = (
            "不适配", "不匹配", "不是", "冲突", "明显", "而来源商品",
            "mismatch", "incompatible", "different product", "conflict",
        )
        if any(
            bool(item.get("blocking"))
            or any(marker in str(item.get("message") or "").casefold() for marker in mismatch_markers)
            for item in category_risks
        ):
            raise RuntimeError(
                "锁定Ozon类目与已识别商品类型不匹配；已停止后续生图和上传，"
                "请在工作台重新选择真实类目。"
            )
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
    elif step == "ecommerce_design":
        run_checked([python, "scripts/product_fact_merger.py", str(product_dir)], log_path, timeout, product_dir)
        require_files(product_dir, ["output/merged-product-facts.json", "output/ozon-category-attributes.json"])
        run_checked([python, "scripts/attribute_fill_input.py", str(product_dir)], log_path, timeout, product_dir)
        require_files(product_dir, [
            "output/attribute-fill-input.json",
            "output/attribute-fill-input.compact.json",
            "output/ecommerce-design-context.json",
        ])
        run_checked([python, "scripts/image_source_preflight.py", str(product_dir)], log_path, timeout, product_dir)
        require_files(product_dir, ["output/image-source-preflight.json"])
        return False
    elif step == "image_plan":
        require_files(product_dir, [
            "output/ozon-ecommerce-design.json",
            "output/title-ru.json", "output/description-ru.json", "output/ozon-tags.json",
            "output/ozon-attributes-final.json", "output/pricing-result.json",
            "output/platform-grouping-result.json",
            "output/image-source-preflight.json",
        ])
        # The connected-Codex designer is the only commercial source.
        # For an allowed multi-store group the master image lane deliberately
        # becomes the first selected store's lane.  The remaining stores are
        # generated in isolated workspaces by store_variant_assets after this
        # lane has passed QC, instead of reusing the first store's pictures.
        design_path = product_dir / "output/ozon-ecommerce-design.json"
        design = load_json(design_path)
        try:
            try:
                from store_variant_assets import selected_store_ids
                from ozon_ecommerce_designer_contract import materialize, store_variant_design
            except ModuleNotFoundError:
                from scripts.store_variant_assets import selected_store_ids
                from scripts.ozon_ecommerce_designer_contract import materialize, store_variant_design
            selected_stores = selected_store_ids(product_dir)
            projected_store_variant = len(selected_stores) > 1 and bool(design.get("store_variants"))
            if projected_store_variant:
                materialize(product_dir, store_variant_design(design, selected_stores[0]))
                status = load_json(product_dir / "status.json")
                status["requires_store_variant_assets"] = True
                write_json_atomic(product_dir / "status.json", status)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"多店图片方案投影失败：{exc}") from exc
        # ``materialize`` above already wrote the selected store's copy and
        # plan.  Re-materializing from the generic file here used to replace
        # that projection with whichever top-level variant happened to be
        # persisted last, making the primary lane depend on registry order.
        if not projected_store_variant:
            run_checked([python, "scripts/ozon_ecommerce_designer_contract.py", str(product_dir), "--materialize"], log_path, timeout, product_dir)
        run_checked([python, "scripts/image_planner.py", str(product_dir), "--write"], log_path, timeout, product_dir)
        require_files(product_dir, ["output/image-plan.json"])
    elif step == "image_qc":
        # Rebuild the report on every entry.  The image worker updates
        # image-hard-gate.json incrementally, so an older QC report may only
        # contain the slots that existed during a previous attempt and would
        # incorrectly block final upload even when all planned images now pass.
        run_checked([python, "scripts/image_qc.py", str(product_dir), "--hard-gate", "--write"], log_path, timeout, product_dir)
        run_checked([python, "scripts/image_qc.py", str(product_dir), "--verify-report"], log_path, timeout, product_dir)
        report = load_json(product_dir / "output/image-qc-report.json")
        if report.get("critical_failures"):
            slots = sorted({slot for issue in report.get("issues", []) for slot in issue.get("image_slots", [])})
            if not slots:
                slots = [item["slot"] for item in report.get("images_checked", [])]
            status = load_json(product_dir / "status.json")
            route_image_qc_failures_back_to_image_plan(
                product_dir,
                status,
                report,
                "Image QC failed; failed slots require designer prompt revision before regeneration.",
            )
            raise ImageRegenerationRequested("Image QC requested designer prompt revision before slot regeneration")
        (product_dir / "output/image-regeneration-request.json").unlink(missing_ok=True)
        # Image generation runs after the pre-image field-completion pass.
        # Once QC passes, rebuild the final local upload artifacts so
        # ozon-draft.json contains the current generated image slots and
        # rich-content.json exists before the user clicks upload.
        run_checked([
            python, "ozon-field-completion/cli.py", product_dir.name,
        ], log_path, timeout, product_dir)
        require_files(product_dir, [
            "output/ozon-draft.json",
            "output/rich-content.json",
            "output/ozon-tags.json",
            "output/ozon-attributes-final.json",
            "output/ozon-upload-config.json",
        ])
    elif step == "store_variant_assets":
        # This stage never calls Ozon.  It stores a separately generated and
        # QC-checked visual set for every selected cross-entity store.
        run_checked([
            python, "scripts/store_variant_assets.py", str(product_dir), "--prepare",
        ], log_path, timeout, product_dir)
        try:
            try:
                from store_variant_assets import has_store_variants, selected_store_ids, verify_variant_assets
            except ModuleNotFoundError:
                from scripts.store_variant_assets import has_store_variants, selected_store_ids, verify_variant_assets
            if has_store_variants(product_dir):
                for store_id in selected_store_ids(product_dir):
                    verify_variant_assets(product_dir, store_id)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"多店独立图片资产校验失败：{exc}") from exc
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
    elif step == "field_completion":
        run_checked([
            python, "ozon-field-completion/cli.py", product_dir.name, "--pre-image",
        ], log_path, timeout, product_dir)
        require_files(product_dir, [
            "output/ozon-tags.json", "output/ozon-attributes-final.json",
            "output/attribute-coverage-report.json",
        ])
        tags = load_json(product_dir / "output/ozon-tags.json")
        if int(tags.get("count") or 0) != len(tags.get("tags") or []) or len(tags.get("tags") or []) > 30:
            raise RuntimeError("Ozon tags must contain at most 30 valid entries")
        validate_ozon_tags(product_dir)
        attributes = load_json(product_dir / "output/ozon-attributes-final.json")
        missing_required = int((attributes.get("required_summary") or {}).get("missing") or 0)
        if missing_required:
            raise RuntimeError(f"{missing_required} required Ozon attributes are still missing")
    elif step == "russian_copy":
        run_checked([
            python, "scripts/ozon_ecommerce_designer_contract.py", str(product_dir), "--materialize",
        ], log_path, timeout, product_dir)
        require_files(product_dir, [
            "output/copy-ru.json",
            "output/title-ru.json",
            "output/description-ru.json",
            "output/keyword-research-ru.json",
        ])
        # ozon-tags.json / ozon-attributes-final.json / ozon-draft.json 由随后的
        # field_completion 单一出口生成（2026-08-14 双写合并）。
        if not russian_copy_output_is_complete(product_dir):
            raise RuntimeError("Russian copy projection failed validation")
    elif step == "ozon_upload":
        require_all_planned_images_before_upload(product_dir, settings)
        copy_projection_refreshed = False
        if (
            upload_visible_copy_contains_cjk(product_dir)
            and (product_dir / "output/ozon-ecommerce-design.json").is_file()
        ):
            run_checked([
                python, "scripts/ozon_ecommerce_designer_contract.py", str(product_dir), "--repair-buyer-copy",
            ], log_path, timeout, product_dir)
            copy_projection_refreshed = True
        if copy_projection_refreshed or upload_artifacts_need_refresh(product_dir):
            run_checked([
                python, "ozon-field-completion/cli.py", product_dir.name,
            ], log_path, timeout, product_dir)
            require_files(product_dir, [
                "output/ozon-upload-config.json",
                "output/ozon-draft.json",
                "output/rich-content.json",
                "output/ozon-tags.json",
                "output/ozon-attributes-final.json",
            ])
            validate_ozon_tags(product_dir)
        if upload_feasibility_final_artifacts_ready(product_dir / "output"):
            upload_feasibility(product_dir)
        if app_mode(settings) != "production":
            raise RuntimeError("APP_MODE=development prohibits real Ozon batch uploads")
        status = load_json(product_dir / "status.json")
        retry_stores = [str(value) for value in status.get("target_store_ids_for_run") or []]
        if (
            not retry_stores
            and int(status.get("api_write_count") or 0) > 0
            and (product_dir / "output/ozon-write-receipt.json").is_file()
        ):
            # The product-level write was accepted earlier. Move to read-only
            # task polling instead of resubmitting.  A failed-store retry is
            # different: multi_store_upload.py receives an explicit allowlist
            # and checks each store's own task/offer state independently.
            complete_step(product_dir, step)
            return True
        command = [python, "scripts/multi_store_upload.py", str(product_dir), "--execute"]
        for store_id in retry_stores:
            command.extend(["--only-store", store_id])
        run_checked(command, log_path, timeout, product_dir)
        # SQLite is authoritative after the task-state cutover.  Reading the
        # removed legacy JSON here can turn a successful multi-store hand-off
        # into a false pipeline failure after all Ozon writes have completed.
        publications = load_publications(product_dir)
        if not any(item.get("selected") for item in (publications.get("stores") or {}).values()):
            raise RuntimeError("No selected Ozon store publication was found")
    else:
        return False
    if product_deleted(product_dir):
        raise ProductDeletionRequested(product_dir.name)
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


def ecommerce_design_artifact_is_complete(product_dir: Path, path: Path) -> bool:
    """Validate the unified connected-Codex design before advancing."""
    if not path.is_file():
        return False
    try:
        from ozon_ecommerce_designer_contract import validate_design
        value = load_json(path)
        return (
            not validate_design(product_dir, value)
            and value.get("product_id") == product_dir.name
            and (value.get("processing") or {}).get("step") == "ecommerce_design"
            and (value.get("processing") or {}).get("model_mode") == "connected_codex"
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False


def ecommerce_design_output_is_complete(product_dir: Path) -> bool:
    """Validate the current unified connected-Codex design before advancing."""
    return ecommerce_design_artifact_is_complete(
        product_dir,
        product_dir / "output/ozon-ecommerce-design.json",
    )


def ecommerce_design_live_failure_reason(product_dir: Path, quiet_seconds: float = 15.0) -> Optional[str]:
    """Detect complete-but-invalid designer output while the worker is still alive.

    A connected designer sometimes writes `{}` or a JSON object with
    `processing.status=completed` but missing image arrays, then keeps running.
    Waiting for the outer timeout makes the workbench look frozen and can trigger
    expensive downstream retries.  The child may write either the final JSON or
    a `.tmp` JSON before an atomic move; stable partial JSON is therefore checked
    in both places.  Broken JSON is ignored because the child may still be
    writing it.
    """
    for path in (
        product_dir / "output/ozon-ecommerce-design.json",
        product_dir / "output/ozon-ecommerce-design.json.tmp",
    ):
        if not path.is_file():
            continue
        try:
            age_seconds = time.time() - path.stat().st_mtime
        except OSError:
            continue
        try:
            design = load_json(path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(design, dict):
            continue
        is_temporary = path.name.endswith(".tmp")
        suffix = " temporary" if is_temporary else ""
        if not design:
            if not is_temporary and age_seconds >= quiet_seconds:
                return f"ecommerce_design wrote an empty{suffix} JSON artifact; retrying designer from source facts"
            continue
        processing = design.get("processing") if isinstance(design.get("processing"), dict) else {}
        claims_done = processing.get("status") in {"completed", "done", "DONE"}
        has_image_arrays = "main_images" in design or "detail_images" in design
        if not claims_done and not has_image_arrays:
            continue
        try:
            from ozon_ecommerce_designer_contract import validate_design
            errors = validate_design(product_dir, design)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            if age_seconds < quiet_seconds:
                continue
            return f"ecommerce_design wrote an invalid{suffix} artifact: {exc}"
        if errors and age_seconds >= quiet_seconds:
            # 2026-08-15：去掉 claims_done 秒杀 —— 声称完成但校验失败的设计
            # 也可能是代理仍在自我修正；给足 quiet_seconds 窗口再判死。
            # 空产物与坏 JSON 分支同样遵守窗口，避免 30 秒一次的无限重试循环。
            first = str(errors[0])
            more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
            return f"ecommerce_design wrote an incomplete{suffix} artifact: {first}{more}"
    return None


def ecommerce_design_revision_requested(product_dir: Path) -> bool:
    request_path = product_dir / "output/image-design-revision-request.json"
    if not request_path.is_file():
        return False
    design_path = product_dir / "output/ozon-ecommerce-design.json"
    if design_path.is_file() and ecommerce_design_artifact_is_complete(product_dir, design_path):
        try:
            if design_path.stat().st_mtime >= request_path.stat().st_mtime:
                return False
        except OSError:
            pass
    try:
        request = load_json(request_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return True
    if request.get("product_id") not in {None, product_dir.name}:
        return False
    source = str(request.get("source") or "").strip()
    return source in {"image_qc", "visual_director", "user_visual_director"}


def promote_complete_ecommerce_design_tmp(product_dir: Path) -> None:
    """Promote a fully valid `ozon-ecommerce-design.json.tmp` to the final name.

    2026-08-15：Codex 会话偶尔把完整设计写入 `.tmp` 后忘记原子改名，导致完成
    检查永远等不到正式文件、尝试空转到超时。这里在完成检查前把校验通过的
    `.tmp` 晋升为正式产物；无效或空的 `.tmp` 保持原样（由失败检测处理）。
    """
    final = product_dir / "output/ozon-ecommerce-design.json"
    tmp = product_dir / "output/ozon-ecommerce-design.json.tmp"
    if final.is_file() or not tmp.is_file():
        return
    try:
        design = load_json(tmp)
        if not isinstance(design, dict) or not design:
            return
        from ozon_ecommerce_designer_contract import validate_design
        if validate_design(product_dir, design):
            return
        os.replace(tmp, final)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass


def restore_latest_complete_ecommerce_design(product_dir: Path) -> bool:
    """Restore the newest valid archived ecommerce design after a bad retry.

    Connected Codex can occasionally leave an empty or partial artifact while
    the previous completed design has already been moved to stale-artifacts.
    The pipeline should recover that verified checkpoint instead of looping.
    """
    if ecommerce_design_revision_requested(product_dir):
        return False
    current = product_dir / "output/ozon-ecommerce-design.json"
    if ecommerce_design_output_is_complete(product_dir):
        return True
    archive_dir = product_dir / "output/stale-artifacts"
    candidates = sorted(
        archive_dir.glob("ozon-ecommerce-design-*.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for candidate in candidates:
        if not ecommerce_design_artifact_is_complete(product_dir, candidate):
            continue
        shutil.copy2(candidate, current)
        (product_dir / "output/image-design-revision-request.json").unlink(missing_ok=True)
        status_path = product_dir / "status.json"
        try:
            status = load_json(status_path)
            status.setdefault("warnings", []).append(
                f"已恢复最近一次有效电商设计：{candidate.relative_to(product_dir)}"
            )
            status.setdefault("history", []).append({
                "from": "ecommerce_design_archive",
                "to": "restored",
                "at": now(),
                "reason": "Current ecommerce design was empty or incomplete; restored the latest validated archive.",
                "path": str(candidate.relative_to(product_dir)),
            })
            write_json_atomic(status_path, status)
        except Exception:
            pass
        return True
    return False


def archive_unusable_ecommerce_design(product_dir: Path) -> bool:
    """Move only unusable or explicitly rejected design artifacts aside.

    A concrete attribute hash mismatch is no longer a rewind contract. The
    validator checks real attribute IDs and dictionary values instead, so a
    complete design stays usable unless it is empty, incomplete, or the user/QC
    explicitly requested visual revision.
    """
    path = product_dir / "output/ozon-ecommerce-design.json"
    force_archive = ecommerce_design_revision_requested(product_dir)
    if not path.is_file() or (ecommerce_design_output_is_complete(product_dir) and not force_archive):
        return False
    archive_dir = product_dir / "output/stale-artifacts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"ozon-ecommerce-design-{int(time.time())}.json"
    if target.exists():
        target = archive_dir / f"ozon-ecommerce-design-{time.time_ns()}.json"
    shutil.move(str(path), str(target))
    status_path = product_dir / "status.json"
    try:
        status = load_json(status_path)
        status.setdefault("warnings", []).append(
            f"已归档无法继续使用的电商设计：{target.relative_to(product_dir)}"
        )
        status.setdefault("history", []).append({
            "from": "ecommerce_design_archive",
            "to": "archived",
            "at": now(),
            "reason": "Existing ecommerce design was empty, incomplete, or explicitly requested for visual revision.",
            "path": str(target.relative_to(product_dir)),
        })
        write_json_atomic(status_path, status)
    except Exception:
        pass
    return True


def codex_worker_unavailable(log_path: Path, start_offset: int = 0) -> bool:
    """Recognize temporary Codex transport/service failures that must pause the step."""
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(max(0, int(start_offset)))
            text = handle.read()[-16000:].casefold()
    except OSError:
        return False
    signals = (
        "403 forbidden", "failed to connect to websocket", "unexpected status 403",
        "codex executable is unavailable", "transport channel closed",
        "error: reconnecting", "429 too many requests", "unexpected status 429",
        "rate limit", "502 bad gateway", "503 service unavailable",
        "504 gateway timeout", "connection reset by peer", "connection refused",
        "you've hit your usage limit", "you have hit your usage limit",
        "purchase more credits", "upgrade to pro",
    )
    return any(signal in text for signal in signals)


def codex_usage_limit_reached(log_path: Path, start_offset: int = 0) -> bool:
    """Return true only for exhausted-account capacity, not short transport outages."""
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(max(0, int(start_offset)))
            text = handle.read()[-16000:].casefold()
    except OSError:
        return False
    return any(signal in text for signal in (
        "you've hit your usage limit", "you have hit your usage limit",
        "purchase more credits", "upgrade to pro",
    ))


def codex_retry_remaining_seconds(
    status: Dict[str, Any],
    current_time: Optional[datetime] = None,
) -> float:
    """Return the remaining service-backoff delay without treating it as a product error."""
    if str(status.get("ai_service_state") or "normal") != "waiting_for_recovery":
        return 0.0
    value = str(status.get("ai_service_retry_after") or "")
    if not value or value == "unknown":
        return 0.0
    try:
        retry_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    current = current_time or datetime.now(timezone.utc)
    return max(0.0, (retry_at.astimezone(timezone.utc) - current.astimezone(timezone.utc)).total_seconds())


def mark_codex_service_waiting(
    product_dir: Path,
    step: str,
    settings: Dict[str, Any],
    service_reason: str = "temporary_outage",
) -> Dict[str, Any]:
    """Pause on the same checkpoint and schedule a later online-model retry."""
    path = product_dir / "status.json"
    status = load_json(path)
    previous_state = str(status.get("ai_service_state") or "normal")
    capacity_limited = service_reason == "usage_limit"
    delay_key = "codex_usage_limit_retry_seconds" if capacity_limited else "codex_outage_retry_seconds"
    delay = max(10, int(settings.get(delay_key, 600 if capacity_limited else 30)))
    if service_reason == "long_running_timeout":
        delay = max(60, int(settings.get("codex_long_design_retry_seconds", 120)))
    elif service_reason == "early_artifact_failure":
        delay = max(20, int(settings.get("codex_design_contract_retry_seconds", 30)))
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    if capacity_limited:
        wait_message = (
            f"联网大模型额度暂时不可用，任务已停在{step}并将在约{delay // 60}分钟后自动检查；"
            "商品断点和已完成结果均已保留。"
        )
    elif service_reason == "long_running_timeout":
        wait_message = (
            f"电商设计生成超过本轮等待窗口，系统已保留断点并将在约{delay}秒后自动重试；"
            "这不是商品资料错误，无需人工处理。"
        )
    elif service_reason == "early_artifact_failure":
        wait_message = (
            f"电商设计连续输出不完整结果，系统已停止空转并将在约{delay}秒后从断点重试；"
            "这不是商品资料错误，无需重新采集。"
        )
    else:
        wait_message = (
            f"联网大模型暂时不可用，任务已停在{step}并将在约{delay}秒后自动重试；无需人工处理。"
        )
    status.update({
        "status": "PROCESSING",
        "current_step": step,
        "next_action": step,
        "active_step": None,
        "failed_step": "unknown",
        "error_code": (
            "AI_SERVICE_CAPACITY_WAIT"
            if capacity_limited else
            "AI_DESIGN_LONG_RUNNING_WAIT"
            if service_reason == "long_running_timeout" else
            "AI_DESIGN_CONTRACT_WAIT"
            if service_reason == "early_artifact_failure" else
            "AI_SERVICE_TEMPORARILY_UNAVAILABLE"
        ),
        "error_message": wait_message,
        "last_run_at": now(),
        "ai_service_state": "waiting_for_recovery",
        "ai_service_reason": (
            "codex_usage_limit"
            if capacity_limited else
            "codex_long_running_design"
            if service_reason == "long_running_timeout" else
            "codex_incomplete_design_artifact"
            if service_reason == "early_artifact_failure" else
            "codex_transport_or_service_unavailable"
        ),
        "ai_service_retry_after": retry_at.isoformat(timespec="seconds"),
        "ai_service_retry_count": int(status.get("ai_service_retry_count") or 0) + 1,
    })
    if previous_state != "waiting_for_recovery":
        status.setdefault("warnings", []).append(
            "联网大模型额度暂时不可用；系统已暂停当前步骤，额度恢复后自动从断点继续。"
            if capacity_limited else
            "电商设计本轮耗时过长；系统已暂停当前步骤，稍后自动重试，不把商品标为资料错误。"
            if service_reason == "long_running_timeout" else
            "电商设计连续输出不完整结果；系统已暂停空转并保留断点，稍后自动重试。"
            if service_reason == "early_artifact_failure" else
            "联网大模型暂时不可用；系统已暂停当前步骤，恢复后自动从断点继续，不启用本地备用分析。"
        )
    write_json_atomic(path, status)
    return status


def clear_codex_service_waiting(product_dir: Path) -> None:
    path = product_dir / "status.json"
    status = load_json(path)
    if str(status.get("ai_service_state") or "normal") != "waiting_for_recovery":
        return
    status.update({
        "ai_service_state": "normal",
        "ai_service_reason": "unknown",
        "ai_service_retry_after": "unknown",
        "error_code": "unknown",
        "error_message": "unknown",
        "active_step": None,
        "last_run_at": now(),
    })
    write_json_atomic(path, status)


def russian_copy_quality_errors(
    copy_value: Dict[str, Any],
    content_value: Dict[str, Any],
    keyword_value: Dict[str, Any],
) -> List[str]:
    """Keep only machine-required copy blockers.

    Copy quality is now owned by ozon-ecommerce-designer prompts.  Paragraph
    count, selling-point count and keyword provenance are prompt/design issues
    and must trigger regeneration when useful, not strand the whole product as
    a hard contract blocker.  The remaining hard rule is the Ozon-facing tag
    format because it is deterministic and can be auto-normalized upstream.
    """
    errors: List[str] = []
    hashtags = content_value.get("hashtags_ru") or []
    valid_hashtags = (
        isinstance(hashtags, list)
        and len(hashtags) <= 30
        and len({str(value).casefold() for value in hashtags}) == len(hashtags)
        and all(
                re.fullmatch(r"#[А-Яа-яЁё]+", str(value).strip())
                and 3 <= len(str(value).strip()) <= 30
                for value in hashtags
            )
        )
    if not valid_hashtags:
        errors.append("Russian copy must contain at most 30 valid unique hashtags")
    return errors


def russian_copy_output_is_complete(product_dir: Path) -> bool:
    """Accept the Russian-copy step as soon as all three validated artifacts exist."""
    output_dir = product_dir / "output"
    copy_path = output_dir / "copy-ru.json"
    keyword_path = output_dir / "keyword-research-ru.json"
    required_paths = [copy_path, keyword_path]
    if not all(path.is_file() for path in required_paths):
        return False
    try:
        copy_value = load_json(copy_path)
        keyword_value = load_json(keyword_path)
        copy_schema = load_json(ROOT / "templates/copy-ru.schema.json")
        keyword_schema = load_json(ROOT / "templates/keyword-research-ru.schema.json")

        copy_errors = list(Draft202012Validator(copy_schema).iter_errors(copy_value))
        keyword_errors = list(Draft202012Validator(keyword_schema).iter_errors(keyword_value))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False
    quality_errors = russian_copy_quality_errors(copy_value, copy_value, keyword_value)
    return (
        not copy_errors
        and not keyword_errors
        and not quality_errors
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


def image_slot_result_path(product_dir: Path, slot: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(slot)).strip("-") or "unknown"
    return product_dir / "output/image-slot-results" / f"{safe}.json"


def resolve_planned_product_path(product_dir: Path, path_value: str) -> Path:
    """Resolve a product artifact path without assuming products live below ROOT."""
    raw = str(path_value or "").strip()
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


def ensure_path_under(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    allowed = root.resolve()
    if not resolved.is_relative_to(allowed):
        raise ValueError(f"{label}路径越界：{resolved}")
    return resolved


def ensure_slot_output_path(product_dir: Path, output_path: str) -> Path:
    resolved = resolve_planned_product_path(product_dir, output_path)
    return ensure_path_under(resolved, product_dir / "output/generated-images", "图位输出")


def ensure_slot_receipt_path(product_dir: Path, result_path: Path) -> Path:
    return ensure_path_under(result_path, product_dir / "output/image-slot-results", "图位结果回执")


def receipt_has_builtin_image_source(receipt: Dict[str, Any]) -> bool:
    return (
        str(receipt.get("generation_source") or "").strip() == BUILT_IN_IMAGE_GENERATION_SOURCE
        and receipt.get("designer_prompt_followed") is True
        and receipt.get("local_script_generation") is False
    )


def image_slot_is_main(slot: Any) -> bool:
    return str(slot or "").strip().startswith("main-")


def receipt_visual_acceptance_passes(receipt: Dict[str, Any], slot: Any) -> bool:
    if not image_slot_is_main(slot):
        return True
    # Visual acceptance is a design-quality note. Do not make subjective
    # ecommerce taste a regeneration gate after the paid image already exists.
    # Factual and technical failures still block through receipt.status,
    # hard_failures, fact-lock checks and image QC.
    acceptance = receipt.get("visual_acceptance")
    if not isinstance(acceptance, dict):
        return True
    checks = acceptance.get("checks")
    if not isinstance(checks, dict):
        return True
    factual_checks = ("product_visually_dominant",)
    return all(checks.get(key) is not False for key in factual_checks)


def image_slot_worker_path(product_dir: Path, slot: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(slot)).strip("-") or "unknown"
    return ROOT / "logs/image-slot-workers" / f"{product_dir.name}--{safe}.json"


def image_slot_log_path(product_dir: Path, slot: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(slot)).strip("-") or "unknown"
    return product_dir / "logs/image-slots" / f"{safe}.log"


def image_slot_prompt(product_dir: Path, slot_item: Dict[str, Any], attempt: int) -> str:
    slot = str(slot_item.get("slot") or "")
    result_path = ensure_slot_receipt_path(product_dir, image_slot_result_path(product_dir, slot))
    output_path = str(slot_item.get("output_path") or "")
    resolved_output = ensure_slot_output_path(product_dir, output_path)
    fact_lock_path = product_dir / "output/product-fact-lock.json"
    fact_lock = load_json(fact_lock_path) if fact_lock_path.is_file() else {}
    fact_lock_hash = str(fact_lock.get("lock_hash") or "")
    visual_contract = "主图需记录visual_acceptance质量观察。" if image_slot_is_main(slot) else ""
    return (
        f"调用$image-generator：product_id={product_dir.name}，slot={slot}，attempt={attempt}。"
        "只读image-plan该slot和product-fact-lock；不分析、不改计划、不碰其他图位。"
        "只画russian_text/overlay_plan白名单俄文；尺寸图可把同一组组合尺寸拆成同源尺寸线；商品/SKU结构、颜色、规格、配件不变；"
        "参考图只用当前商品input；信息图必须解释真实产品证明，禁止提示词标签上图。"
        f"{visual_contract}"
        f"输出3:4 PNG到{output_path}，回执写{result_path}；不得覆盖input或调用Ozon。"
        f'回执output_path="{output_path}"，不得写绝对路径；'
        f'fact_lock_checked={str(bool(fact_lock_hash)).lower()}，fact_lock_hash="{fact_lock_hash}"，generation_source="{BUILT_IN_IMAGE_GENERATION_SOURCE}"，designer_prompt_followed=true，'
        f'local_script_generation=false。校验路径：{resolved_output}。只输出DONE {slot}。'
    )


def image_slot_attempt_for(product_dir: Path, slot: str, fallback: int = 1) -> int:
    status_path = product_dir / "status.json"
    try:
        status = load_json(status_path) if status_path.is_file() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        status = {}
    retry_counts = status.get("image_slot_retry_count_by_slot") or {}
    try:
        recorded = int(retry_counts.get(slot) or 0)
    except (TypeError, ValueError):
        recorded = 0
    if recorded:
        receipt_missing = not image_slot_result_path(product_dir, slot).is_file()
        output_missing = True
        try:
            plan = load_json(product_dir / "output/image-plan.json")
            for collection in ("main_images", "detail_images", "disclaimer_images"):
                for item in plan.get(collection) or []:
                    if str(item.get("slot") or "") == slot:
                        output_missing = not ensure_slot_output_path(product_dir, str(item.get("output_path") or "")).is_file()
                        raise StopIteration
        except StopIteration:
            pass
        except (OSError, ValueError, json.JSONDecodeError):
            output_missing = True
        if receipt_missing and output_missing:
            recorded = 0
    return max(1, int(fallback), recorded + 1)


def image_slot_stall_seconds(settings: Dict[str, Any]) -> int:
    """Bound one slot invocation; the parent owns targeted retries."""
    value = (
        settings.get("image_slot_stall_seconds")
        or settings.get("image_generation_stall_seconds")
        or 300
    )
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 300
    return max(180, min(seconds, 600))


def receipt_output_matches(
    product_dir: Path,
    receipt_output_path: Any,
    expected_relative: str,
    expected_output: Path,
) -> bool:
    """Accept the planned relative path and its exact in-product absolute form."""
    raw = str(receipt_output_path or "").strip()
    if raw == expected_relative:
        return True
    try:
        return ensure_slot_output_path(product_dir, raw) == expected_output
    except ValueError:
        return False


def validate_image_slot_result(
    product_dir: Path,
    slot_item: Dict[str, Any],
    attempt: int,
) -> Dict[str, Any]:
    slot = str(slot_item.get("slot") or "")
    expected_relative = str(slot_item.get("output_path") or "")
    if not expected_relative:
        return {"slot": slot, "status": "failed", "attempt": attempt, "error": "图位输出路径越界"}
    try:
        expected_output = ensure_slot_output_path(product_dir, expected_relative)
        result_path = ensure_slot_receipt_path(product_dir, image_slot_result_path(product_dir, slot))
    except ValueError as exc:
        return {"slot": slot, "status": "failed", "attempt": attempt, "error": str(exc)}
    if not result_path.is_file() or not expected_output.is_file():
        return {"slot": slot, "status": "failed", "attempt": attempt, "error": "未生成图片或独立结果清单"}
    try:
        receipt = load_json(result_path)
        width, height = read_png_size(expected_output)
        digest = image_file_sha256(expected_output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"slot": slot, "status": "failed", "attempt": attempt, "error": f"图片或结果不可读：{exc}"}
    errors = []
    if receipt.get("product_id") != product_dir.name or str(receipt.get("slot") or "") != slot:
        errors.append("结果清单商品或图位不一致")
    if not receipt_output_matches(product_dir, receipt.get("output_path"), expected_relative, expected_output):
        errors.append("结果清单输出路径不一致")
    if str(receipt.get("status") or "").strip().lower() != "pass":
        errors.append("图位硬检查未通过")
    if receipt.get("hard_failures"):
        errors.append("图位仍有硬错误")
    if not receipt_has_builtin_image_source(receipt):
        errors.append("图片不是按设计师提示词由内置生图工具生成，禁止参考图搬运或本地脚本叠字")
    if not receipt_visual_acceptance_passes(receipt, slot):
        errors.append("主图视觉验收未通过：商品必须占主导，文字不能像海报贴词或压过商品")
    fact_lock_path = product_dir / "output/product-fact-lock.json"
    fact_lock = load_json(fact_lock_path) if fact_lock_path.is_file() else {}
    expected_fact_lock_hash = str(fact_lock.get("lock_hash") or "")
    if expected_fact_lock_hash and (receipt.get("fact_lock_checked") is not True or str(receipt.get("fact_lock_hash") or "") != expected_fact_lock_hash):
        errors.append("图片未按当前锁定SKU事实完成身份核验")
    if str(receipt.get("sha256") or "") != digest:
        errors.append("图片在检查后发生变化")
    if width < 900 or height < 1200 or abs((width / height) - 0.75) > 0.02:
        errors.append(f"图片尺寸或3:4比例不合格：{width}x{height}")
    if errors:
        return {
            "slot": slot,
            "status": "failed",
            "attempt": attempt,
            "error": "；".join(errors),
            # Feed the isolated worker's concrete hard-check findings back to
            # the planner. Retrying the original prompt would repeat the same
            # product-identity mistake and waste another image request.
            "hard_failures": [str(value) for value in (receipt.get("hard_failures") or []) if str(value).strip()],
        }
    gate_entry = {
        "slot": slot,
        "output_path": expected_relative,
        "status": "PASS",
        "retry_count": max(0, attempt - 1),
        "sha256": digest,
        "dimensions": {"width": width, "height": height},
        "generation_source": BUILT_IN_IMAGE_GENERATION_SOURCE,
        "designer_prompt_followed": True,
        "local_script_generation": False,
        "checked_at": str(receipt.get("checked_at") or now()),
    }
    if image_slot_is_main(slot):
        gate_entry["visual_acceptance"] = receipt.get("visual_acceptance")
    return {
        "slot": slot,
        "status": "passed",
        "attempt": attempt,
        "output_path": expected_relative,
        "hard_gate_entry": gate_entry,
    }


def image_slot_receipt_gate_entry(
    product_dir: Path,
    slot: str,
    expected_relative: str,
) -> Dict[str, Any] | None:
    """Return a hard-gate entry from a valid isolated PASS receipt.

    This repairs interrupted parent runs: a child can finish a slot and write
    its receipt before the parent merges it into ``image-hard-gate.json``.
    The image plus matching receipt is still completed work and must be
    preserved instead of regenerated.
    """
    if not slot or not expected_relative:
        return None
    try:
        expected_output = ensure_slot_output_path(product_dir, expected_relative)
        result_path = ensure_slot_receipt_path(product_dir, image_slot_result_path(product_dir, slot))
    except ValueError:
        return None
    if not result_path.is_file() or not expected_output.is_file():
        return None
    try:
        receipt = load_json(result_path)
        width, height = read_png_size(expected_output)
        digest = image_file_sha256(expected_output)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if receipt.get("product_id") != product_dir.name or str(receipt.get("slot") or "") != str(slot):
        return None
    if str(receipt.get("status") or "").strip().lower() != "pass" or receipt.get("hard_failures"):
        return None
    if not receipt_has_builtin_image_source(receipt):
        return None
    if not receipt_visual_acceptance_passes(receipt, slot):
        return None
    fact_lock_path = product_dir / "output/product-fact-lock.json"
    fact_lock = load_json(fact_lock_path) if fact_lock_path.is_file() else {}
    expected_fact_lock_hash = str(fact_lock.get("lock_hash") or "")
    if expected_fact_lock_hash and (receipt.get("fact_lock_checked") is not True or str(receipt.get("fact_lock_hash") or "") != expected_fact_lock_hash):
        return None
    if not receipt_output_matches(product_dir, receipt.get("output_path"), expected_relative, expected_output):
        return None
    if str(receipt.get("sha256") or "") != digest:
        return None
    if width < 900 or height < 1200 or abs((width / height) - 0.75) > 0.02:
        return None
    return {
        "slot": slot,
        "output_path": expected_relative,
        "status": "PASS",
        "retry_count": max(0, int(receipt.get("attempt") or 1) - 1),
        "sha256": digest,
        "dimensions": {"width": width, "height": height},
        "generation_source": BUILT_IN_IMAGE_GENERATION_SOURCE,
        "designer_prompt_followed": True,
        "local_script_generation": False,
        "checked_at": str(receipt.get("checked_at") or now()),
        **({"visual_acceptance": receipt.get("visual_acceptance")} if image_slot_is_main(slot) else {}),
    }


def archive_failed_image_slot(product_dir: Path, slot_item: Dict[str, Any], attempt: int) -> None:
    """Move only the failed attempt; never touch another slot or any input image."""
    slot = str(slot_item.get("slot") or "unknown")
    image_slot_result_path(product_dir, slot).unlink(missing_ok=True)
    if attempt <= 1:
        return
    try:
        output_path = ensure_slot_output_path(product_dir, str(slot_item.get("output_path") or ""))
    except ValueError:
        return
    if not output_path.is_file():
        return
    rejected = product_dir / "output/rejected-generation"
    rejected.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", slot).strip("-") or "unknown"
    target = rejected / f"{safe}-attempt-{attempt - 1}{output_path.suffix.lower() or '.png'}"
    if target.exists():
        target = rejected / f"{safe}-attempt-{attempt - 1}-{time.time_ns()}{output_path.suffix.lower() or '.png'}"
    shutil.move(str(output_path), str(target))


def update_image_plan_from_results(product_dir: Path, results: List[Dict[str, Any]]) -> None:
    plan_path = product_dir / "output/image-plan.json"
    plan = load_json(plan_path)
    by_slot = {str(item.get("slot")): item for item in results}
    for key in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(key) or []:
            result = by_slot.get(str(item.get("slot") or ""))
            if not result:
                continue
            if result.get("status") == "passed":
                item["status"] = "generated"
                item["failure_reason"] = "unknown"
                item["generation_attempts"] = int(result.get("attempt") or 1)
            elif result.get("status") in {"failed", "prelaunch_failure"}:
                item["status"] = "needs_review"
                item["failure_reason"] = str(result.get("error") or "image_slot_failed")
    write_json_atomic(plan_path, plan)
    status_path = product_dir / "status.json"
    if status_path.is_file():
        status = load_json(status_path)
        retry_counts = status.setdefault("image_slot_retry_count_by_slot", {})
        service_wait_counts = status.setdefault("image_slot_service_wait_count_by_slot", {})
        for result in results:
            slot = str(result.get("slot") or "").strip()
            if slot:
                retry_counts[slot] = max(
                    int(retry_counts.get(slot) or 0),
                    max(0, int(result.get("attempt") or 1) - 1),
                )
                if result.get("status") == "service_unavailable":
                    service_wait_counts[slot] = int(service_wait_counts.get(slot) or 0) + 1
        write_json_atomic(status_path, status)


def merge_parallel_image_hard_gate(product_dir: Path, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge isolated slot receipts after a wave; children never race on this file."""
    plan = load_json(product_dir / "output/image-plan.json")
    planned = [
        item for key in ("main_images", "detail_images", "disclaimer_images")
        for item in (plan.get(key) or [])
    ]
    gate_path = product_dir / "output/image-hard-gate.json"
    existing = load_json(gate_path) if gate_path.is_file() else {}
    current: Dict[str, Dict[str, Any]] = {}
    for entry in existing.get("checked_slots") or []:
        slot = str(entry.get("slot") or "")
        try:
            output = ensure_slot_output_path(product_dir, str(entry.get("output_path") or ""))
        except ValueError:
            continue
        try:
            if (
                slot
                and str(entry.get("status") or "").strip().lower() == "pass"
                and receipt_has_builtin_image_source(entry)
                and output.is_file()
                and str(entry.get("sha256") or "") == image_file_sha256(output)
            ):
                current[slot] = entry
        except OSError:
            continue
    for item in planned:
        slot = str(item.get("slot") or "")
        receipt_entry = image_slot_receipt_gate_entry(product_dir, slot, str(item.get("output_path") or ""))
        if receipt_entry:
            current[slot] = receipt_entry
    for result in results:
        if result.get("status") == "passed" and isinstance(result.get("hard_gate_entry"), dict):
            current[str(result.get("slot"))] = result["hard_gate_entry"]
    checked = [current[str(item.get("slot"))] for item in planned if str(item.get("slot")) in current]
    value = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "mode": "hard_failures_only",
        "executor": "true_parallel_slot_waves",
        "checked_slots": checked,
        "critical_failures": [],
        "issues": existing.get("issues") or [],
        "checked_at": now(),
    }
    write_json_atomic(gate_path, value)
    return value


def update_image_wave_status(
    product_dir: Path,
    wave_index: int,
    slots: List[Dict[str, Any]],
    active: bool,
) -> None:
    with _batch_write_lock:
        status_path = product_dir / "status.json"
        status = load_json(status_path)
        status["image_parallelism"] = 3
        status["image_wave"] = wave_index
        status["active_image_slots"] = [str(item.get("slot")) for item in slots] if active else []
        status["last_run_at"] = now()
        write_json_atomic(status_path, status)


def run_single_image_slot(
    product_dir: Path,
    settings: Dict[str, Any],
    slot_item: Dict[str, Any],
    attempt: int,
) -> Dict[str, Any]:
    slot = str(slot_item.get("slot") or "unknown")
    attempt = image_slot_attempt_for(product_dir, slot, attempt)
    expected_relative = str(slot_item.get("output_path") or "")
    existing_gate = image_slot_receipt_gate_entry(product_dir, slot, expected_relative)
    if existing_gate:
        return {
            "slot": slot,
            "status": "passed",
            "attempt": max(1, int(existing_gate.get("retry_count") or 0) + 1),
            "output_path": expected_relative,
            "hard_gate_entry": existing_gate,
            "reused_checkpoint": True,
        }
    receipt = image_slot_result_path(product_dir, slot)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.unlink(missing_ok=True)
    log_path = image_slot_log_path(product_dir, slot)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_offset = log_path.stat().st_size if log_path.is_file() else 0
    try:
        prompt = image_slot_prompt(product_dir, slot_item, attempt)
        command = codex_exec_command(settings, "image_generation", prompt)
    except Exception as exc:
        message = f"{now()} [prelaunch_failure] {type(exc).__name__}: {exc}\n"
        with log_path.open("a", encoding="utf-8") as output:
            output.write(message)
        return {
            "slot": slot,
            "status": "prelaunch_failure",
            "attempt": attempt,
            "error": f"{type(exc).__name__}: {exc}",
        }
    def slot_receipt_ready() -> bool:
        if receipt.is_file():
            return True
        return False

    try:
        with log_path.open("a", encoding="utf-8") as output, _image_slot_semaphore:
            completed = run_registered_process(
                command,
                product_dir,
                output,
                product_step_timeout(product_dir, settings, "image_generation"),
                codex_worker_env(settings),
                completion_check=slot_receipt_ready,
                completion_poll_seconds=float(settings.get("artifact_poll_interval_seconds", 0.5)),
                stall_seconds=image_slot_stall_seconds(settings),
                worker_path_override=image_slot_worker_path(product_dir, slot),
            )
    except BatchSafeStopRequested:
        raise
    except subprocess.TimeoutExpired as exc:
        return {"slot": slot, "status": "failed", "attempt": attempt, "error": f"图位生成超时：{exc.timeout}秒"}
    except OSError as exc:
        return {"slot": slot, "status": "service_unavailable", "attempt": attempt, "error": str(exc)}
    result = validate_image_slot_result(product_dir, slot_item, attempt)
    if result.get("status") != "passed" and codex_worker_unavailable(log_path, log_offset):
        result["status"] = "service_unavailable"
        result["service_reason"] = (
            "usage_limit" if codex_usage_limit_reached(log_path, log_offset) else "temporary_outage"
        )
    return result


def run_parallel_image_generation(
    product_dir: Path,
    settings: Dict[str, Any],
    log_path: Path,
) -> Dict[str, Any]:
    """Replace the former one-Codex fake wave with up to three real workers."""
    product_dir = product_dir.resolve()
    log_path = log_path.resolve()
    plan_path = product_dir / "output/image-plan.json"
    plan = load_json(plan_path)
    contract = plan.setdefault("generator_contract", {})
    contract["image_slot_concurrency"] = max(1, min(int(settings.get("image_slot_concurrency", 3)), 3))
    contract["true_parallel_slot_executor"] = True
    write_json_atomic(plan_path, plan)
    request_path = product_dir / "output/image-regeneration-request.json"
    requested_slots = set()
    if request_path.is_file():
        requested_slots = requested_image_slots_from_request(load_json(request_path))

    all_wave_results: List[Dict[str, Any]] = []

    def before_attempt(slot_item: Dict[str, Any], attempt: int) -> None:
        if attempt > 1 or str(slot_item.get("slot")) in requested_slots:
            archive_failed_image_slot(product_dir, slot_item, 2 if attempt == 1 else attempt)

    def after_wave(wave_index: int, slots: List[Dict[str, Any]], results: List[Dict[str, Any]]) -> None:
        all_wave_results.extend(results)
        update_image_plan_from_results(product_dir, results)
        if requested_slots:
            with _batch_write_lock:
                status_path = product_dir / "status.json"
                status = load_json(status_path)
                retry_counts = status.setdefault("image_slot_retry_count_by_slot", {})
                for result in results:
                    slot = str(result.get("slot") or "")
                    if slot in requested_slots:
                        retry_counts[slot] = int(retry_counts.get(slot) or 0) + 1
                write_json_atomic(status_path, status)
        merge_parallel_image_hard_gate(product_dir, all_wave_results)
        update_image_wave_status(product_dir, wave_index, slots, False)
        refresh_live_image_progress(product_dir)
        with log_path.open("a", encoding="utf-8") as output:
            summary = ", ".join(f"{item.get('slot')}={item.get('status')}" for item in results)
            output.write(f"\n[image-wave {wave_index}] {summary}\n")

    def before_wave(wave_index: int, slots: List[Dict[str, Any]], attempt: int) -> None:
        update_image_wave_status(product_dir, wave_index, slots, True)
        with log_path.open("a", encoding="utf-8") as output:
            names = ", ".join(str(item.get("slot")) for item in slots)
            output.write(f"\n[image-wave {wave_index} attempt {attempt}] concurrent start: {names}\n")

    def runner(slot_item: Dict[str, Any], attempt: int) -> Dict[str, Any]:
        return run_single_image_slot(product_dir, settings, slot_item, attempt)

    concurrency = max(1, min(int(settings.get("image_slot_concurrency", 3)), 3))
    initialize_image_generation_progress(product_dir, concurrency)
    requested_retry_limit = max(1, int(settings.get("requested_image_slot_max_attempts", 2)))
    result = execute_image_slot_waves(
        product_dir,
        concurrency,
        runner,
        max_attempts=requested_retry_limit if requested_slots else 2,
        before_attempt=before_attempt,
        before_wave=before_wave,
        after_wave=after_wave,
    )
    if (
        requested_slots
        and not result.get("failed")
        and not result.get("service_unavailable")
        and not result.get("prelaunch_failure")
    ):
        request_path.unlink(missing_ok=True)
        remaining = pending_slots(product_dir, concurrency)
        if int(remaining.get("pending_slot_count") or 0) > 0:
            with log_path.open("a", encoding="utf-8") as output:
                output.write(
                    "\n[image-recovery] requested slots completed; "
                    f"continuing {remaining.get('pending_slot_count')} remaining slots before QC.\n"
                )
            continuation = execute_image_slot_waves(
                product_dir,
                concurrency,
                runner,
                max_attempts=2,
                before_attempt=before_attempt,
                before_wave=before_wave,
                after_wave=after_wave,
            )
            for key in ("passed", "failed", "service_unavailable", "prelaunch_failure", "results"):
                result[key] = [*(result.get(key) or []), *(continuation.get(key) or [])]
            result["wave_count"] = int(result.get("wave_count") or 0) + int(continuation.get("wave_count") or 0)
            result["pending_slot_count"] = int(result.get("pending_slot_count") or 0) + int(
                continuation.get("pending_slot_count") or 0
            )
    update_image_wave_status(product_dir, int(result.get("wave_count") or 0), [], False)
    if not result.get("failed") and not result.get("service_unavailable") and not result.get("prelaunch_failure"):
        request_path.unlink(missing_ok=True)
    return result


def finish_image_generation_step(
    product_dir: Path,
    settings: Dict[str, Any],
    status: Dict[str, Any],
    started: float,
    cache_key: str,
    log_path: Path,
    image_slot_concurrency: int,
) -> Dict[str, Any]:
    """Handle run_parallel_image_generation outcomes (extracted from run_one_step)."""
    step = "image_generation"
    parallel = run_parallel_image_generation(product_dir, settings, log_path)
    if parallel.get("service_unavailable"):
        failed_slots = sorted({str(item.get("slot")) for item in parallel["service_unavailable"]})
        service_reason = (
            "usage_limit"
            if any(item.get("service_reason") == "usage_limit" for item in parallel["service_unavailable"])
            else "temporary_outage"
        )
        write_json_atomic(product_dir / "output/image-regeneration-request.json", {
            "product_id": product_dir.name,
            "failed_slots": failed_slots,
            "attempt": int((status.get("retry_count_by_step") or {}).get("image_generation") or 0),
            "reason": "联网Codex生图服务暂时不可用；已保留同波成功图片。",
            "requested_at": now(),
            "preserve_passed_images": True,
            "consume_image_retry": False,
        })
        waiting = mark_codex_service_waiting(product_dir, step, settings, service_reason)
        performance_finish(product_dir, step, started, False, "waiting_for_ai_service")
        return {
            "product_id": product_dir.name,
            "outcome": "waiting_for_ai_service",
            "step": step,
            "retry_after": waiting["ai_service_retry_after"],
            "preserved_slots": [item.get("slot") for item in parallel.get("passed") or []],
        }
    if parallel.get("prelaunch_failure"):
        failed_slots = sorted({str(item.get("slot")) for item in parallel["prelaunch_failure"]})
        write_json_atomic(product_dir / "output/image-regeneration-request.json", {
            "product_id": product_dir.name,
            "requested_slots": failed_slots,
            "failed_slots": failed_slots,
            "attempt": 0,
            "failure_kind": "prelaunch_failure",
            "reason": "图片子任务启动前发生程序错误；未调用图片工具，修复后只恢复这些图位。",
            "requested_at": now(),
            "preserve_passed_images": True,
            "consume_image_retry": False,
        })
        status_after = load_json(product_dir / "status.json")
        status_after.setdefault("retry_count_by_step", {})["image_generation"] = 0
        write_json_atomic(product_dir / "status.json", status_after)
        reason = "图片子任务启动前失败：" + "、".join(failed_slots)
        mark_needs_attention(product_dir, "image_generation", reason)
        performance_finish(product_dir, step, started, False, "image_slot_prelaunch_failure", 0)
        return {
            "product_id": product_dir.name,
            "outcome": "prelaunch_failure",
            "step": step,
            "failed_slots": failed_slots,
            "retry_consumed": False,
        }
    if parallel.get("failed"):
        plan = load_json(product_dir / "output/image-plan.json")
        planned_by_slot = {
            str(item.get("slot")): item
            for key in ("main_images", "detail_images", "disclaimer_images")
            for item in (plan.get(key) or [])
        }
        for failure in parallel["failed"]:
            item = planned_by_slot.get(str(failure.get("slot")))
            if item:
                archive_failed_image_slot(
                    product_dir,
                    item,
                    int(failure.get("attempt") or 2) + 1,
                )
        failed_slots = sorted({str(item.get("slot")) for item in parallel["failed"]})
        reason = "以下图片仍未通过技术检查，已自动重新排队：" + "、".join(failed_slots)
        # A failed receipt is a product/design correction, not a
        # transport retry. Rebuild prompts for failed slots with the
        # precise hard-check feedback before asking the image service
        # again; passed slots remain locked by the hard gate.
        failure_issues = []
        for failure in parallel["failed"]:
            slot = str(failure.get("slot") or "").strip()
            if not slot:
                continue
            messages = [
                str(value) for value in (failure.get("hard_failures") or [])
                if str(value).strip()
            ] or [str(failure.get("error") or "image_slot_failed")]
            failure_issues.append({
                "code": "image_slot_hard_check_failed",
                "severity": "critical",
                "message": "；".join(messages),
                "image_slots": [slot],
            })
        repaired_status = route_image_qc_failures_back_to_image_plan(
            product_dir,
            load_json(product_dir / "status.json"),
            {
                "critical_failures": [issue["code"] for issue in failure_issues],
                "issues": failure_issues,
                "images_checked": [{"slot": slot} for slot in failed_slots],
            },
            reason,
        )
        if repaired_status.get("status") in ATTENTION_STATES:
            performance_finish(product_dir, step, started, False, "failed_slots_retry_limit", 1)
            return {
                "product_id": product_dir.name,
                "outcome": "failed",
                "step": step,
                "failed_slots": failed_slots,
            }
        performance_finish(product_dir, step, started, False, "failed_slots_requeued", 1)
        return {
            "product_id": product_dir.name,
            "outcome": "retry",
            "step": "image_generation",
            "failed_slots": failed_slots,
            "next_action": repaired_status.get("next_action"),
        }
    remaining_slots = schedulable_image_slot_count(product_dir, image_slot_concurrency)
    if remaining_slots > 0:
        repaired_status = route_incomplete_images_back_to_generation(product_dir, load_json(product_dir / "status.json"), settings)
        performance_finish(product_dir, step, started, False, "image_slots_still_pending")
        return {
            "product_id": product_dir.name,
            "outcome": "retry",
            "step": "image_generation",
            "pending_slots": remaining_slots,
            "next_action": repaired_status.get("next_action"),
            "generated_slots": [item.get("slot") for item in parallel.get("passed") or []],
        }
    complete_step(product_dir, "image_generation")
    run_local_step(product_dir, "image_qc", settings, log_path)
    complete_step(product_dir, "image_qc")
    cache_store(product_dir, "image_generation", cache_key)
    cache_store(product_dir, "image_qc", input_hash(product_dir, "image_qc"))
    clear_image_host_recovery(product_dir)
    performance_finish(product_dir, step, started, False, "completed_with_true_parallel_image_qc")
    return {
        "product_id": product_dir.name,
        "outcome": "completed_with_image_qc",
        "step": step,
        "image_parallelism": parallel.get("concurrency"),
        "generated_slots": [item.get("slot") for item in parallel.get("passed") or []],
    }


def run_one_step(product_dir: Path, settings: Dict[str, Any]) -> Dict[str, Any]:
    if product_deleted(product_dir):
        return {"product_id": product_dir.name, "outcome": "deleted"}
    # Every resume path is guarded, not only the first validate_source step.
    # This prevents a stale checkpoint from reading another product, a manual
    # test fixture or files added after the workbench collection snapshot.
    validate_formal_product_input(product_dir)
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
        clear_codex_service_waiting(product_dir)
        status = transition_to_processing(product_dir)
        if status.get("status") in TERMINAL_STATES:
            return {"product_id": product_dir.name, "outcome": "terminal"}
        step = status.get("next_action") or next(
            (item for item in PIPELINE_STEPS if item not in status.get("completed_steps", [])),
            "complete",
        )
        if step == "image_qc":
            repaired_status = route_incomplete_images_back_to_generation(product_dir, status, settings)
            if repaired_status.get("current_step") == "image_generation":
                status = repaired_status
                step = "image_generation"
        if step != "complete":
            status["current_step"] = step
            status["progress"] = max(int(status.get("progress") or 0), STEP_START_PROGRESS.get(step, 2))
            status["active_step"] = {"name": step, "started_at": now()}
            status["last_run_at"] = now()
            write_json_atomic(product_dir / "status.json", status)
        cache_key = input_hash(product_dir, step)
        local_cache_hit = cache_hit(product_dir, step, cache_key)
        # Cross-product analysis reuse is intentionally disabled. Even similar
        # titles/images/capacities belong to different product_id+collection_id
        # boundaries and may not complement each other's facts or attributes.
        shared_cache_key = None
        shared_cache_hit = False
        started = performance_start(product_dir, step, local_cache_hit or shared_cache_hit)
        if local_cache_hit or shared_cache_hit:
            if product_deleted(product_dir):
                raise ProductDeletionRequested(product_dir.name)
            complete_step(product_dir, step)
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
            "ecommerce_design": [product_dir / "output/ozon-ecommerce-design.json"],
            "russian_copy": [
                product_dir / "output/copy-ru.json",
                product_dir / "output/keyword-research-ru.json",
            ],
        }.get(step, [])
        artifact_signatures_before = {
            path: file_signature(path) for path in artifact_paths
        }

        def new_step_artifacts_are_complete() -> bool:
            if step == "ecommerce_design":
                promote_complete_ecommerce_design_tmp(product_dir)
            if not artifact_paths or not any(
                file_signature(path) != artifact_signatures_before[path]
                for path in artifact_paths
            ):
                # Some steps are deterministic projections of an upstream
                # unified artifact.  In normal production ecommerce_design
                # materializes russian_copy-compatible files before the
                # russian_copy checkpoint starts; requiring a post-start file
                # signature change makes Continue appear inert even though the
                # required artifacts are already valid.
                if step == "russian_copy":
                    return russian_copy_output_is_complete(product_dir)
                if step == "ecommerce_design":
                    return restore_latest_complete_ecommerce_design(product_dir)
                return False
            if step == "product_analysis":
                return product_analysis_output_is_complete(product_dir)
            if step == "ecommerce_design":
                return restore_latest_complete_ecommerce_design(product_dir)
            if step == "russian_copy":
                return russian_copy_output_is_complete(product_dir)
            return False
        image_slot_concurrency = max(1, min(int(settings.get("image_slot_concurrency", 3)), 3))
        # Only ecommerce_design delegates to Codex in this path: product_analysis
        # and russian_copy run as deterministic local steps and always return True
        # from run_local_step, so their former delegation prompts were dead code.
        prompt = ""
        if step == "ecommerce_design":
            if restore_latest_complete_ecommerce_design(product_dir):
                complete_step(product_dir, step)
                cache_store(product_dir, step, cache_key)
                performance_finish(product_dir, step, started, True, "existing_valid_design")
                return {
                    "product_id": product_dir.name,
                    "outcome": "existing_valid_design",
                    "step": step,
                }
            archive_unusable_ecommerce_design(product_dir)
            prompt = (
                f"调用$ozon-ecommerce-designer，product_id={product_dir.name}。"
                "无人值守低推理：禁止提问、解释、列计划、联网、打开预览或上传Ozon。"
                "只读取当前商品：input/source.json、input/category-selection.json、output/product-analysis.json、"
                "output/merged-product-facts.json、output/ecommerce-design-context.json、output/image-source-preflight.json、"
                "output/image-design-revision-request.json（若存在）和templates/ozon-ecommerce-design.schema.json；"
                "不要读取完整attribute-fill-input.json、attribute-fill-input.compact.json、日志、其他商品、归档、test-data或项目脚本。"
                "目标：按schema原子写入output/ozon-ecommerce-design.json，最终只输出DONE ecommerce_design。"
                "如需运行Python校验或写文件，必须使用环境变量CAF_PYTHON_BIN指向的项目Python，禁止直接调用python/python3；"
                "顶层必须包含listing、sku_plan、attribute_decisions、main_images、detail_images、processing；"
                "attribute_decisions只做设计阶段必要的轻量语义占位：input_hash匹配ecommerce-design-context输入中的input_hash，common_attributes和attributes_by_sku可为空，"
                "只写当前商品事实中非常明确且与SKU差异、颜色、材质或图片文案直接相关的少量属性；不要枚举全部Ozon属性。"
                "完整必填属性、字典值和上传字段由后续field_completion确定性编译，设计阶段不要为填表牺牲销售故事线。"
                "颜色只取SKU标题/属性/专属图里主体面积最大的一个主色；关键词只用当前商品事实、Ozon类目/属性词和已完成本地缓存，不虚构热度。"
                "不得发明材质、功能、认证、承重、配件、尺寸或包装事实；未知就unknown。"
                "图片合同：每个选中SKU一张main_images，外加8张共享detail_images；每张有俄文文案、场景、构图、当前商品参考图和事实约束。"
                "main_images和detail_images都必须写完整对象数组：本商品有几个选中SKU就写几张主图，detail_images必须正好8张；"
                "禁止只写listing、visual_system或空数组后声称完成。"
                "视觉总监先做销售故事线：SKU主图负责三秒认知；8张详情按购买决策推进，每张只回答一个不同问题；"
                "整套图按顺序能看出从认识商品、选择理由、结构/材质证明、SKU选择、使用场景到下单前核对的购买决策线。"
                "合格信息图允许大标题、尺寸线、步骤号、SKU标签和卖点文字，但文字必须绑定真实产品证明；不要做空背景贴词。"
                "参考图规则：SKU图锁定当前SKU身份；主图和详情图可补充结构、使用方式、尺寸文字和场景参考。"
                "如果详情/SKU图里能读到尺寸、容量、结构或步骤，只能作为本商品参考证据；看不清就不写精确数字。"
                "参考不足时做更简单、更真实的商品图，不要脑补新结构、新材质、新配件。"
                "照片级硬要求：每张出图必须像卖家实拍照片（真实材质纹理、镜头景深、环境光、柔和阴影），禁止3D渲染/CGI/矢量插画观感；"
                "产品主体必须来自真实参考图，禁止凭空重建商品。俄文文案信息量要对标成熟Ozon卖家图：主图必须写商品类型/品名+SKU差异+一个核心卖点，"
                "结构图写部件标注块(部件名+一句说明)、参数图写数字、对比图写表格、场景图写卖点句；禁止的只是脱离产品的空口号大字和纯装饰贴字，不是禁止文字量。"
                "SKU颜色作用域必须先判断再写提示词：读SKU标题+SKU专属图+主图，判断颜色词指机身(body)还是差异部件(accent，如磁吸星环/盖子/把手)。"
                "body=机身整体渲染为该色并作palette首色；accent=机身保持中性本体色，该颜色只用于对应部件且要醒目。禁止一律当机身色，禁止把accent画成全机身、也禁止把body画成点缀。"
                "文字视觉不固定背景、颜色或左右版式；根据商品主体和留白决定，必须像成熟Ozon信息图模块：层级少而清楚、强对比、间距克制、"
                "对齐产品边缘/尺寸线/步骤区/SKU块/引线/自然留白；避免默认左上竖线标题块、孤立角落小字、装饰徽章条、大空文字板和临时字幕式规格堆。"
                "资料阶段优先快速完成可售资料；每个图位prompt只写本图必要的事实、构图、参考图、俄文和禁止改变点，目标350-700字符，"
                "全局摄影、真实性和合规规则只写visual_system/forbidden，禁止在每张图重复扩写；后续image_generation可按图位补足执行细节。"
                "source_references只能写image-source-preflight中可用的当前商品input/sku-images、input/main-images或input/detail-images图片；"
                "JSON证据只能写到source_refs/evidence，不能写进图片source_references；若要引用source.json内部字段，写成input/source.json#/字段名。"
                "SKU主图必须使用对应SKU预检推荐图作为第一参考，其他当前商品主图/详情图只做补充参考。"
                "所有图位默认用 generate_from_reference（参照生成）：参考图只锁定产品结构、颜色、比例和SKU差异这些事实，"
                "生图模型重新生成一张照片级商品图+规整信息图排版，不拼贴供应商促销图的像素、不复制其3D渲染风、中文文字、水印或混入的其它变体结构。"
                "标签最多30个，俄文#标签，禁止品牌、数字、英文和下划线；少于30个允许。"
                "若ecommerce-design-context.json中store_cluster.selected_stores有两家或以上，必须额外写store_variants："
                "每个已选store_id恰好一条，store_profile与上下文一致；每条都必须有独立listing、visual_system、"
                "按当前SKU顺序的main_images以及正好8张detail_images。不同店铺的标题、简介、标签与图片方案必须按定位独立，"
                "但SKU、真实属性、规格、结构、颜色、配件和来源证据绝不能改写；不得为同一营业主体建立重复变体。"
            )
        log_path = product_dir / "logs/full-pipeline.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if run_local_step(product_dir, step, settings, log_path):
            if product_deleted(product_dir):
                return {"product_id": product_dir.name, "outcome": "deleted", "step": step}
            if step == "image_generation":
                remaining_slots = schedulable_image_slot_count(product_dir, image_slot_concurrency)
                if remaining_slots > 0:
                    repaired_status = route_incomplete_images_back_to_generation(product_dir, load_json(product_dir / "status.json"), settings)
                    performance_finish(product_dir, step, started, False, "image_slots_still_pending")
                    return {
                        "product_id": product_dir.name,
                        "outcome": "retry",
                        "step": "image_generation",
                        "pending_slots": remaining_slots,
                        "next_action": repaired_status.get("next_action"),
                    }
                clear_image_host_recovery(product_dir)
            cache_store(product_dir, step, cache_key)
            performance_finish(product_dir, step, started, False, "completed")
            return {"product_id": product_dir.name, "outcome": "completed", "step": step}
        if step == "image_generation":
            return finish_image_generation_step(
                product_dir, settings, status, started, cache_key, log_path, image_slot_concurrency,
            )
        env = codex_worker_env(settings)
        codex_log_offset = log_path.stat().st_size if log_path.is_file() else 0
        with log_path.open("a", encoding="utf-8") as output:
            try:
                with _codex_semaphore:
                    completed = run_registered_process(
                        codex_exec_command(settings, step, prompt),
                        product_dir, output, product_step_timeout(product_dir, settings, step), env,
                        completion_check=(
                            new_step_artifacts_are_complete
                            if step in {"product_analysis", "ecommerce_design", "russian_copy"}
                            else None
                        ),
                        failure_check=(
                            lambda: ecommerce_design_live_failure_reason(
                                product_dir,
                                # 2026-08-15：空产物判死窗口 15s → 300s。设计师（Codex 会话）
                                # 会在写完前长时间保持空的最终产物（先写 .tmp 占位、最后才落
                                # 正式文件），15 秒判死把活着的会话反复误杀导致无限重试。
                                # 300s 与 ecommerce_design_stall_seconds 对齐；真死的会话
                                # 会进程退出，由 completion_check/超时路径处理。
                                quiet_seconds=float(settings.get("ecommerce_design_stall_seconds", 300)),
                            )
                            if step == "ecommerce_design"
                            else None
                        ),
                        completion_poll_seconds=float(settings.get("artifact_poll_interval_seconds", 0.5)),
                        stall_seconds=(
                            int(settings.get("image_generation_stall_seconds", 300))
                            if step == "image_generation"
                            else None
                        ),
                    )
            except EarlyArtifactFailure as exc:
                output.flush()
                if step == "ecommerce_design":
                    archive_unusable_ecommerce_design(product_dir)
                    if restore_latest_complete_ecommerce_design(product_dir):
                        complete_step(product_dir, step)
                        cache_store(product_dir, step, cache_key)
                        performance_finish(product_dir, step, started, False, "restored_valid_design_after_bad_retry")
                        return {
                            "product_id": product_dir.name,
                            "outcome": "restored_valid_design_after_bad_retry",
                            "step": step,
                            "error": str(exc),
                            "auto_resume": True,
                        }
                    status = load_json(product_dir / "status.json")
                    retries = status.setdefault("retry_count_by_step", {})
                    retries[step] = int(retries.get(step) or 0) + 1
                    early_failure_limit = max(1, int(settings.get("ecommerce_design_early_failure_retry_limit", 1)))
                    if retries[step] > early_failure_limit:
                        status.setdefault("warnings", []).append(
                            f"电商设计连续{retries[step]}次写出不完整结果，已暂停空转并保留断点；继续时会从ecommerce_design恢复。"
                        )
                        write_json_atomic(product_dir / "status.json", status)
                        waiting = mark_codex_service_waiting(
                            product_dir,
                            step,
                            settings,
                            "early_artifact_failure",
                        )
                        performance_finish(
                            product_dir,
                            step,
                            started,
                            False,
                            "waiting_after_repeated_early_artifact_failure",
                            retries[step],
                        )
                        return waiting
                    status["status"] = "PROCESSING"
                    status["current_step"] = step
                    status["next_action"] = step
                    status["last_run_at"] = now()
                    status.setdefault("warnings", []).append(str(exc))
                    write_json_atomic(product_dir / "status.json", status)
                    performance_finish(product_dir, step, started, False, "early_artifact_failure_retry", retries[step])
                    return {
                        "product_id": product_dir.name,
                        "outcome": "retry",
                        "step": step,
                        "error": str(exc),
                        "auto_resume": True,
                    }
                raise RuntimeError(str(exc)) from exc
            except subprocess.TimeoutExpired as exc:
                output.flush()
                if new_step_artifacts_are_complete():
                    complete_step(product_dir, step)
                    if step == "image_generation":
                        clear_image_host_recovery(product_dir)
                    cache_store(product_dir, step, cache_key)
                    performance_finish(product_dir, step, started, False, "completed_after_timeout")
                    with log_path.open("a", encoding="utf-8") as output:
                        output.write(f"\n[recovery] valid {step} artifacts detected; timeout treated as completed step.\n")
                    return {
                        "product_id": product_dir.name,
                        "outcome": "completed_after_timeout",
                        "step": step,
                    }
                if codex_worker_unavailable(log_path, codex_log_offset):
                    waiting = mark_codex_service_waiting(
                        product_dir, step, settings,
                        "usage_limit" if codex_usage_limit_reached(log_path, codex_log_offset) else "temporary_outage",
                    )
                    performance_finish(product_dir, step, started, False, "waiting_for_ai_service")
                    output.write(
                        "\n[service-wait] Codex timed out after a transport/service failure; "
                        "checkpoint preserved, local fallback disabled, online retry scheduled.\n"
                    )
                    return {
                        "product_id": product_dir.name,
                        "outcome": "waiting_for_ai_service",
                        "step": step,
                        "retry_after": waiting["ai_service_retry_after"],
                    }
                if step == "image_generation":
                    stalled = isinstance(exc, ImageGenerationStalled)
                    stall_seconds = int(settings.get("image_generation_stall_seconds", 600))
                    stall_minutes = max(1, round(stall_seconds / 60))
                    reason = (
                        f"生图连续{stall_minutes}分钟没有新增图片，已自动重启Codex生图进程"
                        if stalled else
                        f"生图执行超过{product_step_timeout(product_dir, settings, step)}秒，已自动重启Codex生图进程"
                    )
                    recovery = recover_interrupted_image_generation(
                        product_dir,
                        settings,
                        reason,
                        "image_generation_stalled" if stalled else "image_generation_timeout",
                    )
                    performance_finish(
                        product_dir, step, started, False,
                        "automatic_recovery" if recovery["outcome"] == "retry" else "failed_after_recovery",
                        int((load_json(product_dir / "status.json").get("retry_count_by_step") or {}).get(step, 0)),
                    )
                    return recovery
                if step == "ecommerce_design":
                    waiting = mark_codex_service_waiting(
                        product_dir,
                        step,
                        settings,
                        "long_running_timeout",
                    )
                    performance_finish(product_dir, step, started, False, "waiting_for_ai_design_timeout")
                    output.write(
                        "\n[service-wait] Ecommerce design exceeded the step timeout. "
                        "Checkpoint preserved; retrying the same design step later without marking the product as a data error.\n"
                    )
                    return {
                        "product_id": product_dir.name,
                        "outcome": "waiting_for_ai_service",
                        "step": step,
                        "retry_after": waiting["ai_service_retry_after"],
                    }
                raise RuntimeError(
                    f"Step {step} timed out after {product_step_timeout(product_dir, settings, step)}s"
                ) from exc
            except OSError as exc:
                output.write(f"\n[codex-worker-unavailable] {type(exc).__name__}: {exc}\n")
                output.flush()
                waiting = mark_codex_service_waiting(product_dir, step, settings)
                performance_finish(product_dir, step, started, False, "waiting_for_ai_service")
                output.write(
                    "[service-wait] Codex worker could not start; checkpoint preserved, "
                    "local fallback disabled, online retry scheduled.\n"
                )
                return {
                    "product_id": product_dir.name,
                    "outcome": "waiting_for_ai_service",
                    "step": step,
                    "retry_after": waiting["ai_service_retry_after"],
                }
        if new_step_artifacts_are_complete():
            complete_step(product_dir, step)
            if step == "image_generation":
                clear_image_host_recovery(product_dir)
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
                complete_step(product_dir, step)
            cache_store(product_dir, step, cache_key)
            performance_finish(product_dir, step, started, False, "api_submitted", network_wait=time.monotonic() - started)
            return {"product_id": product_dir.name, "outcome": "api_submitted", "step": step}
        # A temporary Codex transport outage is not a product-data error.
        # Keep every existing checkpoint, pause this exact step and retry only
        # the online model later. Never generate a local substitute analysis.
        if (
            completed.returncode != 0
            and codex_worker_unavailable(log_path, codex_log_offset)
        ):
            waiting = mark_codex_service_waiting(
                product_dir, step, settings,
                "usage_limit" if codex_usage_limit_reached(log_path, codex_log_offset) else "temporary_outage",
            )
            performance_finish(product_dir, step, started, False, "waiting_for_ai_service")
            with log_path.open("a", encoding="utf-8") as output:
                output.write(
                    "\n[service-wait] Codex unavailable; checkpoint preserved, local fallback disabled, online retry scheduled.\n"
                )
            return {
                "product_id": product_dir.name,
                "outcome": "waiting_for_ai_service",
                "step": step,
                "retry_after": waiting["ai_service_retry_after"],
            }
        made_progress = (
            after.get("status") in TERMINAL_STATES
            or list(after.get("completed_steps") or []) != before_completed
            or after_api_writes != before_api_writes
            or after.get("next_action") != step
        )
        if completed.returncode == 0 and new_step_artifacts_are_complete():
            if step == "store_variant_assets":
                refresh_store_variant_progress(product_dir)
            complete_step(product_dir, step)
            cache_store(product_dir, step, cache_key)
            performance_finish(product_dir, step, started, False, "artifact_completed_after_worker_exit")
            return {
                "product_id": product_dir.name,
                "outcome": "artifact_completed_after_worker_exit",
                "step": step,
            }
        if completed.returncode == 0 and made_progress:
            merged_qc = False
            if (
                step == "image_generation"
                and step in (after.get("completed_steps") or [])
            ):
                remaining_slots = schedulable_image_slot_count(product_dir, image_slot_concurrency)
                if remaining_slots > 0:
                    route_incomplete_images_back_to_generation(product_dir, after, settings)
                    performance_finish(product_dir, step, started, False, "image_slots_still_pending")
                    return {
                        "product_id": product_dir.name,
                        "outcome": "retry",
                        "step": "image_generation",
                        "pending_slots": remaining_slots,
                    }
                merged_qc = complete_embedded_image_qc(product_dir, settings, log_path)
            if step == "image_generation":
                clear_image_host_recovery(product_dir)
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
            mark_needs_attention(
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
        current = load_json(product_dir / "status.json")
        next_step = str(current.get("next_action") or "image_generation")
        performance_finish(product_dir, step, locals().get("started", time.monotonic()), False, "single_slot_retry", 1)
        return {"product_id": product_dir.name, "outcome": "retry", "step": next_step, "error": str(exc)}
    except ProductDeletionRequested:
        return {"product_id": product_dir.name, "outcome": "deleted", "step": locals().get("step", "unknown")}
    except ImageSourcePreflightBlocked as exc:
        step = "image_source_preflight"
        blocked = mark_needs_attention(product_dir, step, str(exc))
        blocked["current_step"] = step
        blocked["failed_step"] = step
        blocked["next_action"] = "ecommerce_design"
        write_json_atomic(product_dir / "status.json", blocked)
        performance_finish(product_dir, step, locals().get("started", time.monotonic()), False, "sku_reference_preflight_blocked", 0)
        return {"product_id": product_dir.name, "outcome": "needs_attention", "step": step, "error": str(exc)}
    except Exception as exc:
        if product_deleted(product_dir):
            return {"product_id": product_dir.name, "outcome": "deleted", "step": locals().get("step", "unknown")}
        status = load_json(product_dir / "status.json")
        step = status.get("next_action") or "validate_source"
        failed_step = str(status.get("failed_step") or status.get("current_step") or step)
        if step == "retry_failed_step":
            step = failed_step if failed_step in PIPELINE_STEPS else step
        repaired_multi_store = route_missing_multi_store_assets_automatically(product_dir, step, str(exc))
        if repaired_multi_store is not None:
            performance_finish(
                product_dir, step, locals().get("started", time.monotonic()), False,
                "multi_store_assets_auto_repair", 0,
            )
            return {
                "product_id": product_dir.name,
                "outcome": "retry",
                "step": repaired_multi_store.get("next_action"),
                "error": str(exc),
                "auto_resume": True,
            }
        resumed_store_variants = continue_store_variant_assets_automatically(product_dir, step, str(exc))
        if resumed_store_variants is not None:
            performance_finish(
                product_dir, step, locals().get("started", time.monotonic()), False,
                "store_variant_assets_auto_continue", 0,
            )
            return {
                "product_id": product_dir.name,
                "outcome": "retry",
                "step": "store_variant_assets",
                "error": str(exc),
                "auto_resume": True,
            }
        automatic = keep_prewrite_ozon_upload_automatic(product_dir, step, str(exc))
        if automatic is not None:
            performance_finish(
                product_dir,
                step,
                locals().get("started", time.monotonic()),
                False,
                "prewrite_upload_auto_retry",
                int((automatic.get("retry_count_by_step") or {}).get(step, 0)),
            )
            return {
                "product_id": product_dir.name,
                "outcome": "retry",
                "step": step,
                "error": str(exc),
                "auto_resume": True,
            }
        if step == "ozon_upload" and UPLOAD_IMAGE_PRECHECK_ERROR in str(exc):
            repaired = route_upload_image_precheck_back_to_image_plan(product_dir, status, str(exc))
            performance_finish(
                product_dir,
                step,
                locals().get("started", time.monotonic()),
                False,
                "upload_image_precheck_auto_rewind",
                0,
            )
            return {
                "product_id": product_dir.name,
                "outcome": "retry",
                "step": "image_plan",
                "error": str(exc),
                "auto_resume": True,
                "next_action": repaired.get("next_action"),
            }
        retries = status.setdefault("retry_count_by_step", {})
        retries[step] = int(retries.get(step) or 0) + 1
        write_json_atomic(product_dir / "status.json", status)
        if retries[step] > int(settings.get("step_retry_limit", 1)):
            mark_needs_attention(product_dir, step, f"{type(exc).__name__}: {exc}")
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
    if step in {"ecommerce_design", "russian_copy", "product_positioning", "image_plan", "field_completion", "store_variant_assets"}:
        return "copy"
    return "analysis"


def mark_manual_upload_ready(product_dir: Path, status: Dict[str, Any]) -> Dict[str, Any]:
    """Persist the manual upload boundary without pretending upload completed."""
    recovered = any(
        status.get(key) not in {None, "", "unknown", "UNKNOWN"}
        for key in ("error_code", "error_message", "failed_step")
    )
    warning = (
        "上次问题已恢复，商品资料和图片技术质检已通过；请重新确认店铺后上传"
        if recovered else
        "商品资料和图片技术质检已通过，等待用户手动上传"
    )
    previous_status = str(status.get("status") or "unknown")
    status.update({
        "status": "WAITING_MANUAL_REVIEW",
        "current_step": "manual_ozon_upload",
        "progress": max(95, int(status.get("progress") or 0)),
        "completed_at": "unknown",
        "next_action": "manual_ozon_upload",
        "active_step": None,
        "task_authorized": False,
        "error_code": "unknown",
        "error_message": "unknown",
        "failed_step": "unknown",
    })
    ozon = dict(status.get("ozon") or {})
    if int(status.get("api_write_count") or 0) == 0:
        ozon.update({"upload_status": "not_started", "errors": []})
    status["ozon"] = ozon
    status["completed_steps"] = [
        step for step in status.get("completed_steps") or [] if step != "ozon_upload"
    ]
    status["pending_steps"] = list(dict.fromkeys([
        *(status.get("pending_steps") or []), "ozon_upload",
    ]))
    status.pop("target_store_ids_for_run", None)
    warnings = status.setdefault("warnings", [])
    warnings[:] = [
        value for value in warnings
        if "等待用户检查并确认上传" not in str(value)
        and "等待用户手动上传" not in str(value)
        and "图片技术质检已通过" not in str(value)
        and "上次问题已恢复" not in str(value)
    ]
    if warning not in warnings:
        warnings.append(warning)
    if previous_status != "WAITING_MANUAL_REVIEW":
        status.setdefault("history", []).append({
            "from": previous_status,
            "to": "WAITING_MANUAL_REVIEW",
            "at": now(),
            "reason": "Production finished locally and is waiting for explicit manual upload.",
        })
    write_json_atomic(product_dir / "status.json", status)
    return status


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
    batch["failed_count"] = sum(item["status"] in ATTENTION_STATES for item in products)
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
    failed = sum(row["status"] in ATTENTION_STATES for row in rows)
    succeeded = sum(row["status"] in SUCCESS_STATES for row in rows)
    waiting_manual_review = sum(row["status"] == "WAITING_MANUAL_REVIEW" for row in rows)
    awaiting_manual_upload = (
        not batch.get("auto_upload", False)
        and waiting_manual_review > 0
    )
    if awaiting_manual_upload:
        batch_status = "AWAITING_MANUAL_UPLOAD"
    elif not rows:
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
        "waiting_manual_review_count": waiting_manual_review,
        "manual_upload_required": awaiting_manual_upload,
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
    progress_values = [
        int(load_json(root / "products" / row["product_id"] / "status.json").get("progress") or 0)
        for row in rows
    ]
    batch_progress = (
        round(sum(progress_values) / len(progress_values))
        if awaiting_manual_upload and progress_values else 100
    )
    batch.update({
        "status": batch_status,
        "completed_at": report["completed_at"],
        "processing_count": 0,
        "success_count": succeeded,
        "failed_count": failed,
        "progress": batch_progress,
    })
    write_json_atomic(batch_path(root, batch["batch_id"]), batch)
    write_json_atomic(batch_result_path(root, batch["batch_id"]), report)
    write_json_atomic(root / "batch-result.json", report)
    return report


def execute_batch(root: Path, batch: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    global _codex_semaphore, _image_slot_semaphore
    _codex_semaphore = threading.BoundedSemaphore(max(1, int(settings.get("codex_concurrency", 2))))
    _image_slot_semaphore = threading.BoundedSemaphore(
        max(1, min(int(settings.get("image_slot_concurrency", 3)), 3))
    )
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
            mark_needs_attention(
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
            mark_needs_attention(product_dir, "validate_source", str(exc))
    sync_batch(root, batch)

    while True:
        if safe_stop_requested(batch["batch_id"]):
            return stop_batch_at_checkpoint(root, batch)
        active = []
        locked_waiting = False
        ai_service_waiting = False
        nearest_ai_retry_seconds: Optional[float] = None
        groups: Dict[str, List[Path]] = {
            "analysis": [], "category": [], "pricing": [], "copy": [],
            "image_generation": [], "image_qc": [], "ozon": [],
        }
        for product_dir in valid_product_dirs:
            if deletion_requested(root, product_dir.name) or not product_dir.is_dir():
                continue
            status = load_json(product_dir / "status.json")
            retry_remaining = codex_retry_remaining_seconds(status)
            if retry_remaining > 0:
                active.append(product_dir)
                ai_service_waiting = True
                nearest_ai_retry_seconds = (
                    retry_remaining
                    if nearest_ai_retry_seconds is None
                    else min(nearest_ai_retry_seconds, retry_remaining)
                )
                continue
            pipeline_lock = product_dir / ".pipeline.lock"
            if pipeline_lock.is_file():
                try:
                    owner_pid = int(pipeline_lock.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    owner_pid = 0
                worker_checkpoint = product_worker_path(product_dir.name)
                # A worker can finish its checkpoint before the executor
                # thread reaches its finally block. Do not let that brief
                # window create a hot loop; a lock with no active step and no
                # worker checkpoint is stale and can be released safely.
                if (
                    owner_pid == os.getpid()
                    and status.get("active_step") is None
                    and not worker_checkpoint.is_file()
                ):
                    pipeline_lock.unlink(missing_ok=True)
                elif owner_pid == os.getpid():
                    locked_waiting = True
                    continue
            # Workbench batches are continuous after the single Run Task
            # authorization.  Keep legacy manual batches readable, but never
            # insert a new manual-upload checkpoint into a workbench batch.
            if not batch.get("auto_upload", False) and (
                status.get("status") == "WAITING_MANUAL_REVIEW"
                or status.get("next_action") in {"ozon_upload", "manual_ozon_upload"}
            ):
                mark_manual_upload_ready(product_dir, status)
                continue
            manual_upload_resume = (
                bool(batch.get("auto_upload", False))
                and status.get("status") == "WAITING_MANUAL_REVIEW"
                and status.get("next_action") == "ozon_upload"
                and (
                    int(status.get("api_write_count") or 0) == 0
                    or bool(status.get("target_store_ids_for_run") or [])
                )
            )
            if status.get("status") in TERMINAL_STATES and not manual_upload_resume:
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
            if ai_service_waiting:
                time.sleep(max(0.2, min(
                    float(settings.get("poll_interval_seconds", 3)),
                    nearest_ai_retry_seconds or 0.2,
                )))
                continue
            if locked_waiting:
                time.sleep(max(0.2, float(settings.get("poll_interval_seconds", 3))))
                continue
            for product_dir in active:
                mark_needs_attention(product_dir, "validate_source", "No schedulable pipeline step was found.")
            break
        with ThreadPoolExecutor(max_workers=len(scheduled)) as executor:
            future_products = {
                executor.submit(run_one_step, product_dir, settings): product_dir
                for product_dir in scheduled
            }
            deferred_image_products: List[str] = []
            for future in as_completed(future_products):
                product_dir = future_products[future]
                try:
                    result = future.result()
                    if (
                        result.get("step") == "image_generation"
                        and result.get("outcome") == "retry"
                    ):
                        deferred_image_products.append(product_dir.name)
                except Exception as exc:
                    # A worker must never abort the whole batch.  run_one_step
                    # already converts normal failures into product status;
                    # this is the final containment boundary for unexpected
                    # executor errors (serialization, plugin, or coding bugs).
                    status = load_json(product_dir / "status.json")
                    step = str(status.get("next_action") or status.get("current_step") or "validate_source")
                    status.setdefault("warnings", []).append(
                        f"本商品工作线程异常，已隔离并继续批次：{type(exc).__name__}: {exc}"
                    )
                    write_json_atomic(product_dir / "status.json", status)
                    mark_needs_attention(
                        product_dir,
                        step,
                        f"Worker crashed in {step}: {type(exc).__name__}: {exc}",
                    )
            if deferred_image_products:
                deferred = set(deferred_image_products)
                batch["products"] = [
                    item for item in batch["products"]
                    if item.get("product_id") not in deferred
                ] + [
                    item for item in batch["products"]
                    if item.get("product_id") in deferred
                ]
                write_json_atomic(batch_path(root, batch["batch_id"]), batch)
        sync_batch(root, batch)
        if safe_stop_requested(batch["batch_id"]):
            return stop_batch_at_checkpoint(root, batch)
    return finalize_batch(root, batch)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id")
    parser.add_argument("--enqueue-only", action="store_true")
    parser.add_argument("--product-id", help="Run one eligible product as a controlled validation batch")
    args = parser.parse_args()
    lock_fd = acquire_batch_lock()
    completed_normally = False
    active_batch_id = str(args.batch_id or "")
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
        completed_normally = True
        cleanup_images(ROOT, settings)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        try:
            for worker_path in (ROOT / "logs" / "product-workers").glob("*.json"):
                try:
                    worker = load_json(worker_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                product_id = str(worker.get("product_id") or "")
                if not product_id:
                    continue
                status_path = ROOT / "products" / product_id / "status.json"
                try:
                    status = load_json(status_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if active_batch_id and str(status.get("batch_id") or "") != active_batch_id:
                    continue
                pid = int(worker.get("pid") or 0)
                if pid > 0:
                    try:
                        os.killpg(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except OSError:
                            pass
        except Exception:
            pass
        if completed_normally and CURRENT_BATCH_PATH.is_file():
            try:
                current = load_json(CURRENT_BATCH_PATH)
            except (OSError, ValueError, json.JSONDecodeError):
                current = {}
            if (
                int(current.get("pid") or 0) == os.getpid()
                or (active_batch_id and str(current.get("batch_id") or "") == active_batch_id)
            ):
                CURRENT_BATCH_PATH.unlink(missing_ok=True)
        os.close(lock_fd)
        BATCH_LOCK_PATH.unlink(missing_ok=True)
        BATCH_PID_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
