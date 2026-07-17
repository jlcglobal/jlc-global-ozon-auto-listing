"""Per-store publication records backed by one shared product master."""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def publication_path(product_dir: Path) -> Path:
    return product_dir / "output/store-publications.json"


def base_sku_records(product_dir: Path) -> List[Dict[str, Any]]:
    source = load(product_dir / "input/source.json")
    pricing = load(product_dir / "output/pricing-result.json")
    by_sku = {str(item.get("sku_id")): item for item in pricing.get("sku_pricing") or []}
    records = []
    for sku in source.get("skus") or []:
        sku_id = str(sku.get("sku_id") or "unknown")
        price = by_sku.get(sku_id) or {}
        records.append({
            "sku_id": sku_id,
            "initial_price_cny": price.get("selling_price_cny"),
            "initial_price_rub": price.get("selling_price_rub"),
            "profit_cny": price.get("estimated_profit_cny"),
            "offer_id": "unknown",
            "action": "UNKNOWN",
            "task_id": "unknown",
            "ozon_product_id": "unknown",
            "payload_hash": "unknown",
            "moderation_status": "not_submitted",
            "errors": [], "warnings": [],
        })
    return records


def default_record(product_dir: Path, store_id: str) -> Dict[str, Any]:
    return {
        "product_internal_id": product_dir.name,
        "store_id": store_id,
        "selected": False,
        "status": "NOT_SELECTED",
        "submission_version": 0,
        "last_submitted_at": None,
        "last_checked_at": None,
        "title_override": None,
        "image_version_override": None,
        "store_notes": "",
        "has_store_overrides": False,
        "sku_publications": base_sku_records(product_dir),
    }


def load_publications(product_dir: Path, store_ids: Iterable[str] = ()) -> Dict[str, Any]:
    try:
        try:
            from task_database import cutover_active, publications_from_db
        except ModuleNotFoundError:
            from scripts.task_database import cutover_active, publications_from_db
        root = product_dir.parents[1]
        if cutover_active(root):
            projected = publications_from_db(root, product_dir, store_ids)
            if projected is not None:
                return projected
    except Exception:
        # A malformed legacy record must remain readable during rollback.
        pass
    path = publication_path(product_dir)
    data = load(path, {"schema_version": "1.0.0", "product_id": product_dir.name, "stores": {}})
    data.setdefault("stores", {})
    for store_id in store_ids:
        data["stores"].setdefault(store_id, default_record(product_dir, store_id))
    return data


def save_publications(product_dir: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    data.update({"schema_version": "1.0.0", "product_id": product_dir.name, "updated_at": now()})
    root = product_dir.parents[1]
    try:
        try:
            from task_database import cutover_active, sync_publications_json
        except ModuleNotFoundError:
            from scripts.task_database import cutover_active, sync_publications_json
        if cutover_active(root):
            backup = publication_path(product_dir).with_name("store-publications.json.readonly-backup")
            if publication_path(product_dir).is_file() and not backup.exists():
                backup.write_bytes(publication_path(product_dir).read_bytes())
            sync_publications_json(root, product_dir, data)
            return data
        # Migration window: JSON is still written for rollback, while SQLite
        # receives the same projection. The explicit cutover marker ends this.
        write(publication_path(product_dir), data)
        sync_publications_json(root, product_dir, data)
    except Exception:
        if not cutover_active(root):
            write(publication_path(product_dir), data)
    return data


def select_stores(
    product_dir: Path,
    selected_store_ids: Iterable[str],
    available_store_ids: Iterable[str],
    overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    available = list(dict.fromkeys(available_store_ids))
    selected = set(selected_store_ids)
    unknown = selected - set(available)
    if unknown:
        raise ValueError("未知或不可用店铺：" + "、".join(sorted(unknown)))
    if not selected:
        raise ValueError("至少选择一家已验证且启用的店铺")
    data = load_publications(product_dir, available)
    overrides = overrides or {}
    for store_id in available:
        record = data["stores"][store_id]
        record["selected"] = store_id in selected
        if record["selected"] and record.get("status") in {"NOT_SELECTED", "FAILED", "QUERY_ERROR"}:
            record["status"] = "SELECTED"
        elif not record["selected"] and record.get("status") in {"NOT_SELECTED", "SELECTED", "QUEUED"}:
            record["status"] = "NOT_SELECTED"
        store_override = dict(overrides.get(store_id) or {})
        for field in ("title_override", "image_version_override", "store_notes"):
            if field in store_override:
                record[field] = store_override[field] or None
        price_overrides = store_override.get("sku_prices") or {}
        price_overrides_cny = store_override.get("sku_prices_cny") or {}
        for sku in record.get("sku_publications") or []:
            sku_id = str(sku["sku_id"])
            if sku_id in price_overrides:
                sku["initial_price_rub"] = float(price_overrides[sku_id])
            if sku_id in price_overrides_cny:
                sku["price_override_cny"] = float(price_overrides_cny[sku_id])
        record["has_store_overrides"] = bool(
            record.get("title_override") or record.get("image_version_override")
            or record.get("store_notes") or price_overrides or price_overrides_cny
        )
    return save_publications(product_dir, data)


def publication_summary(data: Dict[str, Any]) -> Dict[str, int]:
    records = list((data.get("stores") or {}).values())
    return {
        "selected": sum(bool(item.get("selected")) for item in records),
        "success": sum(str(item.get("status")) in {"SUCCESS", "IMPORTED", "ACTIVE"} for item in records),
        "pending": sum(str(item.get("status")) in {"QUEUED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"} for item in records),
        "failed": sum(str(item.get("status")) == "FAILED" for item in records),
        "skipped": sum(str(item.get("status")) == "SKIPPED" for item in records),
    }


def update_store_result(
    product_dir: Path, store_id: str, status: str, sku_results: List[Mapping[str, Any]],
    submission_version: Optional[int] = None,
) -> Dict[str, Any]:
    data = load_publications(product_dir, [store_id])
    record = data["stores"][store_id]
    by_offer = {str(item.get("offer_id") or ""): item for item in sku_results}
    for sku in record.get("sku_publications") or []:
        match = next((item for item in sku_results if str(item.get("sku_id") or "") == str(sku["sku_id"])), None)
        if match is None and len(sku_results) == 1:
            match = sku_results[0]
        if match:
            sku.update({
                "offer_id": match.get("offer_id") or sku.get("offer_id"),
                "action": str(match.get("action") or match.get("upload_action") or sku.get("action") or "UNKNOWN").upper(),
                "task_id": str(match.get("task_id") or "unknown"),
                "ozon_product_id": str(match.get("product_id") or match.get("ozon_product_id") or "unknown"),
                "payload_hash": match.get("payload_hash") or "unknown",
                "moderation_status": match.get("moderation_status") or status.lower(),
                "errors": match.get("errors") or [], "warnings": match.get("warnings") or [],
            })
    record.update({
        "selected": True, "status": status,
        "submission_version": submission_version or int(record.get("submission_version") or 0) + 1,
        "last_submitted_at": now() if status not in {"SELECTED", "QUEUED"} else record.get("last_submitted_at"),
        "last_checked_at": now(),
    })
    return save_publications(product_dir, data)


def final_snapshot(product_dir: Path, store_ids: Iterable[str], batch_id: str) -> Dict[str, Any]:
    files = {}
    for name in ("copy-ru.json", "ozon-category.json", "ozon-attributes.json", "pricing-result.json", "ozon-draft.json", "rich-content.json"):
        path = product_dir / "output" / name
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot = {
        "product_id": product_dir.name, "batch_id": batch_id,
        "store_ids": list(store_ids), "created_at": now(),
        "file_hashes": files, "inventory_fields_included": False,
    }
    write(product_dir / "output/final-submission-snapshot.json", snapshot)
    return snapshot


def reconcile_update_version(record: Mapping[str, Any], remote_version: int, local_version: int) -> Dict[str, Any]:
    """Decide a bounded correction for out-of-order UPDATE completion."""
    corrections = int(record.get("version_correction_count") or 0)
    if remote_version >= local_version:
        return {"action": "MATCHED", "correction_required": False, "manual_review": False, "version_correction_count": corrections}
    if corrections == 0:
        return {"action": "UPDATE_LATEST_ONCE", "correction_required": True, "manual_review": False, "version_correction_count": 1, "target_version": local_version}
    return {"action": "MANUAL_REVIEW", "correction_required": False, "manual_review": True, "version_correction_count": corrections, "reason": "自动校正已执行一次，禁止循环UPDATE和重新CREATE"}
