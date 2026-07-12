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
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

try:
    from pipeline_runtime import load_json, normalize_checkpoint, now, write_json_atomic
    from store_publications import load_publications, save_publications
except ModuleNotFoundError:  # Imported as scripts.multi_store_upload by tests/tools.
    from scripts.pipeline_runtime import load_json, normalize_checkpoint, now, write_json_atomic
    from scripts.store_publications import load_publications, save_publications


ROOT = Path(__file__).resolve().parents[1]
PENDING_STATES = {"QUEUED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"}
SUCCESS_STATES = {"SUCCESS", "IMPORTED", "UPLOADED", "ACTIVE"}
RETRYABLE_STATES = {"SELECTED", "FAILED", "QUERY_ERROR"}
STORE_ARTIFACTS = (
    "ozon-result.json", "ozon-write-receipt.json", "ozon-idempotency.json",
    "ozon-last-upload-hashes.json", "product-exists-check.json",
    "ozon-upload-payload.json", "ozon-images.json", "ozon-image-transfer.json",
    "ozon-preflight.json", "ozon-update-request-summary.json",
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


def prepare_isolated_product(root: Path, product_dir: Path, store_id: str, publication: Mapping[str, Any]) -> Path:
    workspace = store_workspace(root, product_dir.name, store_id)
    isolated = workspace / "products" / product_dir.name
    if workspace.exists():
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
    return isolated


def default_runner(root: Path, isolated: Path, store_id: str) -> Dict[str, Any]:
    env = dict(os.environ, UPLOAD_MODE="production")
    _load_env_file(root / "ozon-adapter" / f".env.{store_id}", env)
    log_path = isolated / "logs/store-upload.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        completed = subprocess.run(
            [sys.executable, str(root / "ozon-uploader/cli.py"), str(isolated), "--shop", store_id, "--execute"],
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
    elif raw_status in {"PENDING_REMOTE", "OZON_MODERATION", "UPLOADING"} or write_count > 0:
        store_status = "PENDING_REMOTE"
    else:
        store_status = "FAILED"
    items = result.get("items") or []
    by_sku = {str(item.get("source_sku_id") or item.get("sku_id") or ""): item for item in items}
    action = str(result.get("action") or result.get("upload_action") or "UNKNOWN").upper()
    errors = result.get("errors") or (status.get("ozon") or {}).get("errors") or []
    for sku in record.get("sku_publications") or []:
        item = by_sku.get(str(sku.get("sku_id"))) or (items[0] if len(items) == 1 else {})
        sku.update({
            "offer_id": item.get("offer_id") or sku.get("offer_id") or "unknown",
            "action": str(item.get("action") or action or sku.get("action") or "UNKNOWN").upper(),
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


def aggregate_product_status(product_dir: Path, publications: Mapping[str, Any]) -> Dict[str, Any]:
    status = normalize_checkpoint(load_json(product_dir / "status.json"))
    selected = [item for item in (publications.get("stores") or {}).values() if item.get("selected")]
    states = {str(item.get("status") or "") for item in selected}
    total_writes = sum(int(item.get("api_write_count") or 0) for item in selected)
    if states and states <= SUCCESS_STATES:
        target, error = "UPLOADED", "unknown"
    elif states & PENDING_STATES:
        target, error = "PENDING_REMOTE", "unknown"
    elif states & {"FAILED", "QUERY_ERROR"}:
        target, error = "FAILED_HARD_BLOCKER", "一家或多家店铺上传失败；只允许重试失败店铺"
    else:
        target, error = "OZON_READY", "unknown"
    status.update({
        "status": target, "current_step": "ozon_upload", "active_step": None,
        "progress": 100 if target in {"UPLOADED", "PENDING_REMOTE"} else 95,
        "api_write_count": total_writes, "last_run_at": now(),
        "error_message": error, "failed_step": "ozon_upload" if target == "FAILED_HARD_BLOCKER" else "unknown",
        "next_action": "retry_failed_store" if target == "FAILED_HARD_BLOCKER" else "read_only_status_query" if target == "PENDING_REMOTE" else "complete",
    })
    status.pop("target_store_ids_for_run", None)
    if target in {"UPLOADED", "PENDING_REMOTE"} and "ozon_upload" not in status["completed_steps"]:
        status["completed_steps"].append("ozon_upload")
        status["pending_steps"] = [step for step in status["pending_steps"] if step != "ozon_upload"]
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
) -> Dict[str, Any]:
    publications = load_publications(product_dir)
    recover = runner or default_recovery_runner
    checked = []
    for store_id, record in (publications.get("stores") or {}).items():
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
    status = aggregate_product_status(product_dir, publications)
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
    publications = load_publications(product_dir)
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
        save_publications(product_dir, publications)
        attempted.append({"store_id": store_id, "status": record["status"], "api_write_count": record.get("api_write_count", 0)})
    status = aggregate_product_status(product_dir, publications)
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
