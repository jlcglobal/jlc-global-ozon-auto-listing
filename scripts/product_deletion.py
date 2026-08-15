"""Permanent local product deletion with task invalidation and reference cleanup."""
from __future__ import annotations

import json
import os
import shutil
import signal
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


IDENTITY_KEYS = {"product_id", "local_product_id", "internal_product_id"}
PROCESSING_STATES = {
    "QUEUED", "RUNNING", "PROCESSING", "CATEGORY_MATCHED", "COPY_READY",
    "IMAGES_READY", "QC_PASSED", "READY_TO_UPLOAD", "UPLOADING",
}
REMOTE_STATES = {"PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE", "IMPORTED"}
DROP = object()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def deletion_marker_path(root: Path, product_id: str) -> Path:
    return root / "logs/product-deletion-tombstones" / f"{product_id}.deleted"


def deletion_requested(root: Path, product_id: str) -> bool:
    return deletion_marker_path(root, product_id).is_file()


def mark_deletion_requested(root: Path, product_id: str) -> Path:
    marker = deletion_marker_path(root, product_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if not marker.is_file():
        marker.write_text(now() + "\n", encoding="utf-8")
    return marker


def _terminate_pid(pid: Any) -> bool:
    try:
        value = int(pid)
        os.kill(value, 0)
    except (OSError, TypeError, ValueError):
        return False
    try:
        os.killpg(value, signal.SIGTERM)
    except OSError:
        try:
            os.kill(value, signal.SIGTERM)
        except OSError:
            return False
    for _ in range(20):
        try:
            os.kill(value, 0)
        except OSError:
            return True
        time.sleep(0.05)
    try:
        os.killpg(value, signal.SIGKILL)
    except OSError:
        try:
            os.kill(value, signal.SIGKILL)
        except OSError:
            pass
    return True


def cancel_product_workers(root: Path, product_id: str, product_dir: Path) -> List[int]:
    stopped: List[int] = []
    worker_state = load_json(root / "logs/product-workers" / f"{product_id}.json")
    worker_pid = worker_state.get("pid")
    if worker_pid is not None and _terminate_pid(worker_pid):
        stopped.append(int(worker_pid))
    channel_state = load_json(product_dir / "output/image-channel-state.json")
    channel_pid = channel_state.get("worker_pid")
    if channel_pid is not None and _terminate_pid(channel_pid):
        stopped.append(int(channel_pid))
    for pid_path in root.glob(f"logs/{product_id}-*.pid"):
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, TypeError, ValueError):
            pid = 0
        if _terminate_pid(pid):
            stopped.append(pid)
        pid_path.unlink(missing_ok=True)
    (root / "logs/product-workers" / f"{product_id}.json").unlink(missing_ok=True)
    return sorted(set(stopped))


def _contains_identity(value: Any, fingerprints: Iterable[str]) -> bool:
    needles = {item for item in fingerprints if item}
    if isinstance(value, dict):
        return any(_contains_identity(key, needles) or _contains_identity(item, needles) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_identity(item, needles) for item in value)
    if isinstance(value, str):
        return any(needle in value for needle in needles)
    return False


def _filter_product(value: Any, product_id: str) -> Tuple[Any, int]:
    if isinstance(value, dict):
        if any(str(value.get(key) or "") == product_id for key in IDENTITY_KEYS):
            return DROP, 1
        result: Dict[str, Any] = {}
        removed = 0
        for key, item in value.items():
            if str(key) == product_id:
                removed += 1
                continue
            filtered, count = _filter_product(item, product_id)
            removed += count
            if filtered is not DROP:
                result[key] = filtered
        return result, removed
    if isinstance(value, list):
        result = []
        removed = 0
        for item in value:
            filtered, count = _filter_product(item, product_id)
            removed += count
            if filtered is not DROP:
                result.append(filtered)
        return result, removed
    return value, 0


def _refresh_aggregate_counts(value: Dict[str, Any]) -> Dict[str, Any]:
    products = value.get("products")
    if not isinstance(products, list):
        return value
    value["product_count"] = len(products)
    value["sku_count"] = sum(int(item.get("selected_sku_count") or 0) for item in products if isinstance(item, dict))
    value["success_count"] = sum(str(item.get("status") or "") in {"UPLOADED", "OZON_MODERATION", "ACTIVE", "SUCCESS", "IMPORTED"} for item in products if isinstance(item, dict))
    value["failed_count"] = sum("FAIL" in str(item.get("status") or "") for item in products if isinstance(item, dict))
    value["processing_count"] = sum(str(item.get("status") or "") in PROCESSING_STATES for item in products if isinstance(item, dict))
    value["pending_remote_count"] = sum(str(item.get("status") or "") == "PENDING_REMOTE" for item in products if isinstance(item, dict))
    value["submitted_count"] = sum(int(item.get("api_write_count") or 0) > 0 for item in products if isinstance(item, dict))
    return value


def clean_json_reference(path: Path, product_id: str) -> int:
    value = load_json(path, None)
    if value is None:
        return 0
    filtered, removed = _filter_product(value, product_id)
    if not removed:
        return 0
    if filtered is DROP:
        path.unlink(missing_ok=True)
        return removed
    if isinstance(filtered, dict):
        filtered = _refresh_aggregate_counts(filtered)
    write_json_atomic(path, filtered)
    return removed


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def purge_local_product(root: Path, product_id: str) -> Dict[str, Any]:
    if not product_id.startswith("P") or len(product_id) != 7 or not product_id[1:].isdigit():
        raise ValueError("Invalid product id")
    root = root.resolve()
    product_dir = root / "products" / product_id
    source = load_json(product_dir / "input/source.json")
    fingerprints = {product_id, str(source.get("source_url") or "")}
    for group in (source.get("main_images") or [], source.get("detail_images") or [], source.get("skus") or []):
        if isinstance(group, dict):
            fingerprints.update(str(value) for value in group.values() if isinstance(value, str))

    mark_deletion_requested(root, product_id)
    stopped_pids = cancel_product_workers(root, product_id, product_dir)
    removed_records = 0
    removed_paths: List[str] = []
    failures: List[str] = []

    data_json_paths = [
        root / "remote-pending-queue.json",
        root / "image-channel-queue.json",
        root / "batch-result.json",
        root / "logs/current-batch.json",
    ]
    data_json_paths.extend((root / "batches").glob("B-*/*.json"))
    for path in data_json_paths:
        if not path.is_file():
            continue
        try:
            removed_records += clean_json_reference(path, product_id)
        except Exception as exc:
            failures.append(f"{path}: {type(exc).__name__}: {exc}")

    cache_root = root / "cache"
    for path in cache_root.rglob("*") if cache_root.is_dir() else []:
        if not path.is_file():
            continue
        try:
            remove = product_id in path.name
            if path.suffix.lower() == ".json":
                remove = remove or _contains_identity(load_json(path), fingerprints)
            if remove:
                path.unlink()
                removed_paths.append(str(path.relative_to(root)))
        except Exception as exc:
            failures.append(f"{path}: {type(exc).__name__}: {exc}")

    deleted_root = root / "logs/deleted-products"
    for path in deleted_root.glob(f"*-{product_id}") if deleted_root.is_dir() else []:
        try:
            shutil.rmtree(path)
            removed_paths.append(str(path.relative_to(root)))
        except Exception as exc:
            failures.append(f"{path}: {type(exc).__name__}: {exc}")

    logs_root = root / "logs"
    for path in logs_root.iterdir() if logs_root.is_dir() else []:
        if not path.is_file() or path.parent.name == "product-deletion-tombstones":
            continue
        try:
            if product_id in path.name:
                path.unlink()
                removed_paths.append(str(path.relative_to(root)))
            elif path.suffix in {".log", ".jsonl"}:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                retained = [line for line in lines if product_id not in line]
                if len(retained) != len(lines):
                    path.write_text("\n".join(retained) + ("\n" if retained else ""), encoding="utf-8")
                    removed_records += len(lines) - len(retained)
        except Exception as exc:
            failures.append(f"{path}: {type(exc).__name__}: {exc}")

    try:
        if product_dir.exists():
            shutil.rmtree(product_dir)
            removed_paths.append(str(product_dir.relative_to(root)))
    except Exception as exc:
        failures.append(f"{product_dir}: {type(exc).__name__}: {exc}")

    for batch_dir in (root / "batches").glob("B-*") if (root / "batches").is_dir() else []:
        _remove_empty_parents(batch_dir, root / "batches")
    _remove_empty_parents(cache_root / "image-recognition", cache_root)

    # A second queue pass closes races with a monitor that had already read its snapshot.
    for path in (root / "remote-pending-queue.json", root / "image-channel-queue.json"):
        try:
            if path.is_file():
                removed_records += clean_json_reference(path, product_id)
        except Exception as exc:
            failures.append(f"{path}: {type(exc).__name__}: {exc}")

    return {
        "status": "deleted" if not failures else "partial_failure",
        "product_id": product_id,
        "removed_records": removed_records,
        "removed_paths": removed_paths,
        "stopped_product_pids": stopped_pids,
        "failures": failures,
        "local_product_exists": product_dir.exists(),
        "ozon_write_api_calls": 0,
        "ozon_delete_api_calls": 0,
        "inventory_api_calls": 0,
    }
