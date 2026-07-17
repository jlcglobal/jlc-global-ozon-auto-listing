#!/usr/bin/env python3
"""Execute one product independently for each selected local Ozon store.

The shared product master is never duplicated in the workbench. Each store gets
an isolated runtime workspace and its own persisted result artifacts so one
failure cannot overwrite another store's task, product, or idempotency data.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

try:
    from pipeline_runtime import load_json, normalize_checkpoint, now, write_json_atomic
    from store_publications import ensure_store_offer_ids, is_store_offer_id, load_publications, save_publications
    from task_database import cutover_active
    from workbench_stores import mark_store_validation_failed
except ModuleNotFoundError:  # Imported as scripts.multi_store_upload by tests/tools.
    from scripts.pipeline_runtime import load_json, normalize_checkpoint, now, write_json_atomic
    from scripts.store_publications import ensure_store_offer_ids, is_store_offer_id, load_publications, save_publications
    from scripts.task_database import cutover_active
    from scripts.workbench_stores import mark_store_validation_failed


ROOT = Path(__file__).resolve().parents[1]
PENDING_STATES = {"SUBMITTED", "QUEUED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"}
SUCCESS_STATES = {"SUCCESS", "IMPORTED", "UPLOADED", "ACTIVE"}
HANDOFF_STATE = "HANDED_OFF_TO_OZON"
RETRYABLE_STATES = {"SELECTED", "FAILED", "QUERY_ERROR"}
STORE_ARTIFACTS = (
    "ozon-result.json", "ozon-write-receipt.json", "ozon-idempotency.json",
    "ozon-last-upload-hashes.json", "product-exists-check.json",
    "ozon-upload-payload.json", "ozon-images.json", "ozon-image-transfer.json",
    "ozon-preflight.json", "ozon-update-request-summary.json",
    "store-offer-id-map.json",
)


def _safe_store_id(value: str) -> str:
    if not value or any(part in value for part in ("/", "\\", "..")):
        raise ValueError("Invalid local store id")
    return value


def store_artifact_dir(product_dir: Path, store_id: str) -> Path:
    return product_dir / "output/store-runs" / _safe_store_id(store_id)


def store_workspace(root: Path, product_id: str, store_id: str) -> Path:
    return root / "runtime/store-upload-workspaces" / product_id / _safe_store_id(store_id)


def _load_env_file(path: Path, environ: Dict[str, str]) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        environ[key.strip()] = value.strip().strip('"').strip("'")


def _process_state(pid: int) -> str:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2, check=False,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _image_channel_pids(stop_path: Path) -> list[int]:
    """Find every worker watching this stop file, including older duplicates."""
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,command="],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    marker = str(stop_path)
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        if "ozon_uploader.image_channel_worker" not in line or marker not in line:
            continue
        try:
            pid = int(line.strip().split(None, 1)[0])
        except (ValueError, IndexError):
            continue
        if not _process_state(pid).upper().startswith("Z"):
            pids.append(pid)
    return pids


def stop_workspace_image_channels(workspace: Path, wait_seconds: float = 12) -> None:
    """Gracefully close all tunnel workers before deleting their state files."""
    stop_paths = list(workspace.glob("products/P*/output/image-channel.stop"))
    state_paths = list(workspace.glob("products/P*/output/image-channel-state.json"))
    for state_path in state_paths:
        stop_path = state_path.with_name("image-channel.stop")
        if stop_path not in stop_paths:
            stop_paths.append(stop_path)
    for stop_path in stop_paths:
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        stop_path.write_text("isolated_workspace_rebuild\n", encoding="utf-8")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        live = [pid for stop_path in stop_paths for pid in _image_channel_pids(stop_path)]
        if not live:
            return
        time.sleep(0.25)
    live = sorted({pid for stop_path in stop_paths for pid in _image_channel_pids(stop_path)})
    if live:
        raise RuntimeError(f"旧图片通道仍在退出中，已阻止删除工作区：{live}")


def prepare_isolated_product(root: Path, product_dir: Path, store_id: str, publication: Mapping[str, Any]) -> Path:
    workspace = store_workspace(root, product_dir.name, store_id)
    isolated = workspace / "products" / product_dir.name
    if workspace.exists():
        stop_workspace_image_channels(workspace)
        shutil.rmtree(workspace)
    isolated.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(product_dir, isolated)
    output = isolated / "output"
    for name in STORE_ARTIFACTS:
        (output / name).unlink(missing_ok=True)
    previous = store_artifact_dir(product_dir, store_id)
    if previous.is_dir():
        for name in STORE_ARTIFACTS:
            source = previous / name
            if source.is_file():
                shutil.copy2(source, output / name)
    status = normalize_checkpoint(load_json(isolated / "status.json"))
    status.update({
        "status": "OZON_READY", "current_step": "field_completion",
        "next_action": "ozon_upload", "task_authorized": True,
        "api_write_count": 0, "ozon": {"upload_status": "not_started", "errors": []},
        "error_code": "unknown", "error_message": "unknown", "failed_step": "unknown",
    })
    status["completed_steps"] = [step for step in status.get("completed_steps") or [] if step != "ozon_upload"]
    status["pending_steps"] = ["ozon_upload"]
    write_json_atomic(isolated / "status.json", status)
    config_path = output / "ozon-upload-config.json"
    config = load_json(config_path)
    config["shop_name"] = store_id
    prices = {
        str(item.get("sku_id")): item.get("price_override_cny", item.get("initial_price_cny"))
        for item in publication.get("sku_publications") or []
        if item.get("price_override_cny", item.get("initial_price_cny")) not in {None, "", "unknown"}
    }
    for item in config.get("sku_prices") or []:
        sku_id = str(item.get("source_sku_id"))
        if sku_id in prices:
            item["price"] = f"{float(prices[sku_id]):.2f}"
    write_json_atomic(config_path, config)
    offer_ids = {
        str(item.get("sku_id")): str(item.get("offer_id"))
        for item in publication.get("sku_publications") or []
        if not _unknown(item.get("offer_id"))
    }
    draft_path = output / "ozon-draft.json"
    if draft_path.is_file():
        draft = load_json(draft_path)
        draft_sku_ids = [str(item.get("source_sku_id")) for item in draft.get("skus") or []]
        missing = [sku_id for sku_id in draft_sku_ids if sku_id not in offer_ids]
        if missing:
            raise RuntimeError(
                f"店铺 {store_id} 缺少SKU专属货号，已在上传前阻断：{', '.join(missing)}"
            )
        if len({offer_ids[sku_id] for sku_id in draft_sku_ids}) != len(draft_sku_ids):
            raise RuntimeError(f"店铺 {store_id} 存在重复SKU货号，已在上传前阻断")
        for sku in draft.get("skus") or []:
            sku["offer_id"] = offer_ids[str(sku.get("source_sku_id"))]
        if draft_sku_ids:
            draft["offer_id"] = offer_ids[draft_sku_ids[0]]
        write_json_atomic(draft_path, draft)
        grouping_path = output / "variant-grouping-result.json"
        if grouping_path.is_file():
            grouping = load_json(grouping_path)
            for variant in grouping.get("variants") or []:
                sku_id = str(variant.get("sku_id"))
                if sku_id in offer_ids:
                    variant["offer_id"] = offer_ids[sku_id]
            write_json_atomic(grouping_path, grouping)
        generated_mapping = bool(draft_sku_ids) and all(
            is_store_offer_id(offer_ids[sku_id]) for sku_id in draft_sku_ids
        )
        write_json_atomic(output / "store-offer-id-map.json", {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "store_id": store_id,
            "strategy": "store_specific_random_v1" if generated_mapping else "legacy_preserved",
            "requires_create": generated_mapping,
            "prepared_at": now(),
            "sku_offer_ids": [
                {"sku_id": sku_id, "offer_id": offer_ids[sku_id]}
                for sku_id in draft_sku_ids
            ],
        })
    return isolated


def default_runner(root: Path, isolated: Path, store_id: str) -> Dict[str, Any]:
    env = dict(os.environ, UPLOAD_MODE="production")
    _load_env_file(root / "ozon-adapter" / f".env.{store_id}", env)
    log_path = isolated / "logs/store-upload.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(root / "ozon-uploader/cli.py"),
        str(isolated), "--shop", store_id,
    ]
    offer_map_path = isolated / "output/store-offer-id-map.json"
    offer_map = load_json(offer_map_path) if offer_map_path.is_file() else {}
    if offer_map.get("requires_create") is True:
        command.extend(["--require-action", "create"])
    command.append("--execute")
    with log_path.open("a", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=False,
        )
    status = load_json(isolated / "status.json")
    if completed.returncode and status.get("error_message") in {None, "", "unknown", "UNKNOWN"}:
        lines = [line.strip() for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        failure = next((line[2:].strip() for line in reversed(lines) if line.startswith("- ")), None)
        status["error_message"] = failure or (lines[-1] if lines else "店铺上传进程失败，未记录具体原因")
    result_path = isolated / "output/ozon-result.json"
    idempotency_path = isolated / "output/ozon-idempotency.json"
    return {
        "returncode": completed.returncode,
        "status": status,
        "result": load_json(result_path) if result_path.is_file() else {},
        "idempotency": load_json(idempotency_path) if idempotency_path.is_file() else {},
    }


def _persist_store_artifacts(product_dir: Path, store_id: str, isolated: Path) -> None:
    target = store_artifact_dir(product_dir, store_id)
    target.mkdir(parents=True, exist_ok=True)
    for name in STORE_ARTIFACTS:
        source = isolated / "output" / name
        if source.is_file():
            shutil.copy2(source, target / name)


def _unknown(value: Any) -> bool:
    return value in {None, "", "unknown", "UNKNOWN"}


def credential_failure(error: Any) -> bool:
    text = str(error or "").casefold()
    return any(token in text for token in (
        "api-key is deactivated", "api key is deactivated",
        "invalid api-key", "invalid api key", "unauthorized",
    ))


def definitely_retryable(record: Mapping[str, Any]) -> bool:
    if str(record.get("status") or "") not in {"FAILED", "QUERY_ERROR", "SELECTED"}:
        return False
    for sku in record.get("sku_publications") or []:
        if not _unknown(sku.get("task_id")):
            return False
        if str(sku.get("moderation_status") or "").upper() in PENDING_STATES:
            return False
    return True


def _store_result(record: Dict[str, Any], outcome: Mapping[str, Any], increment_version: bool = True) -> None:
    status = dict(outcome.get("status") or {})
    result = dict(outcome.get("result") or {})
    idempotency = dict(outcome.get("idempotency") or {})
    write_count = int(status.get("api_write_count") or 0)
    raw_status = str(status.get("status") or "FAILED_HARD_BLOCKER").upper()
    if raw_status in {"ACTIVE", "UPLOADED"}:
        store_status = "SUCCESS"
    elif raw_status in {"PENDING_REMOTE", "OZON_MODERATION"}:
        store_status = "PENDING_REMOTE"
    elif raw_status in {"SUBMITTED", "UPLOADING"} and any(not _unknown(item.get("task_id")) for item in (result.get("items") or [])):
        # A task id is the local hand-off terminal.  No remote poll is needed
        # to decide whether this product can leave the production pipeline.
        store_status = HANDOFF_STATE
    elif write_count > 0 and any(not _unknown(item.get("task_id")) for item in (result.get("items") or [])):
        store_status = HANDOFF_STATE
    elif write_count > 0:
        store_status = "PENDING_REMOTE"
    else:
        store_status = "FAILED"
    items = result.get("items") or []
    by_sku = {str(item.get("source_sku_id") or item.get("sku_id") or ""): item for item in items}
    action = str(result.get("action") or result.get("upload_action") or "UNKNOWN").upper()
    errors = result.get("errors") or (status.get("ozon") or {}).get("errors") or []
    for sku in record.get("sku_publications") or []:
        item = by_sku.get(str(sku.get("sku_id"))) or (items[0] if len(items) == 1 else {})
        item_action = str(item.get("action") or "UNKNOWN").upper()
        resolved_action = item_action if not _unknown(item_action) else action if not _unknown(action) else str(sku.get("action") or "UNKNOWN").upper()
        sku.update({
            "offer_id": item.get("offer_id") or sku.get("offer_id") or "unknown",
            "action": resolved_action,
            "task_id": str(item.get("task_id") or result.get("task_id") or "unknown"),
            "ozon_product_id": str(item.get("product_id") or item.get("ozon_product_id") or "unknown"),
            "payload_hash": idempotency.get("payload_hash") or result.get("payload_hash") or sku.get("payload_hash") or "unknown",
            "moderation_status": store_status.lower(),
            "errors": errors, "warnings": result.get("warnings") or [],
        })
    record.update({
        "selected": True, "status": store_status,
        "api_write_count": write_count,
        "submission_version": int(record.get("submission_version") or 0) + (1 if increment_version else 0),
        "last_submitted_at": now() if write_count else record.get("last_submitted_at"),
        "last_checked_at": now(),
        "last_error": None if store_status != "FAILED" else (status.get("error_message") or "店铺上传失败"),
    })


def aggregate_product_status(
    product_dir: Path,
    publications: Mapping[str, Any],
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    status = normalize_checkpoint(load_json(product_dir / "status.json"))
    previous_status = str(status.get("status") or "unknown")
    selected = [item for item in (publications.get("stores") or {}).values() if item.get("selected")]
    states = {str(item.get("status") or "") for item in selected}
    total_writes = sum(int(item.get("api_write_count") or 0) for item in selected)
    if states and states <= SUCCESS_STATES:
        target, error = "UPLOADED", "unknown"
    elif states and states <= {HANDOFF_STATE}:
        target, error = HANDOFF_STATE, "已提交Ozon，后续请在Ozon商品卡后台处理"
    elif HANDOFF_STATE in states and not (states & {"FAILED", "QUERY_ERROR"}):
        target, error = HANDOFF_STATE, "已提交Ozon，后续请在Ozon商品卡后台处理"
    elif states & PENDING_STATES:
        target, error = "PENDING_REMOTE", "unknown"
    elif states & {"FAILED", "QUERY_ERROR"}:
        target, error = "FAILED_HARD_BLOCKER", "一家或多家店铺上传失败；只允许重试失败店铺"
    else:
        target, error = "OZON_READY", "unknown"
    published_skus = [
        sku
        for record in selected
        for sku in (record.get("sku_publications") or [])
        if isinstance(sku, dict)
    ]
    first_offer = next((str(sku.get("offer_id")) for sku in published_skus if not _unknown(sku.get("offer_id"))), "unknown")
    first_product = next((str(sku.get("ozon_product_id")) for sku in published_skus if not _unknown(sku.get("ozon_product_id"))), "unknown")
    first_task = next((str(sku.get("task_id")) for sku in published_skus if not _unknown(sku.get("task_id"))), "unknown")
    first_store = next((str(store_id) for store_id, record in (publications.get("stores") or {}).items() if record.get("selected")), "unknown")
    ozon = dict(status.get("ozon") or {})
    ozon.update({
        "upload_status": "handed_off" if target == HANDOFF_STATE else "uploaded" if target in {"UPLOADED", "ACTIVE"} else "uploading" if target == "PENDING_REMOTE" else "failed" if target == "FAILED_HARD_BLOCKER" else "not_started",
        "product_id": first_product,
        "offer_id": first_offer,
        "task_id": first_task,
        "shop_name": first_store,
        "last_response": ozon.get("last_response"),
        "errors": ozon.get("errors") or [],
    })
    status.update({
        "status": target, "current_step": "ozon_upload", "active_step": None,
        "progress": 100 if target in {"UPLOADED", HANDOFF_STATE} else 99 if target == "PENDING_REMOTE" else 95,
        "completed_at": now() if target == "UPLOADED" else "unknown",
        "api_write_count": total_writes, "last_run_at": now(),
        "error_code": "STORE_UPLOAD_FAILED" if target == "FAILED_HARD_BLOCKER" else "unknown",
        "error_message": error, "failed_step": "ozon_upload" if target == "FAILED_HARD_BLOCKER" else "unknown",
        "next_action": "retry_failed_store" if target == "FAILED_HARD_BLOCKER" else "read_only_status_query" if target == "PENDING_REMOTE" else "complete",
        "ozon": ozon,
    })
    if target in {"UPLOADED", HANDOFF_STATE, "PENDING_REMOTE"}:
        status["warnings"] = [
            warning for warning in status.get("warnings") or []
            if "等待用户检查并确认上传" not in str(warning)
        ]
    history = status.setdefault("history", [])
    last_history_status = str((history[-1] if history else {}).get("to") or "unknown")
    transition_from = previous_status if last_history_status == "unknown" else last_history_status
    if transition_from != target:
        if transition_from == "OZON_READY" and target == "PENDING_REMOTE":
            history.append({
                "from": "OZON_READY",
                "to": "UPLOADING",
                "at": now(),
                "reason": "The selected store upload started.",
            })
            transition_from = "UPLOADING"
        history.append({
            "from": transition_from,
            "to": target,
            "at": now(),
            "reason": "Aggregated independent per-store Ozon publication states.",
        })
    status.pop("target_store_ids_for_run", None)
    if target in {"UPLOADED", HANDOFF_STATE, "PENDING_REMOTE"} and "ozon_upload" not in status["completed_steps"]:
        status["completed_steps"].append("ozon_upload")
        status["pending_steps"] = [step for step in status["pending_steps"] if step != "ozon_upload"]
    ozon_steps = [item for item in status.setdefault("steps", []) if item.get("name") == "ozon_upload"]
    if target in {"UPLOADED", HANDOFF_STATE, "PENDING_REMOTE"} and (not ozon_steps or ozon_steps[-1].get("status") != "completed"):
        status["steps"].append({
            "name": "ozon_upload", "status": "completed",
            "started_at": now(), "finished_at": now(),
            "retry_count": int((status.get("retry_count_by_step") or {}).get("ozon_upload", 0)),
            "retryable": True, "error": None,
        })
    # After the explicit cutover SQLite is the only mutable task-state source.
    # status.json remains a compatibility snapshot for rollback and legacy
    # readers; it is not updated by asynchronous recovery.
    # Use the caller's root so isolated/temp runs do not accidentally inherit
    # the production repository's cutover marker.
    state_root = root or product_dir.parents[1]
    if not cutover_active(state_root):
        write_json_atomic(product_dir / "status.json", status)
    return status


def default_recovery_runner(root: Path, isolated: Path, store_id: str) -> Dict[str, Any]:
    env = dict(os.environ, UPLOAD_MODE="production")
    _load_env_file(root / "ozon-adapter" / f".env.{store_id}", env)
    log_path = isolated / "logs/store-recovery.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        completed = subprocess.run(
            [
                sys.executable, str(root / "scripts/recover_ozon_results.py"),
                "--product-dir", str(isolated), "--shop", store_id, "--timeout", "1",
            ],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=False,
        )
    status = load_json(isolated / "status.json")
    result_path = isolated / "output/ozon-result.json"
    idempotency_path = isolated / "output/ozon-idempotency.json"
    return {
        "returncode": completed.returncode, "status": status,
        "result": load_json(result_path) if result_path.is_file() else {},
        "idempotency": load_json(idempotency_path) if idempotency_path.is_file() else {},
    }


def refresh_pending_stores(
    root: Path,
    product_dir: Path,
    runner: Optional[Callable[[Path, Path, str], Dict[str, Any]]] = None,
    only_store_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    publications = load_publications(product_dir)
    recover = runner or default_recovery_runner
    only = {str(item) for item in only_store_ids} if only_store_ids is not None else None
    checked = []
    for store_id, record in (publications.get("stores") or {}).items():
        if only is not None and str(store_id) not in only:
            continue
        if str(record.get("status") or "") not in PENDING_STATES:
            continue
        isolated = store_workspace(root, product_dir.name, store_id) / "products" / product_dir.name
        if not (isolated / "status.json").is_file():
            record["last_error"] = "店铺异步任务工作区缺失，保持处理中并禁止重传"
            record["last_checked_at"] = now()
            continue
        try:
            outcome = recover(root, isolated, store_id)
        except Exception as exc:
            record["last_error"] = f"只读状态查询失败：{exc}"
            record["last_checked_at"] = now()
            continue
        _persist_store_artifacts(product_dir, store_id, isolated)
        _store_result(record, outcome, increment_version=False)
        checked.append({"store_id": store_id, "status": record["status"]})
    save_publications(product_dir, publications)
    status = aggregate_product_status(product_dir, publications, root)
    return {
        "product_id": product_dir.name, "checked": checked, "status": status["status"],
        "write_api_calls": 0, "inventory_api_calls": 0,
    }


def execute_selected_stores(
    root: Path,
    product_dir: Path,
    only_store_ids: Optional[Iterable[str]] = None,
    runner: Optional[Callable[[Path, Path, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    # Allocate every selected store/SKU article in one persisted pass before
    # creating a workspace or allowing the first Ozon request.
    publications = ensure_store_offer_ids(product_dir)
    only = set(only_store_ids or [])
    run = runner or default_runner
    attempted = []
    skipped = []
    for store_id, record in (publications.get("stores") or {}).items():
        if not record.get("selected") or (only and store_id not in only):
            continue
        current = str(record.get("status") or "")
        if current in PENDING_STATES or current in SUCCESS_STATES:
            skipped.append({"store_id": store_id, "reason": "already_submitted_or_pending"})
            continue
        if not definitely_retryable(record):
            skipped.append({"store_id": store_id, "reason": "ambiguous_state_blocks_resubmit"})
            continue
        isolated = prepare_isolated_product(root, product_dir, store_id, record)
        record["status"] = "UPLOADING"
        save_publications(product_dir, publications)
        try:
            outcome = run(root, isolated, store_id)
        except Exception as exc:
            outcome = {"returncode": 1, "status": {"status": "FAILED_HARD_BLOCKER", "api_write_count": 0, "error_message": str(exc)}, "result": {}}
        _persist_store_artifacts(product_dir, store_id, isolated)
        _store_result(record, outcome)
        if credential_failure(record.get("last_error")):
            try:
                mark_store_validation_failed(root, store_id, str(record.get("last_error")))
            except KeyError:
                pass
        save_publications(product_dir, publications)
        attempted.append({"store_id": store_id, "status": record["status"], "api_write_count": record.get("api_write_count", 0)})
    status = aggregate_product_status(product_dir, publications, root)
    return {
        "product_id": product_dir.name, "attempted": attempted, "skipped": skipped,
        "status": status["status"], "api_write_count": status["api_write_count"],
        "inventory_api_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent per-store Ozon uploader")
    parser.add_argument("product_dir")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--only-store", action="append", default=[])
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("--execute is required")
    product_dir = Path(args.product_dir).resolve()
    result = execute_selected_stores(ROOT, product_dir, args.only_store or None)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(item["status"] != "FAILED" for item in result["attempted"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
