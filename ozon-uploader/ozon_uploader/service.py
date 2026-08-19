"""Build, gate, submit, and persist one Ozon product-import task."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jsonschema import Draft202012Validator

from .client import OzonUploadApiError, OzonWriteClient
from .image_channels import PersistentImageTunnel, channel_state, stop_image_channel
from .images import ImageTunnelError, stage_images

try:
    from scripts.image_asset_boundaries import (
        accepted_counterpart,
        asset_contract_path,
        validate_accepted_manifest,
        validate_generated_output,
    )
    from scripts.production_input_guard import ProductionInputError, validate_formal_product_input
    from scripts.russian_color_rules import (
        is_color_name_attribute,
        normalize_russian_color_name,
    )
    from scripts.russian_seo_rules import canonical_hashtag
except ModuleNotFoundError:  # package execution from the repository root
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from image_asset_boundaries import (
        accepted_counterpart,
        asset_contract_path,
        validate_accepted_manifest,
        validate_generated_output,
    )
    from production_input_guard import ProductionInputError, validate_formal_product_input
    from russian_color_rules import (
        is_color_name_attribute,
        normalize_russian_color_name,
    )
    from russian_seo_rules import canonical_hashtag


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
SCHEMAS = {
    "config": TEMPLATES / "ozon-upload-config.schema.json",
    "images": TEMPLATES / "ozon-images.schema.json",
    "preflight": TEMPLATES / "ozon-upload-preflight.schema.json",
    "result": TEMPLATES / "ozon-result.schema.json",
    "draft": TEMPLATES / "ozon-draft.schema.json",
    "status": TEMPLATES / "status.schema.json",
    "tags": TEMPLATES / "ozon-tags.schema.json",
    "attributes_final": TEMPLATES / "ozon-attributes-final.schema.json",
    "rich_content": TEMPLATES / "rich-content.schema.json",
    "color_variants": TEMPLATES / "color-variants.schema.json",
    "color_variant_policy": TEMPLATES / "color-variant-policy.schema.json",
    "upload_payload": TEMPLATES / "ozon-upload-payload.schema.json",
    "exists_check": TEMPLATES / "product-exists-check.schema.json",
    "variant_grouping": TEMPLATES / "variant-grouping-result.schema.json",
    "grouping_verification": TEMPLATES / "grouping-verification.schema.json",
}


class UploadGateError(RuntimeError):
    pass


def _verify_public_image_url(url: str, *, timeout: int = 20) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 crossborder-ai-factory-ozon-image-preflight/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            content_type = str(response.headers.get("Content-Type") or "").casefold()
            head = response.read(2048)
            if status != 200:
                return {"ok": False, "reason": f"HTTP {status}"}
            if "text/html" in content_type or head.lstrip().lower().startswith(b"<!doctype html"):
                return {"ok": False, "reason": "returned HTML instead of an image"}
            if len(head) < 32:
                return {"ok": False, "reason": "image response is too small"}
            if content_type and "image/" not in content_type and "octet-stream" not in content_type:
                return {"ok": False, "reason": f"unexpected content-type {content_type}"}
            return {"ok": True, "status": status, "content_type": content_type or "unknown"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "reason": str(exc)}


def _is_local_public_image_probe_failure(reason: Any) -> bool:
    """Local TLS/proxy probe failures are diagnostics, not proof Ozon cannot fetch."""
    text = str(reason or "").casefold()
    markers = (
        "ssl: unexpected_eof_while_reading",
        "unexpected eof while reading",
        "eof occurred in violation of protocol",
        "ssl_error_syscall",
        "libressl ssl_connect",
        "openssl ssl_connect",
        "tlsv1 alert internal error",
    )
    return any(marker in text for marker in markers)


def verify_public_image_urls(manifest: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    local_probe_unavailable_reason: str | None = None
    for item in manifest.get("images") or []:
        url = str(item.get("public_url") or "")
        if not url.startswith("https://"):
            failures.append(f"{item.get('slot') or item.get('staged_name')}: missing HTTPS public image URL")
            continue
        if local_probe_unavailable_reason:
            item["status"] = "served"
            item["error"] = (
                "local public image probe skipped after this image channel "
                f"failed local TLS verification: {local_probe_unavailable_reason}"
            )
            continue
        result = _verify_public_image_url(url)
        if not result.get("ok"):
            reason = result.get("reason") or "unreachable"
            if _is_local_public_image_probe_failure(reason):
                local_probe_unavailable_reason = str(reason)
                item["status"] = "served"
                item["error"] = f"local public image probe unavailable: {reason}"
                continue
            item["status"] = "failed"
            item["error"] = f"public image download failed: {reason}"
            failures.append(
                f"{item.get('slot') or item.get('staged_name')}: {reason}"
            )
    return failures


def ozon_weight_grams(value: Any) -> int:
    """Return the conservative integer grams required by Ozon's int32 field."""
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        raise UploadGateError("Package weight must be a positive number of grams") from exc
    if not math.isfinite(weight) or weight <= 0:
        raise UploadGateError("Package weight must be a positive number of grams")
    # Shipping weight must never be understated when an estimator produced a
    # fractional gram, and Ozon rejects a JSON float for this int32 field.
    return int(math.ceil(weight))


def ozon_dimension_mm(value: Any) -> int:
    """Return conservative integer millimeters required by Ozon dimension fields."""
    try:
        dimension = float(value)
    except (TypeError, ValueError) as exc:
        raise UploadGateError("Package dimensions must be positive millimeters") from exc
    if not math.isfinite(dimension) or dimension <= 0:
        raise UploadGateError("Package dimensions must be positive millimeters")
    return int(math.ceil(dimension))


def upload_mode() -> str:
    mode = os.environ.get("UPLOAD_MODE", "dry-run").strip().lower()
    if mode not in {"dry-run", "production"}:
        raise UploadGateError("UPLOAD_MODE must be dry-run or production")
    return mode


def require_production_mode() -> None:
    if upload_mode() != "production":
        raise UploadGateError(
            "Ozon API writes are disabled because UPLOAD_MODE is not production"
        )


def _is_ozon_reference_draft_upload(product_dir: Path) -> bool:
    source_path = product_dir / "input/source.json"
    status_path = product_dir / "status.json"
    if not source_path.is_file() or not status_path.is_file():
        return False
    try:
        source = load_json(source_path)
        status = load_json(status_path)
    except Exception:
        return False
    return (
        source.get("source_kind") == "ozon_reference_draft"
        and str(status.get("status") or "").upper() in {
            "OZON_REFERENCE_CARD_READY",
            "WAITING_MANUAL_REVIEW",
            "UPLOADING",
            "PENDING_REMOTE",
            "OZON_MODERATION",
            "UPLOADED",
            "CREATED",
            "UPDATED",
            "ACTIVE",
        }
    )


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SCALAR_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")


def _remote_content_blockers(
    api_items: List[Dict[str, Any]],
    category_metadata: Dict[str, Any],
) -> List[str]:
    """Reject content Ozon will moderate or parse incorrectly before writing.

    Only remote-visible request fields are inspected.  Internal Chinese source
    evidence remains available in local artifacts and audit logs.
    """
    attribute_types = {
        int(item["attribute_id"]): str(item.get("type") or "").casefold()
        for item in category_metadata.get("attributes") or []
        if item.get("attribute_id") is not None
    }
    blockers: List[str] = []

    def inspect_text(value: Any, location: str) -> None:
        text = str(value or "")
        if _CJK_RE.search(text):
            blockers.append(f"{location} 含中文，禁止提交到 Ozon")
        if _CONTROL_RE.search(text):
            blockers.append(f"{location} 含不可见控制字符，禁止提交到 Ozon")
        if re.search(r"(?:^|\s)(?:input|output)/\S+\.(?:jpe?g|png|webp)\b", text, re.IGNORECASE):
            blockers.append(f"{location} 含本地图片路径，禁止提交到 Ozon")

    for item in api_items:
        offer_id = str(item.get("offer_id") or "unknown")
        inspect_text(item.get("name"), f"商品 {offer_id} 的标题")
        inspect_text(item.get("description"), f"商品 {offer_id} 的简介")
        for attribute in item.get("attributes") or []:
            attribute_id = int(attribute.get("id") or 0)
            value_type = attribute_types.get(attribute_id, "")
            for value in attribute.get("values") or []:
                raw_value = value.get("value")
                location = f"商品 {offer_id} 的属性 {attribute_id}"
                inspect_text(raw_value, location)
                if (
                    value_type in {"decimal", "integer", "int32", "int64"}
                    and value.get("dictionary_value_id") is None
                    and not _SCALAR_NUMBER_RE.fullmatch(str(raw_value or "").strip())
                ):
                    blockers.append(f"{location} 必须只填写数字，不能带单位或说明文字")
    return list(dict.fromkeys(blockers))


def _resolve_product_artifact(product_dir: Path, value: Any) -> Path:
    """Resolve an image-plan path without allowing it to escape the project."""
    raw = Path(str(value or ""))
    project_root = product_dir.parent.parent
    if raw.is_absolute():
        candidate = raw.resolve()
    elif raw.parts and raw.parts[0] == "products":
        candidate = (project_root / raw).resolve()
    else:
        candidate = (product_dir / raw).resolve()
    allowed = product_dir.resolve()
    if candidate != allowed and allowed not in candidate.parents:
        raise UploadGateError(f"Image-plan path escapes product directory: {value}")
    return candidate


def _image_file_is_readable(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        from PIL import Image
    except ImportError:
        # The uploader can still run in the minimal runtime; non-empty files
        # are the strongest check available when Pillow is not installed.
        return True
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def current_image_completeness(product_dir: Path) -> Dict[str, Any]:
    """Re-check current image-plan slots and files immediately before upload.

    This deliberately ignores all historical reports.  A stale PASS must never
    allow a CREATE/UPDATE after images were removed or replaced.
    """
    output = product_dir / "output"
    plan = load_json(output / "image-plan.json") if (output / "image-plan.json").is_file() else {}
    draft = load_json(output / "ozon-draft.json") if (output / "ozon-draft.json").is_file() else {}
    qc_report = load_json(output / "image-qc-report.json") if (output / "image-qc-report.json").is_file() else {}
    selected_skus = [str(item.get("source_sku_id")) for item in draft.get("skus") or []]
    planned_main = list(plan.get("main_images") or [])
    planned_detail = list(plan.get("detail_images") or [])
    errors: List[str] = []
    main_results: List[Dict[str, Any]] = []
    detail_results: List[Dict[str, Any]] = []
    asset_contract = load_json(asset_contract_path(product_dir)) if asset_contract_path(product_dir).is_file() else {}
    manual_confirmation_required = bool(asset_contract.get("manual_confirmation_required"))
    qc_passed_slots: set[tuple[str, str]] = set()
    if str(qc_report.get("decision") or "").lower() == "pass":
        for checked in qc_report.get("images_checked") or []:
            try:
                checked_path = _resolve_product_artifact(product_dir, checked.get("path")).resolve()
            except UploadGateError:
                continue
            qc_passed_slots.add((str(checked.get("slot") or "unknown"), str(checked_path)))

    def inspect(item: Dict[str, Any], label: str) -> Dict[str, Any]:
        path_value = item.get("output_path") or item.get("path") or item.get("local_path")
        try:
            path = _resolve_product_artifact(product_dir, path_value)
            if asset_contract:
                path = validate_generated_output(product_dir, path)
                if manual_confirmation_required:
                    path = accepted_counterpart(product_dir, path)
            readable = _image_file_is_readable(path)
        except UploadGateError as exc:
            path = Path(str(path_value or "unknown"))
            readable = False
            errors.append(str(exc))
        status = str(item.get("status") or "").lower()
        status_ok = status in {"", "generated", "ready", "pass", "passed", "complete"}
        if not status_ok and status == "planned":
            status_ok = (str(item.get("slot") or "unknown"), str(path.resolve())) in qc_passed_slots
        passed = readable and status_ok
        if not passed:
            errors.append(f"{label}: image file is missing, damaged, or not generation-complete ({path_value or 'unknown'})")
        return {
            "slot": str(item.get("slot") or "unknown"), "path": str(path), "passed": passed,
            "asset_state": "accepted" if asset_contract and manual_confirmation_required else "candidate",
        }

    for sku_id in selected_skus:
        candidates = [item for item in planned_main if str(item.get("source_sku_id") or "") == sku_id]
        if len(candidates) != 1:
            errors.append(f"SKU {sku_id}: expected exactly one planned main image, got {len(candidates)}")
            main_results.append({"sku_id": sku_id, "passed": False})
            continue
        result = inspect(candidates[0], f"SKU {sku_id} main image")
        result["sku_id"] = sku_id
        main_results.append(result)

    if len(planned_detail) != 8:
        errors.append(f"detail image count is {len(planned_detail)}; exactly 8 are required")
    for item in planned_detail:
        detail_results.append(inspect(item, f"detail {item.get('slot') or 'unknown'}"))
    detail_paths = [item["path"] for item in detail_results]
    if len(detail_paths) != len(set(detail_paths)):
        errors.append("detail images contain duplicate files")
    if asset_contract and manual_confirmation_required:
        accepted_root = product_dir / "output/accepted-images"
        accepted_files = {
            str(path.resolve()) for path in accepted_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".avif"}
        }
        planned_accepted = {
            str(Path(item["path"]).resolve())
            for item in [*main_results, *detail_results]
            if item.get("path") and item.get("path") != "unknown"
        }
        expected_count = len(selected_skus) + 8
        if len(accepted_files) != expected_count or accepted_files != planned_accepted:
            errors.append(
                f"manual mode requires exactly the reviewed N+8 accepted image set; "
                f"expected {expected_count}, found {len(accepted_files)}"
            )
        errors.extend(validate_accepted_manifest(
            product_dir,
            [*planned_main, *planned_detail],
            expected_count=expected_count,
        ))

    return {
        "passed": not errors and len(main_results) == len(selected_skus),
        "selected_sku_count": len(selected_skus),
        "main_images": main_results,
        "detail_images": detail_results,
        "errors": list(dict.fromkeys(errors)),
        "checked_at": now(),
    }


def current_upload_image_gate(product_dir: Path) -> Dict[str, Any]:
    """Build the current upload image gate from image-plan and real files only."""
    result = current_image_completeness(product_dir)
    detail = "每个已选SKU必须有合格主图，且商品必须正好有8张详情图。"
    if result["errors"]:
        detail += " " + "；".join(result["errors"])
    checks = [
        {"name": "image_slot_completeness", "passed": result["passed"], "detail": detail},
        {"name": "images_qc", "passed": result["passed"], "detail": "当前 image-plan 中的全部图片文件必须存在、可读取并完成生成。"},
    ]
    errors = list(dict.fromkeys(str(item) for item in result["errors"]))
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "status": "PASS" if not errors and all(item.get("passed") for item in checks) else "FAIL",
        "passed": not errors and all(item.get("passed") for item in checks),
        "checks": checks,
        "errors": errors,
        "selected_sku_count": result["selected_sku_count"],
        "main_images": result["main_images"],
        "detail_images": result["detail_images"],
        "checked_at": result["checked_at"],
    }


def _sync_draft_image_qc_from_current_gate(
    product_dir: Path,
    draft: Dict[str, Any],
    image_gate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Refresh stale draft image statuses from the current hard image gate.

    ``ozon-draft.json`` can be written before image QC finishes.  Upload
    validation must use the current image-plan plus real files, not a stale
    draft ``qc_status``.  This only upgrades entries whose own current file is
    still readable inside the product directory.
    """
    gate = image_gate or current_upload_image_gate(product_dir)
    passed_slots = {
        str(item.get("slot"))
        for item in [*(gate.get("main_images") or []), *(gate.get("detail_images") or [])]
        if item.get("passed") is True and item.get("slot")
    }
    for item in draft.get("images") or []:
        slot = str(item.get("slot") or "")
        if slot not in passed_slots:
            continue
        try:
            current_path = _resolve_product_artifact(product_dir, item.get("path"))
        except UploadGateError:
            continue
        if _image_file_is_readable(current_path):
            item["qc_status"] = "pass"
    return gate


def _product_context(path: Path) -> Optional[tuple[Path, str]]:
    resolved = path.resolve()
    parts = resolved.parts
    for index, part in enumerate(parts[:-1]):
        if part == "products" and index + 1 < len(parts) and parts[index + 1].startswith("P"):
            project_root = Path(*parts[:index]) if index else Path(resolved.anchor)
            return project_root, parts[index + 1]
    return None


def _product_deletion_requested(product_dir: Path) -> bool:
    project_root = product_dir.resolve().parent.parent
    marker = project_root / "logs/product-deletion-tombstones" / f"{product_dir.name}.deleted"
    return marker.is_file() or not product_dir.is_dir()


def write_json_atomic(path: Path, value: Any) -> None:
    context = _product_context(path)
    if context is not None:
        project_root, product_id = context
        marker = project_root / "logs/product-deletion-tombstones" / f"{product_id}.deleted"
        if marker.is_file():
            raise UploadGateError(f"Local product {product_id} was permanently deleted; stale result discarded")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _sync_remote_pending_queue(product_dir: Path, result: Dict[str, Any], status: Dict[str, Any]) -> None:
    """Maintain the local queue of Ozon tasks that may only be queried, never re-submitted."""
    queue_path = product_dir.parent.parent / "remote-pending-queue.json"
    queue = load_json(queue_path) if queue_path.is_file() else {"items": []}
    items = [item for item in queue.get("items") or [] if item.get("product_id") != product_dir.name]
    if status.get("status") == "PENDING_REMOTE" and not _product_deletion_requested(product_dir):
        idempotency_path = product_dir / "output" / "ozon-idempotency.json"
        idempotency = load_json(idempotency_path) if idempotency_path.is_file() else {}
        items.append({
            "product_id": product_dir.name,
            "task_id": str(result.get("task_id") or status.get("ozon", {}).get("task_id") or "unknown"),
            "offer_ids": [item.get("offer_id") for item in result.get("items") or [] if item.get("offer_id")],
            "submitted_at": idempotency.get("request_timestamp") or now(),
            "last_checked_at": (result.get("recovery") or {}).get("last_checked_at") or "unknown",
            "check_count": int((result.get("recovery") or {}).get("query_count") or 0),
            "status": "PENDING_REMOTE",
            "api_write_completed": True,
            "payload_hash": idempotency.get("payload_hash") or "unknown",
        })
    write_json_atomic(queue_path, {"items": items})


def _enqueue_image_channel_check(
    product_dir: Path, result: Dict[str, Any], expected_image_count: int
) -> None:
    queue_path = product_dir.parent.parent / "image-channel-queue.json"
    queue = load_json(queue_path) if queue_path.is_file() else {"items": []}
    items = [item for item in queue.get("items") or [] if item.get("product_id") != product_dir.name]
    if _product_deletion_requested(product_dir):
        write_json_atomic(queue_path, {"items": items})
        return
    idempotency = load_json(product_dir / "output/ozon-idempotency.json")
    state = channel_state(product_dir)
    items.append({
        "product_id": product_dir.name,
        "task_id": str(result["task_id"]),
        "offer_ids": idempotency["offer_ids"],
        "payload_hash": idempotency["payload_hash"],
        "expected_image_count": expected_image_count,
        "channel_worker_pid": state.get("worker_pid"),
        "channel_url": state.get("public_url", "unknown"),
        "channel_status": state.get("status", "unknown"),
        "submitted_at": idempotency["request_timestamp"],
        "last_checked_at": "unknown",
        "check_count": 0,
        "status": "WAITING_OZON_CDN",
    })
    write_json_atomic(queue_path, {"items": items})


def _ensure_image_status_monitor(project_root: Path) -> None:
    pid_path = project_root / "logs/image-status-monitor.pid"
    if pid_path.is_file():
        try:
            os.kill(int(pid_path.read_text(encoding="utf-8").strip()), 0)
            return
        except (OSError, TypeError, ValueError):
            pid_path.unlink(missing_ok=True)
    log_path = project_root / "logs/image-status-monitor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(project_root / "scripts/image_status_monitor.py")],
            cwd=project_root, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )
    pid_path.write_text(str(process.pid), encoding="utf-8")


def sync_image_channel_status(
    product_dir: Path,
    client: OzonWriteClient,
    product_response: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one CDN confirmation check and close only a confirmed channel."""
    project_root = product_dir.parent.parent
    queue_path = project_root / "image-channel-queue.json"
    queue = load_json(queue_path) if queue_path.is_file() else {"items": []}
    if _product_deletion_requested(product_dir):
        queue["items"] = [item for item in queue.get("items") or [] if item.get("product_id") != product_dir.name]
        write_json_atomic(queue_path, queue)
        return {"status": "discarded_deleted_product"}
    entry = next((item for item in queue.get("items") or [] if item.get("product_id") == product_dir.name), None)
    if entry is None:
        return {"status": "not_queued"}
    response = product_response or client.get_products_info(entry["offer_ids"])
    entry["check_count"] = int(entry.get("check_count") or 0) + 1
    entry["last_checked_at"] = now()
    output = product_dir / "output"
    manifest = load_json(output / "ozon-images.json")
    terminal_errors = _remote_terminal_errors(response, entry["offer_ids"])
    if terminal_errors:
        stop_image_channel(product_dir, reason="ozon_product_declined")
        entry["status"] = "REMOTE_DECLINED"
        entry["channel_status"] = "closing"
        for image in manifest["images"]:
            image["status"] = "failed"
            image["error"] = "Ozon rejected the product before media transfer completed."
        write_json_atomic(output / "ozon-image-transfer.json", {
            "status": "REMOTE_DECLINED", "checked_at": now(),
            "offer_ids": entry["offer_ids"], "temporary_channel_closed": True,
            "errors": terminal_errors, "response": response,
        })
        queue["items"] = [item for item in queue["items"] if item.get("product_id") != product_dir.name]
    elif _images_ingested(response, entry["offer_ids"], int(entry["expected_image_count"])):
        stop_image_channel(product_dir)
        entry["status"] = "MEDIA_CONFIRMED"
        entry["channel_status"] = "closing"
        for image in manifest["images"]:
            image["status"] = "imported"
        manifest["hosting_mode"] = "completed"
        result_path = output / "ozon-result.json"
        if result_path.is_file():
            stored_result = load_json(result_path)
            previous_raw = stored_result.get("raw_response")
            stored_result["raw_response"] = {
                "import": previous_raw.get("import", previous_raw)
                if isinstance(previous_raw, dict) else previous_raw,
                "image_verification": response,
            }
            write_json_atomic(result_path, stored_result)
        write_json_atomic(output / "ozon-image-transfer.json", {
            "status": "MEDIA_CONFIRMED", "checked_at": now(),
            "offer_ids": entry["offer_ids"], "temporary_channel_closed": True,
            "response": response,
        })
        queue["items"] = [item for item in queue["items"] if item.get("product_id") != product_dir.name]
    else:
        state = channel_state(product_dir)
        entry["channel_status"] = state.get("status", "unknown")
        if state.get("status") in {"expired", "failed"}:
            entry["status"] = "CHANNEL_ANOMALY"
            write_json_atomic(output / "ozon-image-transfer.json", {
                "status": "channel_anomaly", "checked_at": now(),
                "offer_ids": entry["offer_ids"], "temporary_channel_closed": True,
                "channel": state, "response": response,
            })
            queue["items"] = [item for item in queue["items"] if item.get("product_id") != product_dir.name]
        else:
            entry["status"] = "WAITING_OZON_CDN"
            write_json_atomic(output / "ozon-image-transfer.json", {
                "status": "waiting_ozon_cdn", "checked_at": now(),
                "offer_ids": entry["offer_ids"], "temporary_channel_closed": False,
                "channel": state, "response": response,
            })
    write_json_atomic(output / "ozon-images.json", manifest)
    write_json_atomic(queue_path, queue)
    return entry


def validate(value: Any, schema_path: Path) -> List[str]:
    schema = load_json(schema_path)
    errors = []
    for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def _attribute_metadata(metadata: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    return {item["attribute_id"]: item for item in metadata["attributes"]}


def _allowed_dictionary_value(attribute: Dict[str, Any], value_id: int, value: str) -> bool:
    return any(
        int(item.get("dictionary_value_id", item.get("id")) or 0) == int(value_id)
        and str(item.get("value") or "").casefold() == str(value or "").casefold()
        for item in attribute["allowed_values"]
    )


def _is_hashtag_attribute(attribute_id: int, metadata: Dict[int, Dict[str, Any]]) -> bool:
    if attribute_id == 23171:
        return True
    name = str((metadata.get(attribute_id) or {}).get("attribute_name") or "").casefold()
    return "хештег" in name or "hashtag" in name


def _is_cable_length_attribute(attribute_id: int, metadata: Dict[int, Dict[str, Any]]) -> bool:
    if attribute_id == 5391:
        return True
    name = str((metadata.get(attribute_id) or {}).get("attribute_name") or "").casefold()
    return any(token in name for token in ("длина шнура", "длина кабеля", "длина провода", "cord length", "cable length"))


def _contains_explicit_cable_evidence(compiled: Dict[str, Any]) -> bool:
    evidence = " ".join(str(value or "") for value in (
        compiled.get("canonical_value"), compiled.get("source"), *(compiled.get("evidence") or []),
    )).casefold()
    return any(token in evidence for token in ("шнур", "кабель", "провод", "cord", "cable", "电源线", "线长", "充电线"))


def _payload_tag_terms(
    config: Dict[str, Any],
    draft: Dict[str, Any],
    sku: Dict[str, Any],
    source: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Terms that may identify a brand/store/model rather than a search tag."""
    values: List[Any] = [
        (config.get("brand") or {}).get("value"),
        (config.get("model_name") or {}).get("value"),
        config.get("merge_product_name"),
        sku.get("offer_id"),
        sku.get("source_sku_name"),
        sku.get("display_name_ru"),
    ]
    # Old drafts may contain a supplier/store name even when the current
    # config says "Нет бренда".  Only this product's collected brand fields
    # are used to filter final search tags; no other product is consulted.
    for key in ("brand", "brand_name", "manufacturer", "seller_brand", "store_name"):
        values.append((source or {}).get(key))
    for item in (source or {}).get("product_attributes") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name_cn") or item.get("name") or "").casefold()
        if "品牌" in name or "商标" in name or "brand" in name or "manufacturer" in name:
            values.append(item.get("value_cn") or item.get("value"))
    result: List[str] = []
    for value in values:
        term = str(value or "").strip()
        if not term or term.casefold() in {"нет бренда", "unknown", "none", "null"}:
            continue
        result.append(term)
    return result


def _flatten_tag_values(values: List[Dict[str, Any]]) -> List[str]:
    candidates: List[str] = []
    for value in values:
        raw = str(value.get("value") or "")
        candidates.extend(part for part in re.split(r"[\s,;]+", raw) if part)
    return candidates


def _canonical_tags_from_file(product_dir: Optional[Path]) -> List[str]:
    if product_dir is None:
        return []
    path = product_dir / "output/ozon-tags.json"
    if not path.is_file():
        return []
    try:
        raw_tags = load_json(path).get("tags") or []
    except Exception:
        return []
    result: List[str] = []
    seen: set[str] = set()
    if not isinstance(raw_tags, list):
        return result
    for value in raw_tags:
        tag = canonical_hashtag(value)
        if not tag or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        result.append(tag)
        if len(result) == 30:
            break
    return result


def _hashtag_attribute_id(metadata: Dict[int, Dict[str, Any]]) -> int | None:
    if 23171 in metadata:
        return 23171
    for attribute_id, attribute in metadata.items():
        if _is_hashtag_attribute(attribute_id, metadata):
            return attribute_id
    return None


def _live_numeric_bounds(attribute: Dict[str, Any]) -> Tuple[float | None, float | None]:
    """Read a live Ozon numeric range without mistaking list limits for values."""
    containers = [attribute]
    for key in ("constraints", "field_contract", "restrictions", "value_limits", "validation"):
        nested = attribute.get(key)
        if isinstance(nested, dict):
            containers.append(nested)
    minimum = maximum = None
    for container in containers:
        if minimum is None:
            for key in ("min_value", "minimum", "min", "minValue", "lower_bound"):
                value = container.get(key)
                if value not in {None, ""}:
                    try:
                        minimum = float(value)
                    except (TypeError, ValueError):
                        pass
                    break
        if maximum is None:
            for key in ("max_value", "maximum", "max", "maxValue", "upper_bound"):
                value = container.get(key)
                if value not in {None, ""}:
                    try:
                        maximum = float(value)
                    except (TypeError, ValueError):
                        pass
                    break
    return minimum, maximum


def _normalized_live_numeric_values(attribute: Dict[str, Any], values: List[Dict[str, Any]]) -> List[Dict[str, Any]] | None:
    """Return API-safe numeric values or ``None`` when a stale value is illegal.

    Unit conversion belongs to the deterministic compiler.  This last layer
    deliberately only normalizes a decimal separator and validates the live
    type/range, so it cannot silently reinterpret cm as mm or a weight as a
    capacity.
    """
    kind = str(attribute.get("type") or "").casefold()
    if kind not in {"integer", "int32", "int64", "decimal", "double", "float"}:
        return values
    minimum, maximum = _live_numeric_bounds(attribute)
    normalized: List[Dict[str, Any]] = []
    for item in values:
        raw = str(item.get("value") or "").strip().replace(",", ".")
        if not _SCALAR_NUMBER_RE.fullmatch(raw):
            return None
        try:
            number = float(raw)
        except ValueError:
            return None
        if kind in {"integer", "int32", "int64"} and not number.is_integer():
            return None
        if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
            return None
        value = str(int(number)) if kind in {"integer", "int32", "int64"} else format(number, ".12g")
        normalized.append({**item, "value": value})
    return normalized


def _repair_final_api_attributes(
    attributes: List[Dict[str, Any]],
    *,
    metadata: Dict[int, Dict[str, Any]],
    compiled_by_id: Dict[int, Dict[str, Any]],
    blocked_tag_terms: List[str],
    canonical_tags: Optional[List[str]] = None,
    repair_log: List[Dict[str, Any]],
    sku_id: str,
) -> List[Dict[str, Any]]:
    """Final no-write cleanup for old/partially stale compiled artifacts.

    The compiler owns semantic decisions.  This function only enforces the
    current Ozon metadata at payload construction: optional invalid fields are
    removed, while a required invalid field remains a deterministic local
    failure rather than a fabricated value.
    """
    result: List[Dict[str, Any]] = []
    for attribute in attributes:
        attribute_id = int(attribute.get("id") or 0)
        live = metadata.get(attribute_id)
        compiled = compiled_by_id.get(attribute_id) or {}
        if _is_cable_length_attribute(attribute_id, metadata) and not _contains_explicit_cable_evidence(compiled):
            repair_log.append({"sku_id": sku_id, "attribute_id": attribute_id, "action": "removed", "reason": "no_explicit_cable_length_source"})
            continue
        if _is_hashtag_attribute(attribute_id, metadata):
            tags: List[str] = []
            seen: set[str] = set()
            candidates = canonical_tags if canonical_tags else _flatten_tag_values(attribute.get("values") or [])
            for candidate in candidates:
                tag = canonical_hashtag(candidate, blocked_terms=blocked_tag_terms)
                if not tag or tag.casefold() in seen:
                    continue
                seen.add(tag.casefold())
                tags.append(tag)
                if len(tags) == 30:
                    break
            if not tags:
                repair_log.append({"sku_id": sku_id, "attribute_id": attribute_id, "action": "removed", "reason": "no_valid_russian_search_tags"})
                continue
            if tags != _flatten_tag_values(attribute.get("values") or []):
                repair_log.append({"sku_id": sku_id, "attribute_id": attribute_id, "action": "normalized", "reason": "canonical_ozon_tags_file", "value_count": len(tags)})
            values = (
                [{"value": " ".join(tags)}]
                if live and live.get("is_collection") is False
                else [{"value": tag} for tag in tags]
            )
            result.append({"complex_id": attribute.get("complex_id", 0), "id": attribute_id, "values": values})
            continue
        if live and live.get("allowed_values"):
            values = attribute.get("values") or []
            valid = bool(values) and all(
                value.get("dictionary_value_id") is not None
                and _allowed_dictionary_value(live, value["dictionary_value_id"], str(value.get("value") or ""))
                for value in values
            )
            if not valid:
                entry = {"sku_id": sku_id, "attribute_id": attribute_id, "action": "blocked_required" if live.get("required") else "removed", "reason": "value_absent_from_current_allowed_values"}
                repair_log.append(entry)
                if live.get("required"):
                    raise UploadGateError(f"必填属性 {attribute_id} 不在当前 Ozon 合法字典中")
                continue
        if live:
            normalized_values = _normalized_live_numeric_values(live, attribute.get("values") or [])
            if normalized_values is None:
                entry = {
                    "sku_id": sku_id,
                    "attribute_id": attribute_id,
                    "action": "blocked_required" if live.get("required") else "removed",
                    "reason": "numeric_value_or_range_not_allowed_by_current_ozon_metadata",
                }
                repair_log.append(entry)
                if live.get("required"):
                    raise UploadGateError(f"必填属性 {attribute_id} 的数值或范围不符合当前 Ozon 要求")
                continue
            if normalized_values != attribute.get("values"):
                repair_log.append({
                    "sku_id": sku_id,
                    "attribute_id": attribute_id,
                    "action": "normalized",
                    "reason": "current_ozon_numeric_contract",
                })
                attribute = {**attribute, "values": normalized_values}
        result.append(attribute)
    return result


def apply_upload_config(draft: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(draft)
    prices = {item["source_sku_id"]: item["price"] for item in config["sku_prices"]}
    colors = {item["source_sku_id"]: item for item in config["sku_colors"]}
    color_attribute_ids = {int(item["attribute_id"]) for item in config["sku_colors"]}
    result["price"]["currency_code"] = config["currency_code"]
    result["price"]["old_price"] = config["old_price"]
    for sku in result["skus"]:
        sku_id = str(sku["source_sku_id"])
        sku["sale_price"] = prices.get(sku_id)
        sku["sale_currency_code"] = config["currency_code"]
        color = colors.get(sku_id)
        sku["attributes"] = [
            item for item in sku["attributes"]
            if item.get("attribute_id") not in color_attribute_ids
        ]
        if color:
            sku["attributes"].append({
                "field_key": "color",
                "attribute_id": color["attribute_id"],
                "complex_id": "unknown",
                "values": [{
                    "dictionary_value_id": color["dictionary_value_id"],
                    "value": color["value"],
                }],
                "source": "source",
                "status": "confirmed",
            })
    configured = {
        config["brand"]["attribute_id"]: ("brand", config["brand"], "human"),
        config["type"]["attribute_id"]: ("product_type", config["type"], "source"),
    }
    model_attribute_id = int((config.get("model_name") or {}).get("attribute_id") or 0)
    if model_attribute_id > 0:
        configured[model_attribute_id] = ("model_name", config["model_name"], "human")
    new_attributes = []
    existing_by_id = {
        item["attribute_id"]: item
        for item in result["attributes"]
        if isinstance(item["attribute_id"], int)
    }
    for attribute_id, (field_key, item, source_type) in configured.items():
        value = {"value": item["value"]}
        if "dictionary_value_id" in item:
            value["dictionary_value_id"] = item["dictionary_value_id"]
        current = existing_by_id.get(attribute_id, {})
        new_attributes.append({
            "field_key": field_key,
            "attribute_id": attribute_id,
            "complex_id": current.get("complex_id", "unknown") or "unknown",
            "values": [value],
            "source": source_type,
            "status": "confirmed",
        })
    configured_ids = set(configured)
    result["attributes"] = [
        item for item in result["attributes"] if item.get("attribute_id") not in configured_ids
    ] + new_attributes
    return result


def _compiled_model_name_config(
    final_attributes: Optional[Dict[str, Any]],
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    """Prefer the compiler's product-level model name over a stale config."""
    configured_id = int((fallback or {}).get("attribute_id") or 0)
    if not final_attributes or configured_id <= 0:
        return fallback or {}
    for attribute in final_attributes.get("common_attributes") or final_attributes.get("attributes") or []:
        if int(attribute.get("attribute_id") or attribute.get("id") or 0) != configured_id:
            continue
        value = str(
            attribute.get("target_value")
            or attribute.get("value")
            or attribute.get("canonical_value")
            or ""
        ).strip()
        if value and value.casefold() not in {"unknown", "none", "null"}:
            return {
                **fallback,
                "value": value,
                "source": "ozon_attributes_final_compiled_model_name",
            }
    return fallback or {}


def build_preflight(
    product_dir: Path,
    draft: Dict[str, Any],
    status: Dict[str, Any],
    config: Dict[str, Any],
    metadata: Dict[str, Any],
    image_manifest: Dict[str, Any],
    checked_at: str,
    image_gate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    checks = []
    errors = []
    warnings = [
        "No stock endpoint will be called; the created product remains unavailable for sale.",
        "Product creation submits the card to Ozon moderation; it does not activate inventory.",
    ]

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            errors.append(detail)

    add(
        "batch_task_authorized",
        status.get("task_authorized") is True or status.get("status") in {"UPLOADED", "OZON_MODERATION", "ACTIVE"},
        "The product must belong to a user-started batch task.",
    )
    add(
        "live_category",
        draft["category"]["metadata_source"] == "ozon_seller_api"
        and isinstance(draft["description_category_id"], int)
        and isinstance(draft["type_id"], int)
        and metadata.get("category_id") == draft["description_category_id"]
        and metadata.get("type_id") == draft["type_id"],
        "A live Ozon category_id and type_id are required.",
    )
    attrs = _attribute_metadata(metadata)
    final_attributes_path = product_dir / "output/ozon-attributes-final.json"
    final_attributes_for_gate = (
        load_json(final_attributes_path) if final_attributes_path.is_file() else {}
    )
    sku_ids = [str(item["source_sku_id"]) for item in draft["skus"]]
    common_by_id = {
        int(item["attribute_id"]): item
        for item in (
            final_attributes_for_gate.get("common_attributes")
            or final_attributes_for_gate.get("attributes")
            or []
        )
        if item.get("attribute_id") is not None
    }
    by_sku = {
        str(sku_id): {
            int(item["attribute_id"]): item
            for item in (values or [])
            if item.get("attribute_id") is not None
        }
        for sku_id, values in (final_attributes_for_gate.get("attributes_by_sku") or {}).items()
    }

    def has_attribute_value(item: Optional[Dict[str, Any]]) -> bool:
        if not item:
            return False
        raw = item.get("target_value", item.get("value", item.get("ozon_value")))
        return raw not in {None, "", "unknown", "unresolved"}

    missing_required: List[str] = []
    for item in metadata.get("attributes", []):
        if item.get("required") is not True:
            continue
        attr_id = int(item["attribute_id"])
        attr_name = item.get("attribute_name") or str(attr_id)
        sku_scoped_required = (
            item.get("is_aspect")
            or item.get("is_collection")
            or any(attr_id in values for values in by_sku.values())
        )
        if sku_scoped_required:
            for sku_id in sku_ids:
                if not has_attribute_value(by_sku.get(sku_id, {}).get(attr_id) or common_by_id.get(attr_id)):
                    missing_required.append(f"{attr_name} ({sku_id})")
        elif not has_attribute_value(common_by_id.get(attr_id)):
            missing_required.append(attr_name)
    add(
        "required_attributes",
        not missing_required,
        "Missing required Ozon attributes: " + (", ".join(missing_required) or "none"),
    )
    invalid_values = []
    for item in (config["brand"], config["type"]):
        attribute = attrs.get(item["attribute_id"])
        if not attribute or not _allowed_dictionary_value(
            attribute, item["dictionary_value_id"], item["value"]
        ):
            invalid_values.append(item["value"])
    for item in config["sku_colors"]:
        attribute = attrs.get(item["attribute_id"])
        if not attribute or not _allowed_dictionary_value(
            attribute, item["dictionary_value_id"], item["value"]
        ):
            invalid_values.append(item["value"])
    add(
        "dictionary_values",
        not invalid_values,
        "Invalid Ozon dictionary values: " + (", ".join(invalid_values) or "none"),
    )
    selected_skus = {str(item["source_sku_id"]) for item in draft["skus"]}
    priced_skus = {item["source_sku_id"] for item in config["sku_prices"] if float(item["price"]) > 0}
    add(
        "sku_prices",
        selected_skus == priced_skus,
        "Every selected SKU requires one positive sale price in the configured currency.",
    )
    add(
        "currency",
        config["currency_code"] == "CNY",
        "The zhonglian1 store was verified to use CNY pricing.",
    )
    add(
        "vat",
        config["vat"] != "unknown",
        "VAT must be confirmed before product creation.",
    )
    final_measurements = (final_attributes_for_gate.get("sku_measurements") or {})

    def sku_measurement_pair(sku_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        measurement = final_measurements.get(str(sku_id)) or {}
        product_dimensions = _canonical_known_dimensions(
            measurement.get("product_dimensions"),
            config.get("product_dimensions") or {},
        )
        package_dimensions = _canonical_known_dimensions(
            measurement.get("package_dimensions"),
            config["package_dimensions"],
        )
        product_weight = _canonical_known_scalar(measurement.get("product_weight"))
        package_weight = _canonical_known_scalar(measurement.get("package_weight"))
        if product_weight is None:
            product_weight = (config.get("product_weight") or {}).get("value_g")
        if package_weight is None:
            package_weight = config["package_weight"].get("value_g")
        return product_dimensions, package_dimensions, {"value_g": product_weight}, {"value_g": package_weight}

    package_measurements_ok = True
    hierarchy_ok = True
    for sku_id in sku_ids:
        product_dimensions, dimensions, product_weight, weight = sku_measurement_pair(sku_id)
        package_measurements_ok = package_measurements_ok and all(
            float(dimensions.get(key) or 0) > 0 for key in ("length_mm", "width_mm", "height_mm")
        ) and float(weight.get("value_g") or 0) > 0
        hierarchy_ok = hierarchy_ok and (
            float(weight.get("value_g") or 0) > float(product_weight.get("value_g") or 0) > 0
            and all(
                float(dimensions.get(key) or 0) > float(product_dimensions.get(key) or 0) > 0
                for key in ("length_mm", "width_mm", "height_mm")
            )
        )
    add(
        "package_measurements",
        package_measurements_ok,
        "Every SKU requires positive package dimensions and package weight.",
    )
    add(
        "measurement_hierarchy",
        hierarchy_ok,
        "Every SKU package weight and every package dimension must be strictly greater than that SKU's product measurements.",
    )
    image_gate = _sync_draft_image_qc_from_current_gate(product_dir, draft, image_gate)
    generated_file_errors = image_gate.get("errors") or []
    generated_files_ok = (
        image_gate.get("status") == "PASS" and image_gate.get("passed") is True
    )
    add(
        "generated_images",
        generated_files_ok,
        "Every generated image must exist locally and pass the current hard image check."
        + (": " + "; ".join(str(item) for item in generated_file_errors) if generated_file_errors else ""),
    )
    public_images_ok = all(
        isinstance(item["public_url"], str) and item["public_url"].startswith("https://")
        for item in image_manifest["images"]
    )
    add(
        "public_images",
        public_images_ok,
        "Generated images require temporary public HTTPS URLs for Ozon import.",
    )
    add(
        "stock_disabled",
        config["stock_mode"] == "not_set"
        and draft["stock"]["quantity"] is None
        and all(item["stock"] is None for item in draft["skus"]),
        "Stock must remain unset for the first product-creation test.",
    )
    output = product_dir / "output"
    field_files = {
        "tags": (output / "ozon-tags.json", SCHEMAS["tags"]),
        "attributes": (output / "ozon-attributes-final.json", SCHEMAS["attributes_final"]),
        "rich": (output / "rich-content.json", SCHEMAS["rich_content"]),
        "colors": (output / "color-variants.json", SCHEMAS["color_variants"]),
        "color_policy": (output / "color-variant-policy.json", SCHEMAS["color_variant_policy"]),
    }
    field_values: Dict[str, Dict[str, Any]] = {}
    for key, (path, schema) in field_files.items():
        if path.is_file():
            value = load_json(path)
            schema_errors = validate(value, schema)
            if not schema_errors:
                field_values[key] = value
        else:
            schema_errors = [f"Missing {path.name}"]
        add(
            f"field_completion_{key}_schema",
            not schema_errors,
            f"{path.name} must exist and pass schema validation"
            + (": " + "; ".join(schema_errors) if schema_errors else "."),
        )
    tags = field_values.get("tags", {})
    normalized_tags = tags.get("tags", []) if isinstance(tags.get("tags"), list) else []
    add(
        "field_completion_tags_count",
        tags.get("count") == len(normalized_tags)
        and len({str(tag).casefold() for tag in normalized_tags}) == len(normalized_tags)
        and len(normalized_tags) <= 30,
        "Field completion allows up to 30 unique Russian Ozon hashtags; an empty optional field is omitted.",
    )
    final_attributes = field_values.get("attributes", {})
    add(
        "field_completion_required_attributes",
        final_attributes.get("required_summary", {}).get("missing") == 0,
        "Field completion requires every mandatory live Ozon category attribute.",
    )
    rich = field_values.get("rich", {})
    add(
        "field_completion_rich_content",
        rich.get("status") in {"ready", "ready_for_upload"},
        "Ozon Rich Content must be valid and use persistent HTTPS images or resolvable local assets.",
    )
    color_policy = field_values.get("color_policy", {})
    add(
        "field_completion_color_variants",
        color_policy.get("status") in {"PASS", "WARNING"},
        "The main SKU needs a safe color image; missing non-core variants remain warnings.",
    )
    upload_allowed = all(item["passed"] for item in checks)
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "shop_name": config["shop_name"],
        "checked_at": checked_at,
        "upload_allowed": upload_allowed,
        "checks": checks,
        "missing_required_attributes": missing_required,
        "invalid_values": invalid_values,
        "errors": errors,
        "warnings": warnings,
    }


def _dictionary_attribute(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "complex_id": 0,
        "id": item["attribute_id"],
        "values": [{
            "dictionary_value_id": item["dictionary_value_id"],
            "value": item["value"],
        }],
    }


def _text_attribute(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "complex_id": 0,
        "id": item["attribute_id"],
        "values": [{"value": item["value"]}],
    }


def _final_attribute_to_api(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    dictionary_values = item.get("dictionary_values") or []
    if dictionary_values:
        values = [
            {
                "dictionary_value_id": value["dictionary_value_id"],
                "value": str(value["value"]),
            }
            for value in dictionary_values
            if value.get("dictionary_value_id") is not None and value.get("value") not in {None, "", "unknown"}
        ]
        if values:
            return {"complex_id": 0, "id": item["attribute_id"], "values": values}
    raw_value = item.get("target_value", item.get("value"))
    if raw_value in {None, "unknown", ""}:
        return None
    value: Dict[str, Any] = {"value": str(raw_value)}
    if item.get("dictionary_value_id") is not None:
        value["dictionary_value_id"] = item["dictionary_value_id"]
    return {"complex_id": 0, "id": item["attribute_id"], "values": [value]}


def _compiled_attributes_for_sku(final_attributes: Dict[str, Any], sku_id: str) -> List[Dict[str, Any]]:
    common = list(final_attributes.get("common_attributes") or final_attributes.get("attributes") or [])
    sku_specific = list((final_attributes.get("attributes_by_sku") or {}).get(str(sku_id)) or [])
    by_id: Dict[int, Dict[str, Any]] = {}
    for item in common:
        if item.get("attribute_id") is not None:
            by_id[int(item["attribute_id"])] = item
    for item in sku_specific:
        if item.get("attribute_id") is not None:
            by_id[int(item["attribute_id"])] = item
    return list(by_id.values())


def _compiled_api_attributes_for_sku(
    final_attributes: Dict[str, Any],
    sku_id: str,
    existing_ids: set[int],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set(existing_ids)
    for item in _compiled_attributes_for_sku(final_attributes, sku_id):
        converted = _final_attribute_to_api(item)
        if converted and int(converted["id"]) not in seen:
            result.append(converted)
            seen.add(int(converted["id"]))
    return result


def _canonical_field_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("canonical_value", value.get("target_value", value.get("value")))
    return value


_UNKNOWN_SCALARS = {"", "unknown", "none", "null", "nan"}


def _is_unknown_scalar(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _UNKNOWN_SCALARS
    return False


def _canonical_known_scalar(value: Any) -> Any:
    value = _canonical_field_value(value)
    if _is_unknown_scalar(value):
        return None
    return value


def _dimensions_are_positive(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        return all(
            not _is_unknown_scalar(value.get(key))
            and math.isfinite(float(value.get(key)))
            and float(value.get(key)) > 0
            for key in ("length_mm", "width_mm", "height_mm")
        )
    except (TypeError, ValueError):
        return False


def _canonical_known_dimensions(value: Any, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    value = _canonical_field_value(value)
    if isinstance(value, dict):
        dimensions = {
            "length_mm": value.get("length_mm"),
            "width_mm": value.get("width_mm"),
            "height_mm": value.get("height_mm"),
        }
        if _dimensions_are_positive(dimensions):
            return dimensions
    return fallback or {}


def _sku_package_measurement(final_attributes: Optional[Dict[str, Any]], sku_id: str, config: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not final_attributes:
        return config["package_dimensions"], config["package_weight"]
    measurement = (final_attributes.get("sku_measurements") or {}).get(str(sku_id)) or {}
    package_dimensions = _canonical_known_dimensions(
        measurement.get("package_dimensions"),
        config["package_dimensions"],
    )
    package_weight = _canonical_known_scalar(measurement.get("package_weight"))
    if package_weight is None:
        package_weight = config["package_weight"].get("value_g")
    return package_dimensions, {"value_g": package_weight}


def _resolve_rich_content_for_upload(
    output: Path,
    final_attributes: Dict[str, Any],
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    rich_path = output / "rich-content.json"
    if not rich_path.is_file():
        return final_attributes
    rich = load_json(rich_path)
    if rich.get("status") != "ready_for_upload":
        return final_attributes
    slot_urls = {
        item["slot"]: item["public_url"]
        for item in manifest["images"]
        if item["role"] != "color"
        and str(item["public_url"]).startswith("https://")
    }
    resolved = copy.deepcopy(rich["content"])
    for widget in resolved["content"]:
        for block in widget["blocks"]:
            for key in ("src", "srcMobile"):
                value = block["img"][key]
                if value.startswith("asset://"):
                    slot = value.removeprefix("asset://")
                    if slot not in slot_urls:
                        raise UploadGateError(f"Rich Content image slot was not staged: {slot}")
                    block["img"][key] = slot_urls[slot]
    serialized = json.dumps(resolved, ensure_ascii=False, separators=(",", ":"))
    updated = copy.deepcopy(final_attributes)
    rich_attribute_id = rich.get("attribute_id")
    if updated.get("common_attributes"):
        target_key = "common_attributes"
    else:
        target_key = "attributes"
        updated.setdefault("attributes", [])
    target_section = updated[target_key]
    found = False
    for item in target_section:
        if rich_attribute_id is not None and item["attribute_id"] == rich_attribute_id:
            item["value"] = serialized
            item["target_value"] = serialized
            item["evidence"] = ["rich-content.json resolved through production image tunnel"]
            found = True
            break
    if rich_attribute_id is not None and not found:
        target_section.append({
            "attribute_id": int(rich_attribute_id),
            "attribute_name": "Rich-контент JSON",
            "scope": "common",
            "required": False,
            "value": serialized,
            "canonical_value": serialized,
            "canonical_unit": "text",
            "target_value": serialized,
            "target_unit": "text",
            "conversion_rule": "rich_content_image_url_resolution",
            "source": "rich-content.json",
            "mapping_method": "resolved_through_production_image_tunnel",
            "confidence": 0.85,
            "dictionary_value_id": None,
            "evidence": ["rich-content.json resolved through production image tunnel"],
        })
    updated["attributes"] = updated.get("common_attributes") or updated.get("attributes") or []
    return updated


def _existing_product_created(output: Path) -> bool:
    result_path = output / "ozon-result.json"
    if not result_path.is_file():
        return False
    result = load_json(result_path)
    return result.get("status") == "created" and bool(result.get("items"))


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _current_upload_hashes(
    product_dir: Path,
    draft: Dict[str, Any],
    final_attributes: Dict[str, Any],
    colors: Dict[str, Any],
    config: Dict[str, Any],
    variant_grouping: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    root = product_dir.parents[1]
    image_entries = []
    for item in draft["images"]:
        path = root / item["path"]
        image_entries.append({
            "slot": item["slot"],
            "role": item["role"],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    for item in colors["variants"]:
        path_text = item["image"]
        digest = "missing"
        if path_text != "missing" and (root / path_text).is_file():
            digest = hashlib.sha256((root / path_text).read_bytes()).hexdigest()
        image_entries.append({"sku_id": item["sku_id"], "sha256": digest})
    product_data = {
        "title": draft["title"],
        "description": draft["description"],
        "category_id": draft["description_category_id"],
        "type_id": draft["type_id"],
        "attributes": [
            item for item in (
                final_attributes.get("common_attributes")
                or final_attributes.get("attributes")
                or []
            )
            if item.get("target_value", item.get("value")) != "unknown"
        ],
        "attributes_by_sku": [
            {
                "sku_id": item["source_sku_id"],
                "attributes": [
                    attr for attr in _compiled_attributes_for_sku(final_attributes, str(item["source_sku_id"]))
                    if attr.get("target_value", attr.get("value")) != "unknown"
                ],
            }
            for item in draft["skus"]
        ],
        "skus": [
            {
                "source_sku_id": item["source_sku_id"],
                "offer_id": item["offer_id"],
                "display_name_ru": item["display_name_ru"],
            }
            for item in draft["skus"]
        ],
        "variant_grouping": {
            "product_group_id": (variant_grouping or {}).get("product_group_id"),
            "model_name_for_merge": (variant_grouping or {}).get("model_name_for_merge"),
            "common_product_name": (variant_grouping or {}).get("common_product_name"),
            "variant_mapping_status": (variant_grouping or {}).get("variant_mapping_status"),
            "variants": [
                {
                    "sku_id": item.get("sku_id"),
                    "offer_id": item.get("offer_id"),
                    "variant_attribute_values": item.get("variant_attribute_values", []),
                }
                for item in (variant_grouping or {}).get("variants", [])
            ],
        },
    }
    prices = sorted(config["sku_prices"], key=lambda item: item["source_sku_id"])
    return {
        "product_data_hash": _canonical_hash(product_data),
        "image_hash": _canonical_hash(image_entries),
        "price_hash": _canonical_hash({"currency": config["currency_code"], "prices": prices}),
    }


def _saved_existing_items(output: Path) -> List[Dict[str, Any]]:
    result_path = output / "ozon-result.json"
    if not result_path.is_file():
        return []
    result = load_json(result_path)
    if result.get("status") not in {"created", "updated"}:
        return []
    return [
        {
            "offer_id": str(item.get("offer_id", "")),
            "product_id": item.get("product_id") or "unknown",
        }
        for item in result.get("items", [])
        if item.get("offer_id")
    ]


def _live_existing_items(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_items = response.get("items")
    if not isinstance(raw_items, list):
        raw_items = response.get("result", {}).get("items", [])
    return [
        {
            "offer_id": str(item.get("offer_id", "")),
            "product_id": item.get("id") or item.get("product_id") or "unknown",
            "description_category_id": item.get("description_category_id") or "unknown",
            "type_id": item.get("type_id") or "unknown",
        }
        for item in raw_items
        if item.get("offer_id")
    ]


def _live_category_conflicts(
    response: Dict[str, Any], description_category_id: int, type_id: int,
) -> List[Dict[str, Any]]:
    conflicts = []
    for item in _live_existing_items(response):
        live_category = item.get("description_category_id")
        live_type = item.get("type_id")
        if not isinstance(live_category, int) or not isinstance(live_type, int):
            continue
        if live_category != description_category_id or live_type != type_id:
            conflicts.append({
                "offer_id": item["offer_id"],
                "live_category_id": live_category,
                "live_type_id": live_type,
                "requested_category_id": description_category_id,
                "requested_type_id": type_id,
            })
    return conflicts


def _saved_category_conflicts(output: Path, draft: Dict[str, Any]) -> List[Dict[str, Any]]:
    verification_path = output / "grouping-verification.json"
    if not verification_path.is_file():
        return []
    verification = load_json(verification_path)
    response = verification.get("last_api_response") or {}
    return _live_category_conflicts(
        response,
        int(draft["description_category_id"]),
        int(draft["type_id"]),
    )


def build_product_exists_check(
    product_dir: Path,
    draft: Dict[str, Any],
    final_attributes: Dict[str, Any],
    colors: Dict[str, Any],
    config: Dict[str, Any],
    variant_grouping: Optional[Dict[str, Any]] = None,
    live_response: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    output = product_dir / "output"
    current_hashes = _current_upload_hashes(
        product_dir, draft, final_attributes, colors, config, variant_grouping
    )
    previous_path = output / "ozon-last-upload-hashes.json"
    previous_hashes = load_json(previous_path) if previous_path.is_file() else None
    existing_items = (
        _live_existing_items(live_response)
        if live_response is not None else _saved_existing_items(output)
    )
    source_name = "ozon_seller_api" if live_response is not None else "saved_ozon_result"
    existing_by_offer = {item["offer_id"]: item for item in existing_items}
    all_unchanged = previous_hashes == current_hashes and previous_hashes is not None
    offers = []
    for sku in draft["skus"]:
        offer_id = sku["offer_id"]
        existing = existing_by_offer.get(offer_id)
        if existing and all_unchanged:
            action = "skip"
        elif existing:
            action = "update"
        else:
            action = "create"
        offers.append({
            "offer_id": offer_id,
            "source_sku_id": str(sku["source_sku_id"]),
            "exists": existing is not None,
            "action": action,
            "existing_product_id": existing["product_id"] if existing else "unknown",
        })
    exists = any(item["exists"] for item in offers)
    if exists and all(item["action"] == "skip" for item in offers):
        action, reason = "skip", "content_unchanged"
    elif exists:
        action, reason = "update", "existing_product_found"
    else:
        action, reason = "create", "product_not_found"
    first_existing = next((item for item in offers if item["exists"]), None)
    value = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "checked_at": now(),
        "check_source": source_name,
        "exists": exists,
        "action": action,
        "existing_product_id": first_existing["existing_product_id"] if first_existing else "unknown",
        "existing_offer_id": first_existing["offer_id"] if first_existing else "unknown",
        "reason": reason,
        "current_hashes": current_hashes,
        "previous_uploaded_hashes": previous_hashes,
        "offers": offers,
    }
    errors = validate(value, SCHEMAS["exists_check"])
    if errors:
        raise ValueError("product-exists-check.json failed validation: " + "; ".join(errors))
    write_json_atomic(output / "product-exists-check.json", value)
    return value


def build_upload_payload(product_dir: Path, mode: Optional[str] = None) -> Dict[str, Any]:
    """Build the complete local payload without opening a network client."""
    product_dir = product_dir.resolve()
    output = product_dir / "output"
    resolved_mode = mode or upload_mode()
    if resolved_mode not in {"dry-run", "production"}:
        raise UploadGateError("Upload mode must be dry-run or production")

    source = load_json(product_dir / "input/source.json")
    config = load_json(output / "ozon-upload-config.json")
    draft = apply_upload_config(load_json(output / "ozon-draft.json"), config)
    final_attributes = load_json(output / "ozon-attributes-final.json")
    canonical_tags = _canonical_tags_from_file(product_dir)
    category_attributes_path = output / "ozon-category-attributes.json"
    category_metadata = (
        load_json(category_attributes_path)
        if category_attributes_path.is_file() else {"attributes": []}
    )
    colors = load_json(output / "color-variants.json")
    rich = load_json(output / "rich-content.json")
    image_gate = _sync_draft_image_qc_from_current_gate(product_dir, draft)
    pricing = load_json(output / "pricing-result.json")
    grouping = load_json(output / "variant-grouping-result.json")
    exists_check = build_product_exists_check(
        product_dir, draft, final_attributes, colors, config, grouping
    )

    grouping_errors = validate(grouping, SCHEMAS["variant_grouping"])
    if grouping_errors:
        raise UploadGateError(
            "variant-grouping-result.json is invalid: " + "; ".join(grouping_errors)
        )

    price_by_sku = {str(item["source_sku_id"]): item["price"] for item in config["sku_prices"]}
    color_by_sku = {item["sku_id"]: item for item in colors["variants"]}
    variants = []
    for sku in draft["skus"]:
        sku_id = str(sku["source_sku_id"])
        color = color_by_sku.get(sku_id, {"color": "not_applicable", "image": "not_applicable"})
        variants.append({
            "source_sku_id": sku_id,
            "offer_id": sku["offer_id"],
            "sku_name": sku["source_sku_name"],
            "display_name_ru": sku["display_name_ru"],
            "price": price_by_sku[sku_id],
            "currency_code": config["currency_code"],
            "color": color["color"],
            "color_image": color["image"],
            "attributes": sku["attributes"],
            "variant_attribute_values": next(
                (
                    item["variant_attribute_values"]
                    for item in grouping["variants"]
                    if item["sku_id"] == sku_id
                ),
                [],
            ),
        })

    public_urls = [item["public_url"] for item in rich["source_images"]]
    shared_template_urls = [
        f"pending_upload:{item['path']}"
        for item in draft["images"]
        if item.get("role") != "color"
        and not (
            item.get("role") == "main"
            and item.get("variant_scope") == "sku"
            and item.get("source_sku_id") not in {None, "", "all"}
        )
    ]
    variant_main_template_urls = {
        str(item["source_sku_id"]): f"pending_upload:{item['path']}"
        for item in draft["images"]
        if item.get("role") == "main"
        and item.get("variant_scope") == "sku"
        and item.get("source_sku_id") not in {None, "", "all"}
    }
    field_repair_log: List[Dict[str, Any]] = []
    api_items = build_import_items(
        draft,
        config,
        shared_template_urls or public_urls,
        final_attributes=final_attributes,
        variant_grouping=grouping,
        variant_main_image_urls=variant_main_template_urls,
        category_metadata=category_metadata,
        field_repair_log=field_repair_log,
        source=source,
        canonical_tags=canonical_tags,
    )
    for item, variant in zip(api_items, variants):
        item["color_image"] = (
            f"pending_upload:{variant['color_image']}"
            if variant["color_image"] not in {"missing", "not_applicable"} else item.get("primary_image", "")
        )

    blockers = list(image_gate["errors"])
    blockers.extend(_remote_content_blockers(api_items, category_metadata))
    saved_category_conflicts = _saved_category_conflicts(output, draft)
    if exists_check["action"] == "update" and saved_category_conflicts:
        write_json_atomic(output / "ozon-category-migration-block.json", {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "checked_at": now(),
            "status": "BLOCKED",
            "reason": "existing_offer_category_change_requires_separate_resolution",
            "conflicts": saved_category_conflicts,
        })
        blockers.append(
            "Existing Ozon offers use a different category/type; automatic cross-category UPDATE is blocked."
        )
    if grouping["variant_mapping_status"] == "SEPARATE_CARDS_REQUIRED":
        valid_separate_cards = (
            grouping.get("upload_strategy") == "separate_cards"
            and grouping.get("platform_can_merge") is False
        )
        if not valid_separate_cards:
            blockers.append(
                "SKU differences cannot use an official Ozon aspect and the separate-card strategy is incomplete."
            )
    elif grouping["variant_mapping_status"] == "RULE_REQUIRED" or not grouping["upload_allowed"]:
        blockers.append(
            "The same-source SKU group requires a missing local Ozon variant mapping rule."
        )
    if grouping["product_group_count"] != 1 or grouping["variant_count"] != len(variants):
        blockers.append("Variant grouping counts do not match the selected SKU payload.")
    payload = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "product_name_cn": source["title_cn"],
        "upload_mode": resolved_mode,
        "api_writes_performed": False,
        "generated_at": now(),
        "shop_name": config["shop_name"],
        "category": {
            "category_id": draft["description_category_id"],
            "type_id": draft["type_id"],
            "category_name": draft["category"]["category_name"],
            "source": "ozon_seller_api",
        },
        "product_group": grouping,
        "title": draft["title"],
        "description": draft["description"],
        "images": [
            {
                **item,
                "public_url": public_urls[index] if index < len(public_urls) else "unknown",
            }
            for index, item in enumerate(draft["images"])
        ],
        "attributes": final_attributes.get("common_attributes") or final_attributes.get("attributes") or [],
        "attributes_by_sku": final_attributes.get("attributes_by_sku") or {},
        "sku_measurements": final_attributes.get("sku_measurements") or {},
        "variants": variants,
        "price": {
            "currency_code": config["currency_code"],
            "source": "ozon-upload-config confirmed SKU prices; pricing-result.json presence validated",
        },
        "image_upload_gate": image_gate,
        "product_exists_check": exists_check,
        "production_blockers": list(dict.fromkeys(blockers)),
        "api_request_template": {
            "endpoint": OzonWriteClient.PRODUCT_IMPORT_ENDPOINT,
            "body": {"items": api_items},
            "pricing_snapshot": pricing,
        },
    }
    errors = validate(payload, SCHEMAS["upload_payload"])
    if errors:
        raise ValueError("ozon-upload-payload.json failed validation: " + "; ".join(errors))
    write_json_atomic(output / "ozon-upload-payload.json", payload)
    compiler_repair_path = output / "ozon-field-repair-report.json"
    compiler_repairs = load_json(compiler_repair_path) if compiler_repair_path.is_file() else {}
    compiler_repairs.update({
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "generated_at": now(),
        "payload_finalization": {
            "repairs": field_repair_log,
            "summary": {
            "repaired_or_removed": len(field_repair_log),
            "payload_item_count": len(api_items),
            },
        },
    })
    write_json_atomic(compiler_repair_path, compiler_repairs)
    return payload


def assert_production_allowed(product_dir: Path, payload: Dict[str, Any]) -> None:
    require_production_mode()
    image_gate = payload.get("image_upload_gate") or {}
    if image_gate.get("status") != "PASS" or not image_gate.get("passed"):
        errors = "；".join(image_gate.get("errors") or ["图片未准备完成"])
        raise UploadGateError("当前图片不完整，不能上传：" + errors)
    if payload["production_blockers"]:
        raise UploadGateError("; ".join(payload["production_blockers"]))


def build_import_items(
    draft: Dict[str, Any],
    config: Dict[str, Any],
    image_urls: List[str],
    final_attributes: Optional[Dict[str, Any]] = None,
    color_image_urls: Optional[Dict[str, str]] = None,
    variant_grouping: Optional[Dict[str, Any]] = None,
    variant_main_image_urls: Optional[Dict[str, str]] = None,
    category_metadata: Optional[Dict[str, Any]] = None,
    field_repair_log: Optional[List[Dict[str, Any]]] = None,
    source: Optional[Dict[str, Any]] = None,
    canonical_tags: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    prices = {item["source_sku_id"]: item["price"] for item in config["sku_prices"]}
    colors = {item["source_sku_id"]: item for item in config["sku_colors"]}
    variant_values = {
        item["sku_id"]: item.get("variant_attribute_values", [])
        for item in (variant_grouping or {}).get("variants", [])
    }
    separate_cards = (
        (variant_grouping or {}).get("variant_mapping_status") == "SEPARATE_CARDS_REQUIRED"
        and (variant_grouping or {}).get("upload_strategy") == "separate_cards"
        and (variant_grouping or {}).get("platform_can_merge") is False
    )
    metadata_by_id = {
        int(item.get("attribute_id") or item.get("id") or 0): item
        for item in (category_metadata or {}).get("attributes") or []
        if item.get("attribute_id") is not None or item.get("id") is not None
    }
    repair_log = field_repair_log if field_repair_log is not None else []
    items = []
    for sku in draft["skus"]:
        sku_id = str(sku["source_sku_id"])
        dimensions, package_weight = _sku_package_measurement(final_attributes, sku_id, config)
        variant_main = (variant_main_image_urls or {}).get(sku_id)
        offer_image_urls = list(dict.fromkeys(
            ([variant_main] if variant_main else []) + image_urls
        ))
        if not offer_image_urls:
            raise UploadGateError(f"SKU {sku_id} has no usable main or shared product images")
        attributes = [
            _dictionary_attribute(config["brand"]),
            _dictionary_attribute(config["type"]),
        ]
        # The category compiler owns the model field used by Ozon to merge
        # variants.  A legacy upload config must not overwrite it.
        model_config = _compiled_model_name_config(
            final_attributes,
            config.get("model_name") or {},
        )
        if int(model_config.get("attribute_id") or 0) > 0:
            # Attribute 9048 is Ozon's "model name for grouping".  Colour
            # variants share one value so Ozon merges them into one card; a
            # size/measurement group Ozon cannot merge (SEPARATE_CARDS_REQUIRED)
            # needs a distinct per-SKU model name so Ozon does not fold them
            # back into a single card.
            if separate_cards:
                model_config = {
                    **model_config,
                    "value": f"{model_config.get('value') or draft['title']} {sku_id}",
                }
            attributes.append(_text_attribute(model_config))
        if sku_id in colors:
            attributes.append(_dictionary_attribute(colors[sku_id]))
        for variant_value in variant_values.get(sku_id, []):
            if variant_value["attribute_id"] not in {item["id"] for item in attributes}:
                attribute_id = int(variant_value["attribute_id"])
                raw_variant_value = variant_value.get("value")
                if attribute_id == 10096 and variant_value.get("dictionary_value_id") is None:
                    continue
                if is_color_name_attribute(attribute_id, variant_value.get("attribute_name")):
                    normalized_color = normalize_russian_color_name(raw_variant_value)
                    if not normalized_color:
                        continue
                    raw_variant_value = normalized_color
                attribute_input = {
                    "attribute_id": attribute_id,
                    "value": raw_variant_value,
                }
                if variant_value.get("dictionary_value_id") is not None:
                    attribute_input["dictionary_value_id"] = variant_value["dictionary_value_id"]
                    attributes.append(_dictionary_attribute(attribute_input))
                else:
                    attributes.append(_text_attribute(attribute_input))
        if final_attributes:
            existing_ids = {item["id"] for item in attributes}
            attributes.extend(_compiled_api_attributes_for_sku(final_attributes, sku_id, existing_ids))
        hashtag_attribute_id = _hashtag_attribute_id(metadata_by_id)
        if canonical_tags and hashtag_attribute_id and hashtag_attribute_id not in {int(item["id"]) for item in attributes}:
            attributes.append({
                "complex_id": 0,
                "id": hashtag_attribute_id,
                "values": [{"value": " ".join(canonical_tags)}],
            })
        compiled_by_id = {
            int(item.get("attribute_id") or 0): item
            for item in _compiled_attributes_for_sku(final_attributes or {}, sku_id)
            if item.get("attribute_id") is not None
        }
        attributes = _repair_final_api_attributes(
            attributes,
            metadata=metadata_by_id,
            compiled_by_id=compiled_by_id,
            blocked_tag_terms=_payload_tag_terms(config, draft, sku, source),
            canonical_tags=canonical_tags,
            repair_log=repair_log,
            sku_id=sku_id,
        )
        if canonical_tags and hashtag_attribute_id:
            tag_values = [
                value.get("value")
                for attribute in attributes
                if int(attribute.get("id") or 0) == hashtag_attribute_id
                for value in attribute.get("values") or []
            ]
            if not tag_values:
                raise UploadGateError(f"SKU {sku_id} is missing the Ozon hashtag attribute in the final import payload")
        common_name = str((variant_grouping or {}).get("common_product_name") or "").strip()
        if common_name.casefold() in {"", "unknown", "none", "null"}:
            common_name = str(draft["title"]).strip()
        if separate_cards:
            sku_label = str(
                sku.get("display_name_ru") or sku.get("source_sku_name") or sku_id
            ).strip()
            product_type = str(config.get("type", {}).get("value") or "").strip()
            if product_type.casefold() in {"", "unknown", "none", "null"}:
                product_type = str(draft["title"]).strip()
            common_name = f"{product_type}, {sku_label}"[:500]
        item = {
            "attributes": attributes,
            "barcode": "",
            "description_category_id": draft["description_category_id"],
            "type_id": draft["type_id"],
            "name": common_name,
            "offer_id": sku["offer_id"],
            "price": prices[sku_id],
            "currency_code": config["currency_code"],
            "vat": config["vat"],
            "depth": ozon_dimension_mm(dimensions["length_mm"]),
            "width": ozon_dimension_mm(dimensions["width_mm"]),
            "height": ozon_dimension_mm(dimensions["height_mm"]),
            "dimension_unit": "mm",
            "weight": ozon_weight_grams(package_weight["value_g"]),
            "weight_unit": "g",
            "images": offer_image_urls,
            "primary_image": offer_image_urls[0],
            "images360": [],
            "complex_attributes": [],
            "color_image": (
                (color_image_urls or {}).get(sku_id)
                or variant_main
                or offer_image_urls[0]
            ),
        }
        description = str(draft.get("description") or "").strip()
        if description:
            item["description"] = description
        if config["old_price"] is not None:
            item["old_price"] = config["old_price"]
        items.append(item)
    return items


def _write_update_request_summary(
    product_dir: Path,
    items: List[Dict[str, Any]],
    exists_check: Dict[str, Any],
    grouping: Dict[str, Any],
    requested_at: str,
) -> None:
    upload_config = load_json(product_dir / "output/ozon-upload-config.json")
    brand_attribute_id = int(upload_config["brand"]["attribute_id"])
    model_attribute_id = int((upload_config.get("model_name") or {}).get("attribute_id") or 0)
    variant_attribute_id = (grouping.get("variant_attribute") or {}).get("attribute_id")
    existing_by_offer = {
        item["offer_id"]: item["existing_product_id"]
        for item in exists_check["offers"]
    }
    summary_items = []
    for item in items:
        attributes = {attribute["id"]: attribute["values"] for attribute in item["attributes"]}
        summary_items.append({
            "offer_id": item["offer_id"],
            "name": item["name"],
            "existing_product_id": existing_by_offer.get(item["offer_id"], "unknown"),
            "category_id": item["description_category_id"],
            "type_id": item["type_id"],
            "brand": attributes.get(brand_attribute_id),
            "model_attribute_id": model_attribute_id,
            "model_value": attributes.get(model_attribute_id) if model_attribute_id > 0 else None,
            "variant_attribute_id": variant_attribute_id,
            "variant_value": attributes.get(variant_attribute_id),
            "price": item["price"],
            "currency_code": item["currency_code"],
            "image_count": len(item["images"]),
            "stock_update_sent": False,
        })
    summary = {
        "schema_version": "1.0.0",
        "action": "update",
        "endpoint": OzonWriteClient.PRODUCT_IMPORT_ENDPOINT,
        "requested_at": requested_at,
        "product_group_id": grouping["product_group_id"],
        "offer_count": len(items),
        "api_write_count": 1,
        "stock_endpoint_called": False,
        "request_body_sha256": _canonical_hash({"items": items}),
        "request_body": {"items": items},
        "offers": summary_items,
    }
    write_json_atomic(
        product_dir / "output" / "ozon-update-request-summary.json", summary
    )


def _sync_draft_preflight(draft: Dict[str, Any], preflight: Dict[str, Any]) -> None:
    draft["upload_allowed"] = preflight["upload_allowed"]
    draft["preflight"] = {
        "status": "pass" if preflight["upload_allowed"] else "failed",
        "errors": preflight["errors"],
        "warnings": preflight["warnings"],
        "checked_at": preflight["checked_at"],
        "metadata_source": "ozon_upload_preflight",
        "missing_required_attributes": [],
        "invalid_values": [],
    }


def prepare_upload(product_dir: Path) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    output = product_dir / "output"
    checked_at = now()
    config = load_json(output / "ozon-upload-config.json")
    draft = apply_upload_config(load_json(output / "ozon-draft.json"), config)
    status = load_json(product_dir / "status.json")
    metadata = load_json(output / "ozon-category-attributes.json")
    colors_path = output / "color-variants.json"
    color_variants = load_json(colors_path) if colors_path.is_file() else None
    grouping = load_json(output / "variant-grouping-result.json")
    manifest = stage_images(product_dir, draft, checked_at, color_variants)
    image_gate = _sync_draft_image_qc_from_current_gate(product_dir, draft)
    preflight = build_preflight(
        product_dir, draft, status, config, metadata, manifest, checked_at, image_gate=image_gate
    )
    _sync_draft_preflight(draft, preflight)
    preview = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "product_name_cn": load_json(product_dir / "input/source.json")["title_cn"],
        "shop_name": config["shop_name"],
        "currency_code": config["currency_code"],
        "stock_mode": config["stock_mode"],
        "sku_prices": config["sku_prices"],
        "category": draft["category"],
        "images": [{"slot": item["slot"], "local_path": item["local_path"]} for item in manifest["images"]],
        "preflight": preflight,
        "payload_template": build_import_items(
            draft,
            config,
            [
                f"https://pending.invalid/{item['staged_name']}"
                for item in manifest["images"] if item["role"] not in {"color", "variant_main"}
            ],
            load_json(output / "ozon-attributes-final.json")
            if (output / "ozon-attributes-final.json").is_file() else None,
            {
                item["slot"].removeprefix("color-"): f"https://pending.invalid/{item['staged_name']}"
                for item in manifest["images"] if item["role"] == "color"
            },
            grouping,
            {
                str(item.get("source_sku_id")): f"https://pending.invalid/{item['staged_name']}"
                for item in manifest["images"] if item["role"] == "variant_main"
            },
            category_metadata=metadata,
            source=load_json(product_dir / "input/source.json"),
            canonical_tags=_canonical_tags_from_file(product_dir),
        ),
    }
    for name, value, schema_key in (
        ("ozon-upload-config.json", config, "config"),
        ("ozon-images.json", manifest, "images"),
        ("ozon-upload-preflight.json", preflight, "preflight"),
        ("ozon-draft.json", draft, "draft"),
    ):
        errors = validate(value, SCHEMAS[schema_key])
        if errors:
            raise ValueError(f"{name} failed schema validation: " + "; ".join(errors))
    write_json_atomic(output / "ozon-images.json", manifest)
    write_json_atomic(output / "ozon-upload-preflight.json", preflight)
    write_json_atomic(output / "ozon-upload-preview.json", preview)
    write_json_atomic(output / "ozon-draft.json", draft)
    result_path = output / "ozon-result.json"
    if not result_path.exists():
        initial_result = _initial_result(product_dir, config["shop_name"], checked_at)
        result_errors = validate(initial_result, SCHEMAS["result"])
        if result_errors:
            raise ValueError("ozon-result.json failed validation: " + "; ".join(result_errors))
        write_json_atomic(result_path, initial_result)
    return preview


def _initial_result(product_dir: Path, shop_name: str, created_at: str) -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "local_product_id": product_dir.name,
        "shop_name": shop_name,
        "task_id": "unknown",
        "status": "not_started",
        "moderation_status": "not_submitted",
        "items": [],
        "errors": [],
        "error_code": None,
        "error_message": None,
        "failed_step": None,
        "created_at": created_at,
        "raw_response": None,
    }


def _transition_to_uploading(status: Dict[str, Any], at: str, action: str = "create") -> None:
    previous = status["status"]
    target = "UPLOADING"
    step_name = "ozon_update" if action == "update" else "ozon_upload"
    status["status"] = target
    status["current_step"] = step_name
    status["progress"] = 90
    status["history"].append({
        "from": previous,
        "to": target,
        "at": at,
        "reason": f"Authorized single-product Ozon {action} started after all upload gates passed.",
    })
    status["steps"].append({
        "name": step_name,
        "status": "in_progress",
        "started_at": at,
        "finished_at": "unknown",
        "retry_count": 0,
        "retryable": True,
        "error": None,
    })
    status["ozon"]["upload_status"] = "updating" if action == "update" else "uploading"


def _save_failure(
    product_dir: Path,
    status: Dict[str, Any],
    result: Dict[str, Any],
    step: str,
    error: Exception,
) -> None:
    at = now()
    result.update({
        "status": "failed",
        "moderation_status": "not_submitted_or_unknown",
        "error_code": type(error).__name__,
        "error_message": str(error),
        "failed_step": step,
    })
    previous = status["status"]
    status["status"] = "NEEDS_ATTENTION"
    status["current_step"] = step
    status["history"].append({"from": previous, "to": "NEEDS_ATTENTION", "at": at, "reason": str(error)})
    status["ozon"]["upload_status"] = "failed"
    status["ozon"]["errors"].append({
        "step": step if step in {"ozon_upload", "ozon_update"} else "ozon_upload",
        "reason": str(error),
        "occurred_at": at,
        "retryable": True,
    })
    for item in reversed(status["steps"]):
        if item["name"] in {"ozon_upload", "ozon_update"} and item["status"] == "in_progress":
            item["status"] = "failed"
            item["finished_at"] = at
            item["error"] = {
                "code": type(error).__name__,
                "reason": str(error),
                "details": {"failed_step": step},
            }
            break
    draft_path = product_dir / "output/ozon-draft.json"
    if draft_path.is_file():
        draft = load_json(draft_path)
        draft["upload_allowed"] = False
        draft["preflight"]["status"] = "failed"
        if str(error) not in draft["preflight"]["errors"]:
            draft["preflight"]["errors"].append(str(error))
        write_json_atomic(draft_path, draft)
    write_json_atomic(product_dir / "output/ozon-result.json", result)
    write_json_atomic(product_dir / "status.json", status)


def _parse_import_result(
    product_dir: Path,
    shop_name: str,
    task_id: int,
    response: Dict[str, Any],
    action: str = "create",
) -> Dict[str, Any]:
    raw_items = response.get("result", {}).get("items", [])
    items = []
    errors = []
    warnings = []
    source_by_offer = {
        item["offer_id"]: str(item["source_sku_id"])
        for item in load_json(product_dir / "output/ozon-draft.json")["skus"]
    }
    for item in raw_items:
        item_errors = item.get("errors") or []
        product_id = item.get("product_id") or "unknown"
        item_status = str(item.get("status") or "unknown").casefold()
        item_created = product_id != "unknown" and item_status in {
            "imported", "success", "processed", "created", "updated",
        }
        for item_error in item_errors:
            if "warning" in str(item_error.get("level") or "").lower():
                warnings.append(item_error)
            else:
                errors.append(item_error)
        items.append({
            "source_sku_id": source_by_offer.get(item.get("offer_id"), "unknown"),
            "offer_id": item.get("offer_id", "unknown"),
            "product_id": product_id,
            "status": item.get("status", "unknown"),
            "errors": item_errors,
        })
    created = bool(items) and not errors and all(
        item["product_id"] != "unknown"
        and str(item["status"]).casefold() in {"imported", "success", "processed", "created", "updated"}
        for item in items
    )
    return {
        "schema_version": "1.0.0",
        "local_product_id": product_dir.name,
        "shop_name": shop_name,
        "task_id": task_id,
        "status": ("updated" if action == "update" else "created")
        if created else ("failed" if errors else "processing"),
        "moderation_status": "pending" if created else "not_submitted_or_unknown",
        "items": items,
        "errors": errors,
        "warnings": warnings,
        "error_code": None if not errors else "OZON_IMPORT_ITEM_ERROR",
        "error_message": None if not errors else "One or more Ozon import items failed.",
        "failed_step": None if not errors else "product_import",
        "created_at": now(),
        "raw_response": response,
    }


def _reconcile_result_with_live_products(
    result: Dict[str, Any], product_response: Dict[str, Any], action: str,
) -> Dict[str, Any]:
    """Promote a stale import task when every expected offer exists and validates live."""
    live_items = product_response.get("items") or (product_response.get("result") or {}).get("items") or []
    live_by_offer = {
        str(item.get("offer_id")): item
        for item in live_items
        if isinstance(item, dict) and item.get("offer_id")
    }
    expected = [item for item in result.get("items") or [] if item.get("offer_id")]
    if not expected or any(str(item["offer_id"]) not in live_by_offer for item in expected):
        return result
    reconciled = copy.deepcopy(result)
    all_valid = True
    moderation_values = set()
    for item in reconciled["items"]:
        live = live_by_offer[str(item["offer_id"])]
        statuses = live.get("statuses") or {}
        product_id = live.get("id") or live.get("product_id")
        validation = str(statuses.get("validation_status") or "unknown").casefold()
        if not product_id or validation != "success":
            all_valid = False
            continue
        item["product_id"] = int(product_id)
        item["status"] = "imported"
        moderation_values.add(str(statuses.get("moderate_status") or "pending").casefold())
    if not all_valid:
        return result
    reconciled["status"] = "updated" if action == "update" else "created"
    reconciled["moderation_status"] = "approved" if moderation_values == {"approved"} else "pending"
    if reconciled.get("errors"):
        reconciled.setdefault("warnings", []).extend(reconciled["errors"])
        reconciled["errors"] = []
        reconciled["error_code"] = None
        reconciled["error_message"] = None
        reconciled["failed_step"] = None
    return reconciled


IMAGE_INGESTION_ERROR_CODES = {
    "primary_image_load_failed",
    "pics_download_server_unavailable",
    "some_image_failed",
}


def _ozon_cdn_rules() -> Dict[str, Any]:
    return load_json(ROOT / "rules/ozon-image-cdn-domains.json")


def _host_matches(host: str, exact_hosts: List[str], host_suffixes: List[str]) -> bool:
    normalized = host.casefold().rstrip(".")
    return normalized in {item.casefold().rstrip(".") for item in exact_hosts} or any(
        normalized.endswith("." + suffix.casefold().lstrip("."))
        for suffix in host_suffixes
    )


def _is_official_ozon_image_url(url: Any, rules: Optional[Dict[str, Any]] = None) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    rules = rules or _ozon_cdn_rules()
    if _host_matches(parsed.hostname, [], rules.get("temporary_host_suffixes") or []):
        return False
    return _host_matches(
        parsed.hostname,
        rules.get("exact_hosts") or [],
        rules.get("host_suffixes") or [],
    )


def _remote_terminal_errors(
    response: Dict[str, Any], offer_ids: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Return explicit Ozon validation/moderation failures, excluding warnings."""
    expected = set(offer_ids or [])
    items = response.get("items") or response.get("result", {}).get("items") or []
    errors: List[Dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        offer_id = str(item.get("offer_id") or "unknown")
        if expected and offer_id not in expected:
            continue
        statuses = item.get("statuses") or {}
        moderation = str(statuses.get("moderate_status") or "").casefold()
        item_errors = []
        for error in item.get("errors") or []:
            if not isinstance(error, dict):
                continue
            level = str(error.get("level") or "").casefold()
            if level in {"error", "error_level_error"}:
                item_errors.append({"offer_id": offer_id, **error})
        errors.extend(item_errors)
        if moderation in {"declined", "rejected", "failed"} and not item_errors:
            errors.append({
                "offer_id": offer_id,
                "code": "OZON_MODERATION_DECLINED",
                "field": "moderation_status",
                "level": "error",
                "message": f"Ozon moderation status is {moderation}.",
            })
    return errors


def _images_ingested(
    response: Dict[str, Any], offer_ids: List[str], expected_count: int
) -> bool:
    items = response.get("items", [])
    if not isinstance(items, list):
        return False
    rules = _ozon_cdn_rules()
    by_offer = {str(item.get("offer_id")): item for item in items}
    for offer_id in offer_ids:
        item = by_offer.get(offer_id)
        images = item.get("images", []) if item else []
        primary_images = item.get("primary_image", []) if item else []
        all_images = [*primary_images, *images]
        ingested_images = set(all_images)
        statuses = item.get("statuses") or {} if item else {}
        if not item or statuses.get("validation_status") != "success":
            return False
        if not primary_images or len(ingested_images) != expected_count:
            return False
        if not all(_is_official_ozon_image_url(url, rules) for url in all_images):
            return False
        error_codes = {
            error.get("code") for error in item.get("errors", [])
            if isinstance(error, dict)
        }
        if error_codes & IMAGE_INGESTION_ERROR_CODES:
            return False
    return True


def execute_upload(
    product_dir: Path,
    client: OzonWriteClient,
    required_action: Optional[str] = None,
) -> Dict[str, Any]:
    require_production_mode()
    product_dir = product_dir.resolve()
    output = product_dir / "output"
    idempotency_path = output / "ozon-idempotency.json"
    idempotency = load_json(idempotency_path) if idempotency_path.is_file() else {}
    existing_result = load_json(output / "ozon-result.json") if (output / "ozon-result.json").is_file() else {}
    remote_failed = str(existing_result.get("status") or "").lower() == "failed"
    if idempotency.get("api_write_completed") is True and idempotency.get("task_id") and not remote_failed:
        existing_status = str(existing_result.get("status") or "").lower()
        has_remote_product_id = any(
            str(item.get("product_id") or "").strip() not in {"", "unknown", "0"}
            for item in existing_result.get("items") or []
            if isinstance(item, dict)
        )
        update_after_created = (
            required_action == "update"
            and existing_status in {"created", "updated"}
            and has_remote_product_id
        )
        if not update_after_created:
            raise UploadGateError(
                "上一次 Ozon 导入任务还没有确认失败，禁止重复提交。请先执行只读状态查询。"
            )
    # Never trust a historical image report.  Rebuild the image
    # gate from the current image-plan and filesystem before any remote call,
    # especially before CREATE/UPDATE can be reached.
    current_check = current_upload_image_gate(product_dir)
    if current_check.get("status") != "PASS" or not current_check.get("passed"):
        raise UploadGateError("当前图片完整性检查失败：" + "；".join(current_check.get("errors") or ["图片未准备完成"]))
    # Input provenance is an independent hard gate.  It deliberately runs
    # after rebuilding the local image report so a stale PASS can never remain
    # visible, but still before the first Ozon read or write call.
    if not _is_ozon_reference_draft_upload(product_dir):
        try:
            validate_formal_product_input(product_dir)
        except ProductionInputError as exc:
            raise UploadGateError(f"正式生产输入门禁失败：{exc}") from exc
    payload = build_upload_payload(product_dir, mode="production")
    assert_production_allowed(product_dir, payload)
    offer_ids = [item["offer_id"] for item in payload["variants"]]
    live_response = client.get_products_info(offer_ids)
    config = load_json(output / "ozon-upload-config.json")
    draft = apply_upload_config(load_json(output / "ozon-draft.json"), config)
    final_attributes = load_json(output / "ozon-attributes-final.json")
    color_variants = load_json(output / "color-variants.json")
    exists_check = build_product_exists_check(
        product_dir,
        draft,
        final_attributes,
        color_variants,
        config,
        payload["product_group"],
        live_response=live_response,
    )
    payload["product_exists_check"] = exists_check
    payload_errors = validate(payload, SCHEMAS["upload_payload"])
    if payload_errors:
        raise ValueError("ozon-upload-payload.json failed validation: " + "; ".join(payload_errors))
    write_json_atomic(output / "ozon-upload-payload.json", payload)
    action = exists_check["action"]
    if idempotency.get("api_write_completed") is True and idempotency.get("task_id") and not remote_failed:
        if action == "create":
            raise UploadGateError(
                "An Ozon import task already exists; duplicate CREATE is forbidden."
            )
    if required_action is not None and action != required_action:
        raise UploadGateError(
            f"Required Ozon action is {required_action}, but the live existence check selected {action}"
        )
    if action == "update" and any(
        not item["exists"] or item["action"] != "update"
        for item in exists_check["offers"]
    ):
        raise UploadGateError(
            "UPDATE requires every selected offer_id to exist; mixed create/update is forbidden"
        )
    if action == "update":
        category_conflicts = _live_category_conflicts(
            live_response,
            int(draft["description_category_id"]),
            int(draft["type_id"]),
        )
        if category_conflicts:
            write_json_atomic(output / "ozon-category-migration-block.json", {
                "schema_version": "1.0.0",
                "product_id": product_dir.name,
                "checked_at": now(),
                "status": "BLOCKED",
                "reason": "existing_offer_category_change_requires_separate_resolution",
                "conflicts": category_conflicts,
            })
            raise UploadGateError(
                "Existing Ozon offers use a different category/type. Automatic cross-category UPDATE is blocked before the write request."
            )
    if action == "skip":
        existing = load_json(output / "ozon-result.json")
        skip_result = copy.deepcopy(existing)
        skip_result["status"] = "skipped"
        skip_result["moderation_status"] = existing.get("moderation_status", "unchanged")
        skip_result["created_at"] = now()
        write_json_atomic(output / "ozon-skip-result.json", skip_result)
        return skip_result

    preview = prepare_upload(product_dir)
    blocking_without_images = [
        check for check in preview["preflight"]["checks"]
        if not check["passed"] and check["name"] != "public_images"
    ]
    if blocking_without_images:
        raise UploadGateError("; ".join(item["detail"] for item in blocking_without_images))

    draft = load_json(output / "ozon-draft.json")
    status = load_json(product_dir / "status.json")
    metadata = load_json(output / "ozon-category-attributes.json")
    manifest = load_json(output / "ozon-images.json")
    result = _initial_result(product_dir, config["shop_name"], now())
    staging = output / "ozon-image-staging"

    try:
        with PersistentImageTunnel(staging) as tunnel:
            tunnel.public_image_urls(manifest)
            image_urls = [
                item["public_url"]
                for item in manifest["images"]
                if item["role"] not in {"color", "variant_main"}
            ]
            variant_main_image_urls = {
                str(item.get("source_sku_id")): item["public_url"]
                for item in manifest["images"] if item["role"] == "variant_main"
            }
            color_image_urls = {
                item["slot"].removeprefix("color-"): item["public_url"]
                for item in manifest["images"] if item["role"] == "color"
            }
            public_image_failures = verify_public_image_urls(manifest)
            if public_image_failures:
                write_json_atomic(output / "ozon-images.json", manifest)
                raise UploadGateError(
                    "图片公网链接无法被真实下载，已在调用Ozon前停止："
                    + "; ".join(public_image_failures[:8])
                )
            resolved_final_attributes = _resolve_rich_content_for_upload(
                output, final_attributes, manifest
            )
            checked_at = now()
            preflight = build_preflight(
                product_dir,
                draft,
                status,
                config,
                metadata,
                manifest,
                checked_at,
                image_gate=_sync_draft_image_qc_from_current_gate(product_dir, draft),
            )
            _sync_draft_preflight(draft, preflight)
            if not preflight["upload_allowed"] or not draft["upload_allowed"]:
                raise UploadGateError("Final upload preflight did not allow the write request")
            for value, schema_key in (
                (manifest, "images"),
                (preflight, "preflight"),
                (draft, "draft"),
            ):
                errors = validate(value, SCHEMAS[schema_key])
                if errors:
                    raise ValueError("Final upload package failed validation: " + "; ".join(errors))
            write_json_atomic(output / "ozon-images.json", manifest)
            write_json_atomic(output / "ozon-upload-preflight.json", preflight)
            write_json_atomic(output / "ozon-draft.json", draft)

            _transition_to_uploading(status, checked_at, action)
            write_json_atomic(product_dir / "status.json", status)
            items = build_import_items(
                draft,
                config,
                image_urls,
                final_attributes=resolved_final_attributes,
                color_image_urls=color_image_urls,
                variant_grouping=load_json(output / "variant-grouping-result.json"),
                variant_main_image_urls=variant_main_image_urls,
                category_metadata=metadata,
                source=load_json(product_dir / "input/source.json"),
                canonical_tags=_canonical_tags_from_file(product_dir),
            )
            if action == "update":
                _write_update_request_summary(
                    product_dir,
                    items,
                    exists_check,
                    payload["product_group"],
                    checked_at,
                )
            response = client.import_products(items)
            status["api_write_count"] = int(status.get("api_write_count") or 0) + 1
            status["last_run_at"] = now()
            write_json_atomic(product_dir / "status.json", status)
            write_json_atomic(output / "ozon-write-receipt.json", {
                "endpoint": OzonWriteClient.PRODUCT_IMPORT_ENDPOINT,
                "written_at": now(),
                "request_hash": _canonical_hash({"items": items}),
                "response": response,
            })
            task_id = response.get("result", {}).get("task_id")
            if not isinstance(task_id, int) or task_id <= 0:
                raise OzonUploadApiError(
                    OzonWriteClient.PRODUCT_IMPORT_ENDPOINT,
                    "response did not contain a valid task_id",
                )
            write_json_atomic(output / "ozon-idempotency.json", {
                "schema_version": "1.0.0",
                "payload_hash": _canonical_hash({"items": items}),
                "task_id": task_id,
                "api_write_completed": True,
                "offer_ids": [item["offer_id"] for item in items],
                "request_timestamp": now(),
            })
            result.update({
                "action": action,
                "task_id": task_id,
                "status": "submitted",
                "moderation_status": "pending",
                "raw_response": response,
            })
            status["ozon"]["task_id"] = str(task_id)
            status["ozon"]["shop_name"] = config["shop_name"]
            status["ozon"]["last_response"] = response
            previous = status.get("status")
            status.update({
                "status": "PENDING_REMOTE",
                "current_step": "ozon_upload",
                "progress": 99,
                "next_action": "read_only_status_query",
                "task_authorized": False,
                "upload_priority_state": "waiting_remote",
            })
            status.setdefault("history", []).append({
                "from": previous,
                "to": "PENDING_REMOTE",
                "at": now(),
                "reason": "Ozon accepted the import task; product card creation is waiting for read-only task result recovery.",
            })
            draft["upload_allowed"] = False
            draft["preflight"].update({
                "status": "submitted",
                "errors": [],
                "checked_at": now(),
            })
            write_json_atomic(output / "ozon-draft.json", draft)
            write_json_atomic(output / "ozon-result.json", result)
            write_json_atomic(product_dir / "status.json", status)
            for image in manifest["images"]:
                image["status"] = "submitted"
            manifest["hosting_mode"] = "submitted"
            write_json_atomic(output / "ozon-images.json", manifest)
            # Image channel lifetime is fixed at 24 hours and independent of
            # remote status polling. Do not enqueue a remote check here.
            channel = channel_state(product_dir)
            write_json_atomic(output / "ozon-image-transfer.json", {
                "status": "waiting_ozon_cdn",
                "checked_at": now(),
                "offer_ids": [item["offer_id"] for item in items],
                "temporary_channel_closed": False,
                "channel": channel,
                "channel_expires_at": channel.get("expires_at"),
            })
    except (OzonUploadApiError, ImageTunnelError, UploadGateError, ValueError) as exc:
        if status.get("status") == "UPLOADING":
            _save_failure(
                product_dir,
                status,
                result,
                "ozon_update" if action == "update" else "ozon_upload",
                exc,
            )
        raise

    if result["status"] in {"created", "updated"}:
        at = now()
        previous = status["status"]
        step_name = "ozon_update" if action == "update" else "ozon_upload"
        status["status"] = "OZON_MODERATION"
        status["current_step"] = step_name
        status["progress"] = 100
        status["history"].append({
            "from": previous,
            "to": "OZON_MODERATION",
            "at": at,
            "reason": f"Ozon product cards were {action}d without an inventory request; moderation is pending.",
        })
        status["ozon"]["upload_status"] = "uploaded"
        first = result["items"][0]
        status["ozon"]["product_id"] = str(first["product_id"])
        status["ozon"]["offer_id"] = first["offer_id"]
        status["ozon"]["last_response"] = result["raw_response"]
        for step in reversed(status["steps"]):
            if step["name"] == step_name and step["status"] == "in_progress":
                step["status"] = "completed"
                step["finished_at"] = at
                break
        draft["upload_allowed"] = False
        draft["preflight"]["status"] = "failed"
        draft["preflight"]["errors"] = [
            "The last Ozon import completed. Re-run field completion and hash checks before another update."
        ]
        draft["preflight"]["checked_at"] = at
        write_json_atomic(output / "ozon-draft.json", draft)
        write_json_atomic(
            output / "ozon-last-upload-hashes.json",
            exists_check["current_hashes"],
        )
        write_json_atomic(product_dir / "status.json", status)
    return result


def poll_existing_import(
    product_dir: Path,
    client: OzonWriteClient,
    timeout_seconds: int = 900,
) -> Dict[str, Any]:
    """Compatibility alias for a single non-blocking asynchronous status sync."""
    return recover_remote_import(product_dir, client, timeout_seconds=1)


def _remote_grouping_verification(
    product_dir: Path,
    result: Dict[str, Any],
    product_response: Dict[str, Any],
    attribute_response: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a neutral color-variant verification record from read-only Ozon data."""
    output = product_dir / "output"
    grouping = load_json(output / "variant-grouping-result.json")
    color_variants = load_json(output / "color-variants.json")
    color_by_sku = {str(item["sku_id"]): item for item in color_variants.get("variants") or []}
    product_items = product_response.get("items") or product_response.get("result", {}).get("items") or []
    live_by_offer = {str(item.get("offer_id")): item for item in product_items if isinstance(item, dict)}
    result_by_offer = {str(item.get("offer_id")): item for item in result.get("items") or []}
    offers = []
    all_created = True
    for variant in grouping.get("variants") or []:
        offer_id = str(variant["offer_id"])
        imported = result_by_offer.get(offer_id, {})
        live = live_by_offer.get(offer_id, {})
        product_id = imported.get("product_id") or live.get("id") or live.get("product_id") or "unknown"
        statuses = live.get("statuses") or {}
        moderation = str(statuses.get("moderate_status") or "pending")
        state = str(imported.get("status") or statuses.get("validation_status") or "pending")
        if product_id == "unknown":
            all_created = False
        color = color_by_sku.get(str(variant["sku_id"]), {})
        offers.append({
            "offer_id": offer_id,
            "ozon_product_id": product_id,
            "color": color.get("color", "unknown"),
            "image": color.get("image", "unknown"),
            "status": state,
            "moderation_status": moderation,
        })
    grouping_status = "pending"
    grouped_card_id = None
    if all_created:
        model_ids = {
            str((live_by_offer.get(item["offer_id"], {}).get("model_info") or {}).get("model_id"))
            for item in offers
            if (live_by_offer.get(item["offer_id"], {}).get("model_info") or {}).get("model_id")
        }
        if len(model_ids) == 1:
            grouping_status = "grouped"
            grouped_card_id = next(iter(model_ids))
    return {
        "schema_version": "1.0.0",
        "product_group_id": grouping["product_group_id"],
        "checked_at": now(),
        "expected_card_count": 1,
        "offer_count": len(offers),
        "offers": offers,
        "grouping_status": grouping_status,
        "grouped_card_id": grouped_card_id,
        "errors": result.get("errors") or [],
        "warnings": [] if all_created else ["Ozon has not assigned product IDs to every offer yet."],
        "last_api_response": product_response,
        "last_attribute_response": attribute_response,
    }


def recover_remote_import(
    product_dir: Path,
    client: OzonWriteClient,
    timeout_seconds: int = 1,
    poll_interval_seconds: int = 30,
) -> Dict[str, Any]:
    """Perform one read-only Ozon asynchronous-status sync; never submits a product."""
    product_dir = product_dir.resolve()
    if _product_deletion_requested(product_dir):
        raise UploadGateError(f"Local product {product_dir.name} was permanently deleted; remote result discarded")
    output = product_dir / "output"
    result = load_json(output / "ozon-result.json")
    receipt_path = output / "ozon-write-receipt.json"
    receipt = load_json(receipt_path) if receipt_path.is_file() else {}
    task_id = result.get("task_id") or (receipt.get("response") or {}).get("result", {}).get("task_id")
    if not isinstance(task_id, int) or task_id <= 0:
        raise UploadGateError("Cannot recover Ozon import without a valid task_id")
    idempotency_path = output / "ozon-idempotency.json"
    if not idempotency_path.is_file():
        grouping = load_json(output / "variant-grouping-result.json")
        write_json_atomic(idempotency_path, {
            "schema_version": "1.0.0",
            "payload_hash": receipt.get("request_hash", "unknown"),
            "task_id": task_id,
            "api_write_completed": True,
            "offer_ids": [item["offer_id"] for item in grouping.get("variants") or []],
            "request_timestamp": receipt.get("written_at", now()),
        })
    query_count = int((result.get("recovery") or {}).get("query_count") or 0)
    product_response: Dict[str, Any] = {}
    attribute_response: Dict[str, Any] = {}
    query_count += 1
    info = client.get_import_info(task_id)
    action = "update" if result.get("status") == "updated" else "create"
    result = _parse_import_result(product_dir, result.get("shop_name", "unknown"), task_id, info, action=action)
    checked_at = now()
    result["recovery"] = {
        "query_count": query_count,
        "last_checked_at": checked_at,
        "next_check_at": None,
    }
    offer_ids = [item["offer_id"] for item in result.get("items") or []]
    if offer_ids:
        product_response = client.get_products_info(offer_ids)
        result = _reconcile_result_with_live_products(result, product_response, action)
        remote_errors = _remote_terminal_errors(product_response, offer_ids)
        if remote_errors:
            first = remote_errors[0]
            texts = first.get("texts") or {}
            result.update({
                "status": "failed",
                "moderation_status": "declined",
                "error_code": str(first.get("code") or "OZON_REMOTE_DECLINED"),
                "error_message": str(
                    first.get("message")
                    or texts.get("message")
                    or texts.get("description")
                    or "Ozon rejected the product during validation or moderation."
                ),
                "errors": remote_errors,
            })
    terminal = result["status"] in {"created", "updated", "failed"}
    if terminal and result["status"] != "failed":
        attribute_response = client.get_product_attributes(offer_ids)
    if _product_deletion_requested(product_dir):
        raise UploadGateError(f"Local product {product_dir.name} was permanently deleted; remote result discarded")
    write_json_atomic(output / "ozon-result.json", result)

    status = load_json(product_dir / "status.json")
    previous_status = status.get("status")
    verification = _remote_grouping_verification(product_dir, result, product_response, attribute_response)
    verification_errors = validate(verification, SCHEMAS["grouping_verification"])
    if verification_errors:
        raise ValueError("grouping-verification.json failed validation: " + "; ".join(verification_errors))
    write_json_atomic(output / "grouping-verification.json", verification)
    if result["status"] == "failed":
        status.pop("remote_recovery", None)
        status.update({
            "status": "NEEDS_ATTENTION", "current_step": "ozon_status",
            "error_code": result.get("error_code") or "OZON_IMPORT_ITEM_ERROR",
            "error_message": result.get("error_message") or "Ozon import failed",
            "failed_step": "ozon_status", "next_action": "manual_rule_required",
        })
    elif result["status"] in {"created", "updated"}:
        status.pop("remote_recovery", None)
        moderation_values = {offer["moderation_status"] for offer in verification["offers"]}
        result["moderation_status"] = "approved" if moderation_values == {"approved"} else "pending"
        write_json_atomic(output / "ozon-result.json", result)
        status.update({
            "status": "UPLOADED" if moderation_values == {"approved"} else "OZON_MODERATION",
            "current_step": "ozon_status", "progress": 100,
            "error_code": "unknown", "error_message": "unknown", "failed_step": "unknown", "next_action": "complete",
        })
    else:
        result["recovery"]["next_check_at"] = None
        write_json_atomic(output / "ozon-result.json", result)
        status.update({
            "status": "PENDING_REMOTE", "current_step": "ozon_status", "progress": 100,
            "next_action": "remote_result_recovery",
            "remote_recovery": {
                "task_id": str(task_id), "query_count": query_count,
                "last_checked_at": checked_at,
                "next_check_at": "on_next_trigger",
                "api_write_completed": True,
            },
        })
    status.setdefault("ozon", {}).update({
        "upload_status": "failed" if result["status"] == "failed" else "uploaded",
        "task_id": str(task_id),
        "product_id": str((result.get("items") or [{}])[0].get("product_id") or "unknown"),
        "offer_id": str((result.get("items") or [{}])[0].get("offer_id") or "unknown"),
        "last_response": result.get("raw_response"),
        "errors": result.get("errors") or [],
    })
    status["last_run_at"] = now()
    if previous_status != status.get("status"):
        status.setdefault("history", []).append({
            "from": previous_status,
            "to": status["status"],
            "at": now(),
            "reason": "Ozon import result was recovered through a read-only remote task query.",
        })
    _sync_remote_pending_queue(product_dir, result, status)
    sync_image_channel_status(product_dir, client, product_response=product_response)
    write_json_atomic(product_dir / "status.json", status)
    return result


def repair_uploaded_images(
    product_dir: Path, client: OzonWriteClient, force_resubmit: bool = False
) -> Dict[str, Any]:
    require_production_mode()
    product_dir = product_dir.resolve()
    output = product_dir / "output"
    status = load_json(product_dir / "status.json")
    if status.get("status") != "UPLOADED":
        raise UploadGateError("Image repair is allowed only for an already uploaded product")

    config = load_json(output / "ozon-upload-config.json")
    draft = load_json(output / "ozon-draft.json")
    if config.get("stock_mode") != "not_set" or draft["stock"]["quantity"] is not None:
        raise UploadGateError("Image repair requires stock to remain unset")

    offer_ids = [item["offer_id"] for item in draft["skus"]]
    existing_info = client.get_products_info(offer_ids)
    if not force_resubmit and _images_ingested(existing_info, offer_ids, len(draft["images"])):
        result = load_json(output / "ozon-result.json")
        result["moderation_status"] = "approved"
        previous_raw = result.get("raw_response")
        result["raw_response"] = {
            "import": previous_raw.get("import", previous_raw)
            if isinstance(previous_raw, dict) else previous_raw,
            "image_verification": existing_info,
        }
        manifest = load_json(output / "ozon-images.json")
        for image in manifest["images"]:
            image["status"] = "imported"
        manifest["hosting_mode"] = "completed"
        status["ozon"]["last_response"] = result["raw_response"]
        verified_warning = (
            "ozon_images: Ozon verified one primary image and three detail images "
            "for every uploaded SKU."
        )
        if verified_warning not in status["warnings"]:
            status["warnings"].append(verified_warning)
        write_json_atomic(output / "ozon-images.json", manifest)
        write_json_atomic(output / "ozon-result.json", result)
        write_json_atomic(product_dir / "status.json", status)
        return result

    manifest = stage_images(product_dir, draft, now())
    staging = output / "ozon-image-staging"
    with PersistentImageTunnel(staging) as tunnel:
        tunnel.public_image_urls(manifest)
        image_urls = [
            item["public_url"]
            for item in manifest["images"]
            if item["role"] not in {"color", "variant_main"}
        ]
        variant_main_image_urls = {
            str(item.get("source_sku_id")): item["public_url"]
            for item in manifest["images"] if item["role"] == "variant_main"
        }
        public_image_failures = verify_public_image_urls(manifest)
        if public_image_failures:
            write_json_atomic(output / "ozon-images.json", manifest)
            raise UploadGateError(
                "图片公网链接无法被真实下载，已在调用Ozon前停止："
                + "; ".join(public_image_failures[:8])
            )
        items = build_import_items(
            draft,
            config,
            image_urls,
            final_attributes=load_json(output / "ozon-attributes-final.json")
            if (output / "ozon-attributes-final.json").is_file() else None,
            variant_grouping=load_json(output / "variant-grouping-result.json"),
            variant_main_image_urls=variant_main_image_urls,
            category_metadata=load_json(output / "ozon-category-attributes.json")
            if (output / "ozon-category-attributes.json").is_file() else None,
            source=load_json(product_dir / "input/source.json")
            if (product_dir / "input/source.json").is_file() else None,
            canonical_tags=_canonical_tags_from_file(product_dir),
        )
        response = client.create_products(items)
        task_id = response.get("result", {}).get("task_id")
        if not isinstance(task_id, int) or task_id <= 0:
            raise OzonUploadApiError(
                OzonWriteClient.PRODUCT_IMPORT_ENDPOINT,
                "image repair response did not contain a valid task_id",
            )
        payload_hash = _canonical_hash({"items": items})
        write_json_atomic(output / "ozon-image-update-receipt.json", {
            "endpoint": OzonWriteClient.PRODUCT_IMPORT_ENDPOINT,
            "written_at": now(),
            "request_hash": payload_hash,
            "response": response,
            "inventory_api_called": False,
        })
        result = _initial_result(product_dir, config["shop_name"], now())
        result.update({
            "task_id": task_id, "status": "submitted", "moderation_status": "pending",
            "action": "image_repair", "upload_action": "image_repair",
            "raw_response": response,
        })
        write_json_atomic(output / "ozon-idempotency.json", {
            "schema_version": "1.0.0", "payload_hash": payload_hash,
            "task_id": task_id, "api_write_completed": True,
            "offer_ids": [item["offer_id"] for item in items], "request_timestamp": now(),
        })
        status["api_write_count"] = int(status.get("api_write_count") or 0) + 1
        status.update({
            "status": "PENDING_REMOTE",
            "current_step": "ozon_upload",
            "progress": 99,
            "next_action": "read_only_status_query",
            "task_authorized": False,
            "upload_priority_state": "waiting_remote",
        })
        status["ozon"]["task_id"] = str(task_id)
        status["ozon"]["last_response"] = response
        for image in manifest["images"]:
            image["status"] = "submitted"
        manifest["hosting_mode"] = "submitted"
        channel = channel_state(product_dir)
        write_json_atomic(output / "ozon-image-transfer.json", {
            "status": "waiting_ozon_cdn", "checked_at": now(),
            "offer_ids": [item["offer_id"] for item in items],
            "temporary_channel_closed": False, "channel": channel,
            "channel_expires_at": channel.get("expires_at"),
        })
    write_json_atomic(output / "ozon-images.json", manifest)
    write_json_atomic(output / "ozon-result.json", result)
    write_json_atomic(product_dir / "status.json", status)
    return result
