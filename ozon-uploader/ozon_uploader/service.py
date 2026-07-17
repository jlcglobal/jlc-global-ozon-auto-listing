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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
except ModuleNotFoundError:  # package execution from the repository root
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from image_asset_boundaries import (
        accepted_counterpart,
        asset_contract_path,
        validate_accepted_manifest,
        validate_generated_output,
    )
    from production_input_guard import ProductionInputError, validate_formal_product_input


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
    "final_upload_check": TEMPLATES / "final-upload-check.schema.json",
    "upload_payload": TEMPLATES / "ozon-upload-payload.schema.json",
    "exists_check": TEMPLATES / "product-exists-check.schema.json",
    "variant_grouping": TEMPLATES / "variant-grouping-result.schema.json",
    "grouping_verification": TEMPLATES / "grouping-verification.schema.json",
}


class UploadGateError(RuntimeError):
    pass


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

    This deliberately ignores the previous final-upload-check result.  A stale
    PASS must never allow a CREATE/UPDATE after images were removed or replaced.
    """
    output = product_dir / "output"
    plan = load_json(output / "image-plan.json") if (output / "image-plan.json").is_file() else {}
    draft = load_json(output / "ozon-draft.json") if (output / "ozon-draft.json").is_file() else {}
    selected_skus = [str(item.get("source_sku_id")) for item in draft.get("skus") or []]
    planned_main = list(plan.get("main_images") or [])
    planned_detail = list(plan.get("detail_images") or [])
    errors: List[str] = []
    main_results: List[Dict[str, Any]] = []
    detail_results: List[Dict[str, Any]] = []
    asset_contract = load_json(asset_contract_path(product_dir)) if asset_contract_path(product_dir).is_file() else {}
    manual_confirmation_required = bool(asset_contract.get("manual_confirmation_required"))

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


def refresh_current_image_check(product_dir: Path) -> Dict[str, Any]:
    """Rewrite final-upload-check.json with the current image result."""
    output = product_dir / "output"
    check_path = output / "final-upload-check.json"
    check = load_json(check_path) if check_path.is_file() else {
        "schema_version": "1.0.0", "product_id": product_dir.name,
        "status": "FAIL", "upload_allowed": False, "checks": [], "errors": [], "warnings": [],
    }
    result = current_image_completeness(product_dir)
    checks = [item for item in check.get("checks") or [] if item.get("name") not in {"image_slot_completeness", "images_qc"}]
    detail = "每个已选SKU必须有合格主图，且商品必须正好有8张详情图。"
    if result["errors"]:
        detail += " " + "；".join(result["errors"])
    checks.extend([
        {"name": "image_slot_completeness", "passed": result["passed"], "detail": detail},
        {"name": "images_qc", "passed": result["passed"], "detail": "当前 image-plan 中的全部图片文件必须存在、可读取并完成生成。"},
    ])
    check["checks"] = checks
    errors = [str(item) for item in check.get("errors") or [] if "详情图" not in str(item) and "SKU" not in str(item)]
    if result["errors"]:
        errors.extend(result["errors"])
    check["errors"] = list(dict.fromkeys(errors))
    check["status"] = "PASS" if not check["errors"] and all(item.get("passed") for item in checks) else "FAIL"
    check["upload_allowed"] = check["status"] == "PASS"
    check["checked_at"] = result["checked_at"]
    write_json_atomic(check_path, check)
    return check


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
        item["id"] == value_id and item["value"].casefold() == value.casefold()
        for item in attribute["allowed_values"]
    )


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
        config["model_name"]["attribute_id"]: ("model_name", config["model_name"], "human"),
        config["type"]["attribute_id"]: ("product_type", config["type"], "source"),
    }
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


def build_preflight(
    product_dir: Path,
    draft: Dict[str, Any],
    status: Dict[str, Any],
    config: Dict[str, Any],
    metadata: Dict[str, Any],
    image_manifest: Dict[str, Any],
    checked_at: str,
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
    final_by_id = {
        int(item["attribute_id"]): item
        for item in final_attributes_for_gate.get("attributes", [])
    }
    missing_required = [
        item["attribute_name"]
        for item in metadata.get("attributes", [])
        if item.get("required") is True
        and (
            int(item["attribute_id"]) not in final_by_id
            or final_by_id[int(item["attribute_id"])].get("value") in {None, "unknown"}
        )
    ]
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
    dimensions = config["package_dimensions"]
    weight = config["package_weight"]
    product_dimensions = config.get("product_dimensions") or {}
    product_weight = config.get("product_weight") or {}
    add(
        "package_measurements",
        all(dimensions[key] > 0 for key in ("length_mm", "width_mm", "height_mm"))
        and weight["value_g"] > 0,
        "Positive package dimensions and weight are required.",
    )
    add(
        "measurement_hierarchy",
        float(weight.get("value_g") or 0) > float(product_weight.get("value_g") or 0) > 0
        and all(
            float(dimensions.get(key) or 0) > float(product_dimensions.get(key) or 0) > 0
            for key in ("length_mm", "width_mm", "height_mm")
        ),
        "Package weight and every package dimension must be strictly greater than product measurements.",
    )
    generated_files_ok = all(
        (ROOT / item["path"]).is_file() and item["qc_status"] == "pass"
        for item in draft["images"]
    )
    add(
        "generated_images",
        generated_files_ok,
        "Every generated image must exist locally and have qc_status=pass.",
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
    add(
        "field_completion_tags_count",
        tags.get("count") == 30 and len(set(tags.get("tags", []))) == 30,
        "Field completion requires exactly 30 unique Ozon hashtags.",
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
    if item["value"] == "unknown":
        return None
    value: Dict[str, Any] = {"value": str(item["value"])}
    if item.get("dictionary_value_id") is not None:
        value["dictionary_value_id"] = item["dictionary_value_id"]
    return {"complex_id": 0, "id": item["attribute_id"], "values": [value]}


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
    for item in updated["attributes"]:
        if rich_attribute_id is not None and item["attribute_id"] == rich_attribute_id:
            item["value"] = serialized
            item["evidence"] = ["rich-content.json resolved through production image tunnel"]
            break
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
            item for item in final_attributes["attributes"] if item["value"] != "unknown"
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
    colors = load_json(output / "color-variants.json")
    rich = load_json(output / "rich-content.json")
    final_check = load_json(output / "final-upload-check.json")
    pricing = load_json(output / "pricing-result.json")
    grouping = load_json(output / "variant-grouping-result.json")
    exists_check = build_product_exists_check(
        product_dir, draft, final_attributes, colors, config, grouping
    )

    schema_errors = validate(final_check, SCHEMAS["final_upload_check"])
    if schema_errors:
        raise UploadGateError("final-upload-check.json is invalid: " + "; ".join(schema_errors))
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
    api_items = build_import_items(
        draft,
        config,
        shared_template_urls or public_urls,
        variant_grouping=grouping,
        variant_main_image_urls=variant_main_template_urls,
    )
    optional_api_attributes = [
        converted for converted in (
            _final_attribute_to_api(item) for item in final_attributes["attributes"]
        ) if converted is not None
    ]
    base_attribute_ids = {
        int(config["brand"]["attribute_id"]),
        int(config["model_name"]["attribute_id"]),
        int(config["type"]["attribute_id"]),
        *[int(item["attribute_id"]) for item in config.get("sku_colors", [])],
    }
    optional_api_attributes = [
        item for item in optional_api_attributes if item["id"] not in base_attribute_ids
    ]
    for item, variant in zip(api_items, variants):
        item["attributes"].extend(copy.deepcopy(optional_api_attributes))
        item["color_image"] = (
            f"pending_upload:{variant['color_image']}"
            if variant["color_image"] not in {"missing", "not_applicable"} else ""
        )

    blockers = list(final_check["errors"])
    category_attributes_path = output / "ozon-category-attributes.json"
    category_metadata = (
        load_json(category_attributes_path)
        if category_attributes_path.is_file() else {"attributes": []}
    )
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
        "attributes": final_attributes["attributes"],
        "variants": variants,
        "price": {
            "currency_code": config["currency_code"],
            "source": "ozon-upload-config confirmed SKU prices; pricing-result.json presence validated",
        },
        "final_upload_check": final_check,
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
    return payload


def assert_production_allowed(product_dir: Path, payload: Dict[str, Any]) -> None:
    require_production_mode()
    final_check = payload["final_upload_check"]
    if final_check.get("status") != "PASS" or not final_check.get("upload_allowed"):
        raise UploadGateError("Production requires final-upload-check.status=PASS")
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
) -> List[Dict[str, Any]]:
    prices = {item["source_sku_id"]: item["price"] for item in config["sku_prices"]}
    colors = {item["source_sku_id"]: item for item in config["sku_colors"]}
    dimensions = config["package_dimensions"]
    variant_values = {
        item["sku_id"]: item.get("variant_attribute_values", [])
        for item in (variant_grouping or {}).get("variants", [])
    }
    separate_cards = (
        (variant_grouping or {}).get("variant_mapping_status") == "SEPARATE_CARDS_REQUIRED"
        and (variant_grouping or {}).get("upload_strategy") == "separate_cards"
        and (variant_grouping or {}).get("platform_can_merge") is False
    )
    items = []
    for sku in draft["skus"]:
        sku_id = str(sku["source_sku_id"])
        variant_main = (variant_main_image_urls or {}).get(sku_id)
        offer_image_urls = list(dict.fromkeys(
            ([variant_main] if variant_main else []) + image_urls
        ))
        if not offer_image_urls:
            raise UploadGateError(f"SKU {sku_id} has no usable main or shared product images")
        model_config = config["model_name"]
        if separate_cards:
            model_config = {
                **model_config,
                "value": f"{model_config['value']} {sku_id}",
            }
        attributes = [
            _dictionary_attribute(config["brand"]),
            _text_attribute(model_config),
            _dictionary_attribute(config["type"]),
        ]
        if sku_id in colors:
            attributes.append(_dictionary_attribute(colors[sku_id]))
        for variant_value in variant_values.get(sku_id, []):
            if variant_value["attribute_id"] not in {item["id"] for item in attributes}:
                attribute_input = {
                    "attribute_id": variant_value["attribute_id"],
                    "value": variant_value["value"],
                }
                if variant_value.get("dictionary_value_id") is not None:
                    attribute_input["dictionary_value_id"] = variant_value["dictionary_value_id"]
                    attributes.append(_dictionary_attribute(attribute_input))
                else:
                    attributes.append(_text_attribute(attribute_input))
        if final_attributes:
            existing_ids = {item["id"] for item in attributes}
            for final_attribute in final_attributes["attributes"]:
                converted = _final_attribute_to_api(final_attribute)
                if converted and converted["id"] not in existing_ids:
                    attributes.append(converted)
                    existing_ids.add(converted["id"])
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
            "depth": dimensions["length_mm"],
            "width": dimensions["width_mm"],
            "height": dimensions["height_mm"],
            "dimension_unit": "mm",
            "weight": ozon_weight_grams(config["package_weight"]["value_g"]),
            "weight_unit": "g",
            "images": offer_image_urls,
            "primary_image": offer_image_urls[0],
            "images360": [],
            "complex_attributes": [],
            "color_image": (
                variant_main
                if variant_main and "color" in (
                    (variant_grouping or {}).get("mapping_requirements", {}).get("difference_types", [])
                )
                else (color_image_urls or {}).get(sku_id, "")
            ),
        }
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
    model_attribute_id = int(upload_config["model_name"]["attribute_id"])
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
            "model_value": attributes.get(model_attribute_id),
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
    preflight = build_preflight(
        product_dir, draft, status, config, metadata, manifest, checked_at
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
    status["status"] = "FAILED_HARD_BLOCKER"
    status["current_step"] = step
    status["history"].append({"from": previous, "to": "FAILED_HARD_BLOCKER", "at": at, "reason": str(error)})
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
    # A returned task id is the terminal local handoff.  The workbench never
    # performs a second import or post-upload readback for the same submission;
    # later changes belong in the Ozon product-card backend.
    idempotency_path = output / "ozon-idempotency.json"
    idempotency = load_json(idempotency_path) if idempotency_path.is_file() else {}
    if idempotency.get("api_write_completed") is True and idempotency.get("task_id"):
        raise UploadGateError(
            "The previous Ozon import task is still pending or already handed off; "
            "a second write is forbidden."
        )
    # Never trust a historical final-upload-check PASS.  Rebuild the image
    # gate from the current image-plan and filesystem before any remote call,
    # especially before CREATE/UPDATE can be reached.
    current_check = refresh_current_image_check(product_dir)
    if current_check.get("status") != "PASS" or not current_check.get("upload_allowed"):
        raise UploadGateError("当前图片完整性检查失败：" + "；".join(current_check.get("errors") or ["图片未准备完成"]))
    # Input provenance is an independent hard gate.  It deliberately runs
    # after rebuilding the local image report so a stale PASS can never remain
    # visible, but still before the first Ozon read or write call.
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
    if idempotency.get("api_write_completed") is True and idempotency.get("task_id"):
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
            resolved_final_attributes = _resolve_rich_content_for_upload(
                output, final_attributes, manifest
            )
            checked_at = now()
            preflight = build_preflight(
                product_dir, draft, status, config, metadata, manifest, checked_at
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
                "status": "HANDED_OFF_TO_OZON",
                "current_step": "ozon_upload",
                "progress": 100,
                "next_action": "complete",
            })
            status.setdefault("history", []).append({
                "from": previous,
                "to": "HANDED_OFF_TO_OZON",
                "at": now(),
                "reason": "Ozon accepted the import task; local processing is handed off to the Ozon product card.",
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
            "status": "FAILED_HARD_BLOCKER", "current_step": "ozon_status",
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
        items = build_import_items(
            draft,
            config,
            image_urls,
            variant_grouping=load_json(output / "variant-grouping-result.json"),
            variant_main_image_urls=variant_main_image_urls,
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
            "raw_response": response,
        })
        write_json_atomic(output / "ozon-idempotency.json", {
            "schema_version": "1.0.0", "payload_hash": payload_hash,
            "task_id": task_id, "api_write_completed": True,
            "offer_ids": [item["offer_id"] for item in items], "request_timestamp": now(),
        })
        status["api_write_count"] = int(status.get("api_write_count") or 0) + 1
        status.update({"status": "HANDED_OFF_TO_OZON", "current_step": "ozon_upload", "next_action": "complete"})
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
