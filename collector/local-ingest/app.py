from __future__ import annotations

import base64
import csv
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import zipfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
PRODUCTS_DIR = ROOT / "products"
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "pricing-engine"))
from pipeline_runtime import batch_path, collected_products, create_batch, retryable_products  # noqa: E402
from image_cache_cleanup import cleanup_images  # noqa: E402
from image_source_preflight import source_image_candidates  # noqa: E402
from product_deletion import deletion_marker_path, purge_local_product  # noqa: E402
from store_publications import (  # noqa: E402
    final_snapshot,
    load_publications,
    publication_summary,
    save_publications,
    select_stores,
)
from workbench_stores import (  # noqa: E402
    delete_store,
    list_stores,
    load_registry,
    set_enabled,
    upsert_store,
    validate_store_read_only,
)
from workbench_learning import (  # noqa: E402
    materialize_active_experience,
    record_image_feedback,
    record_workbench_edits,
)
from workbench_operators import (  # noqa: E402
    DEFAULT_OPERATOR_ID,
    authenticate as authenticate_operator,
    default_owner as default_operator,
    delete_operator,
    list_operators,
    upsert_operator,
)
from multi_store_upload import definitely_retryable, refresh_pending_stores  # noqa: E402
from collector_categories import (  # noqa: E402
    build_selection,
    category_tree_children,
    get_category,
    load_translated_tree_cache,
    prepare_rules,
    public_preferences,
    recommend_categories,
    search_categories,
    set_favorite,
)
from pricing_engine.dimension_estimator import (  # noqa: E402
    estimate_package_dimensions,
    estimate_product_dimensions,
    fit_estimated_product_dimensions_to_confirmed_package,
)
from pricing_engine.weight_estimator import (  # noqa: E402
    estimate_package_weight,
    estimate_product_weight,
    fit_estimated_product_weight_to_confirmed_package,
)

SCHEMA_VERSION = "1.0.0"
MAX_SELECTED_SKUS_PER_PRODUCT = 10
ID_LOCK = threading.Lock()
BATCH_PID_PATH = ROOT / "logs/batch-runner.pid"
BATCH_LOG_PATH = ROOT / "logs/batch-runner.log"
CURRENT_BATCH_PATH = ROOT / "logs/current-batch.json"
WORKBENCH_RUN_QUEUE_PATH = ROOT / "logs/workbench-run-queue.json"
SAFE_STOP_REQUEST_PATH = ROOT / "logs/safe-stop-request.json"
WORKBENCH_SETTINGS_PATH = ROOT / "config/workbench-settings.json"
ATTRIBUTE_TRANSLATIONS_PATH = ROOT / "config/ozon-attribute-translations-zh.json"
LAN_ACCESS_CONFIG_PATH = ROOT / "config/lan-access.json"
PRICING_RULES_PATH = ROOT / "pricing-engine/pricing_rules.json"
IMAGE_CLEANUP_THREAD_LOCK = threading.Lock()
BATCH_QUEUE_LOCK = threading.RLock()
BATCH_DISPATCHER_LOCK = threading.Lock()
BATCH_DISPATCHER_WAKE = threading.Event()
BATCH_DISPATCHER_STARTED = False
DEFAULT_LAN_CIDRS = (
    "127.0.0.0/8", "::1/128", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12",
)
DEFAULT_WORKBENCH_SETTINGS = {
    "schema_version": "1.0.0",
    "auto_mode_enabled": False,
    "default_review_mode": "manual",
    "learning_threshold": 2,
    "fixed_cny_to_rub": 12.0,
    "rub_rounding": 10,
}

CURRENT_OPERATOR: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_workbench_operator", default=None)


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    ensure_batch_dispatcher()
    yield


app = FastAPI(title="crossborder-ai-factory local ingest", version="0.2.0", lifespan=app_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"]
)


def trigger_image_cleanup() -> None:
    """Run due cleanup in the background so opening the inbox never waits."""
    if not IMAGE_CLEANUP_THREAD_LOCK.acquire(blocking=False):
        return

    def worker() -> None:
        try:
            settings = json.loads(
                (ROOT / "config/pipeline-settings.json").read_text(encoding="utf-8")
            )
            cleanup_images(ROOT, settings)
        finally:
            IMAGE_CLEANUP_THREAD_LOCK.release()

    threading.Thread(target=worker, name="image-cleanup", daemon=True).start()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_schema(name: str) -> Dict[str, Any]:
    with (TEMPLATES_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_json(instance: Dict[str, Any], schema_name: str) -> List[str]:
    validator = Draft202012Validator(load_schema(schema_name))
    errors = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path)):
        path = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{path}: {error.message}")
    return errors


def apply_shared_price_tier(payload: Dict[str, Any]) -> Optional[float]:
    """Apply a verified product tier only when every final SKU lacks a direct price."""
    skus = payload.get("skus") or []
    if not skus or any(isinstance(item.get("purchase_price"), (int, float)) for item in skus):
        return None
    quantity = (payload.get("minimum_order_quantity") or {}).get("value")
    if not isinstance(quantity, (int, float)) or quantity <= 0:
        return None
    price_info = payload.get("price_information") or {}
    texts = [str(price_info.get("raw_text") or "")]
    texts.extend(str(item.get("raw_text") or "") for item in price_info.get("price_ranges") or [])
    text = " ".join(texts).replace(",", "")
    unit = r"(?:件|个|只|套|箱|条|包)"
    tiers: List[Tuple[float, int, Optional[int]]] = []
    for match in re.finditer(rf"(?:¥|￥)\s*([0-9]+(?:\.[0-9]{{1,2}})?)\s*(\d+)\s*{unit}\s*起批", text):
        tiers.append((float(match.group(1)), int(match.group(2)), None))
    for match in re.finditer(rf"(?:¥|￥)\s*([0-9]+(?:\.[0-9]{{1,2}})?)\s*(\d+)\s*[-–—]\s*(\d+)\s*{unit}", text):
        tiers.append((float(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in re.finditer(rf"(?:¥|￥)\s*([0-9]+(?:\.[0-9]{{1,2}})?)\s*[≥>=]\s*(\d+)\s*{unit}", text):
        tiers.append((float(match.group(1)), int(match.group(2)), None))
    applicable = sorted(
        (tier for tier in tiers if quantity >= tier[1] and (tier[2] is None or quantity <= tier[2])),
        key=lambda tier: tier[1], reverse=True,
    )
    if not applicable:
        return None
    price = applicable[0][0]

    def apply(items: List[Dict[str, Any]]) -> None:
        if items and not any(isinstance(item.get("purchase_price"), (int, float)) for item in items):
            for item in items:
                item["purchase_price"] = price
                item["price"] = price
                item["price_source"] = "price_range"
                item.setdefault("source_data", {})["inherited_price_range"] = {
                    "price_cny": price,
                    "minimum_order_quantity": quantity,
                    "reason": "Product tier matching the captured minimum order quantity applies to all final SKUs.",
                }

    apply(skus)
    apply((payload.get("raw_snapshot") or {}).get("all_raw_skus") or [])
    payload["capture_warnings"] = [
        str(item) for item in payload.get("capture_warnings") or []
        if "SKU missing sku-specific price" not in str(item)
    ]
    payload["capture_warnings"].append(
        f"skus: inherited shared product price tier CNY {price:.2f} for MOQ {quantity:g}; source=price_range"
    )
    return price


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)


def load_lan_access_config() -> Dict[str, Any]:
    path = ROOT / "config/lan-access.json"
    if not path.is_file():
        return {"enabled": False, "access_code": "", "allowed_cidrs": list(DEFAULT_LAN_CIDRS)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"enabled": False, "access_code": "", "allowed_cidrs": list(DEFAULT_LAN_CIDRS)}
    return {
        "enabled": bool(data.get("enabled", False)),
        "access_code": str(data.get("access_code") or "").strip(),
        "allowed_cidrs": [str(value) for value in data.get("allowed_cidrs") or DEFAULT_LAN_CIDRS],
    }


def client_ip_allowed(client_host: str, config: Dict[str, Any]) -> bool:
    host = str(client_host or "").split("%", 1)[0]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "testclient"}
    for cidr in config.get("allowed_cidrs") or DEFAULT_LAN_CIDRS:
        try:
            if address in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def is_loopback_client(client_host: str) -> bool:
    host = str(client_host or "").split("%", 1)[0]
    if host in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def append_log(product_dir: Path, event: str, payload: Dict[str, Any]) -> None:
    logs_dir = product_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    record = {"at": now_iso(), "event": event, "payload": payload}
    with (logs_dir / "collector.log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def safe_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def unknown_text(value: Any) -> str:
    return safe_text(value) or "unknown"


def is_generated_sku_id(value: Any) -> bool:
    text = safe_text(value) or ""
    return bool(re.match(r"^(script-sku|dom-sku|dom-combo|combo-sku)-", text, re.IGNORECASE))


def has_acceptable_sku_id(value: Any) -> bool:
    text = safe_text(value)
    return bool(text and text != "unknown" and not is_generated_sku_id(text))


def normalize_url(url: Any, base_url: str) -> Optional[str]:
    text = safe_text(url)
    if not text or text == "unknown":
        return None
    if text.startswith("//"):
        return "https:" + text
    return urllib.parse.urljoin(base_url, text)


def is_disallowed_image_url(url: str) -> bool:
    lowered = url.lower()
    return bool(
        re.search(r"\.svg(?:$|[?#])", lowered)
        or re.search(r"(icon|logo|avatar|sprite|pay|payment|wangwang|qrcode|qr|loading|blank|grey)", lowered)
        or re.search(r"(?:^|[-_/])tps-\d{1,3}-\d{1,3}(?:[-_.]|$)", lowered)
    )


def url_to_extension(url: str, content_type: Optional[str]) -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
            return guessed
    return ".jpg"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_product_id() -> str:
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    with ID_LOCK:
        max_id = 0
        for path in PRODUCTS_DIR.glob("P[0-9][0-9][0-9][0-9][0-9][0-9]"):
            try:
                max_id = max(max_id, int(path.name[1:]))
            except ValueError:
                continue
        for number in range(max_id + 1, max_id + 1000000):
            product_id = f"P{number:06d}"
            if deletion_marker_path(PRODUCTS_DIR.parent, product_id).is_file():
                continue
            product_dir = PRODUCTS_DIR / product_id
            try:
                product_dir.mkdir(parents=True, exist_ok=False)
                return product_id
            except FileExistsError:
                continue
    raise RuntimeError("Unable to allocate product_id")


def create_product_dirs(product_dir: Path) -> None:
    for rel in [
        "input/main-images",
        "input/sku-images",
        "input/detail-images",
        "output",
        "output/images",
        "output/images/main",
        "output/images/detail",
        "logs"
    ]:
        (product_dir / rel).mkdir(parents=True, exist_ok=True)


def find_existing_source_urls() -> Dict[str, str]:
    existing: Dict[str, str] = {}
    for source_path in PRODUCTS_DIR.glob("P*/input/source.json"):
        if not product_is_owned(source_path.parents[1]):
            continue
        try:
            data = json.loads(source_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        source_url = data.get("source_url")
        product_id = data.get("product_id") or source_path.parents[1].name
        if source_url and source_url != "unknown":
            existing.setdefault(source_url, product_id)
    return existing


def make_status(
    product_id: str,
    status: str,
    current_step: str,
    progress: int,
    started_at: str,
    completed_at: Optional[str],
    warnings: List[str],
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    retry_count: int = 0
) -> Dict[str, Any]:
    step_status = "completed" if status == "COLLECTED" else "failed" if status == "FAILED_HARD_BLOCKER" else "in_progress"
    step_error = None
    if error_message:
        step_error = {
            "step": current_step,
            "reason": error_message,
            "occurred_at": completed_at or now_iso(),
            "retryable": True
        }
    history = [
        {
            "from": None,
            "to": "COLLECTING",
            "at": started_at,
            "reason": "Collector ingest started."
        }
    ]
    if status != "COLLECTING":
        history.append(
            {
                "from": "COLLECTING",
                "to": status,
                "at": completed_at or now_iso(),
                "reason": error_message or "Collector ingest finished."
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "status": status,
        "current_step": current_step,
        "progress": progress,
        "started_at": started_at,
        "completed_at": completed_at or "unknown",
        "error_code": error_code or "unknown",
        "error_message": error_message or "unknown",
        "warnings": warnings,
        "retry_count": retry_count,
        "task_authorized": False,
        "batch_id": "unknown",
        "completed_steps": ["collect_source"] if status == "COLLECTED" else [],
        "pending_steps": [
            "validate_source", "product_analysis", "category_match", "variant_rules",
            "russian_copy", "measurements", "pricing", "marketplace_content",
            "image_generation", "image_qc", "rich_content", "final_upload_check",
            "ozon_upload", "ozon_status"
        ] if status == "COLLECTED" else [],
        "failed_step": "unknown",
        "retry_count_by_step": {},
        "api_write_count": 0,
        "last_run_at": "unknown",
        "next_action": "wait_for_run_task" if status == "COLLECTED" else current_step,
        "ozon": {
            "upload_status": "not_started",
            "product_id": "unknown",
            "offer_id": "unknown",
            "task_id": "unknown",
            "last_response": None,
            "errors": []
        },
        "steps": [
            {
                "name": current_step,
                "status": step_status,
                "started_at": started_at,
                "finished_at": completed_at or "unknown",
                "retry_count": retry_count,
                "retryable": True,
                "error": step_error
            }
        ],
        "history": history
    }


def build_field_diagnostic(field: str, strategy: str, hit: bool, failure_reason: Optional[str], candidate_count: int) -> Dict[str, Any]:
    return {
        "field": field,
        "strategy": strategy,
        "hit": hit,
        "failure_reason": failure_reason or "unknown",
        "candidate_count": candidate_count
    }


def coerce_attribute(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name_cn": unknown_text(item.get("name_cn") or item.get("name")),
        "value_cn": unknown_text(item.get("value_cn") or item.get("value")),
        "source": item.get("source") if item.get("source") in {
            "structured_json",
            "script_init_data",
            "dom_semantic",
            "candidate_selector",
            "text_inference",
            "unknown"
        } else "unknown",
        "source_text": unknown_text(item.get("source_text") or item.get("raw_text"))
    }


def coerce_price_information(payload: Dict[str, Any]) -> Dict[str, Any]:
    price_info = payload.get("price_information") or {}
    ranges = []
    for item in price_info.get("price_ranges") or []:
        ranges.append(
            {
                "min_quantity": item.get("min_quantity"),
                "price_cny": item.get("price_cny"),
                "raw_text": unknown_text(item.get("raw_text"))
            }
        )
    return {
        "currency": price_info.get("currency") if price_info.get("currency") == "CNY" else "unknown",
        "price_ranges": ranges,
        "raw_text": unknown_text(price_info.get("raw_text"))
    }


def coerce_minimum_order(payload: Dict[str, Any]) -> Dict[str, Any]:
    moq = payload.get("minimum_order_quantity") or {}
    return {
        "value": moq.get("value") if isinstance(moq.get("value"), int) else None,
        "unit": unknown_text(moq.get("unit")),
        "raw_text": unknown_text(moq.get("raw_text"))
    }


def collect_image_inputs(payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    source_url = payload.get("source_url") or ""
    main_inputs = []
    detail_inputs = []
    sku_by_url: Dict[str, Dict[str, Any]] = {}

    for index, item in enumerate(payload.get("main_images") or []):
        url = normalize_url(item.get("url") or item.get("original_url"), source_url)
        if url and not is_disallowed_image_url(url):
            main_inputs.append({"url": url, "source": "main_gallery", "source_order": index})

    for index, item in enumerate(payload.get("detail_images") or []):
        url = normalize_url(item.get("url") or item.get("original_url"), source_url)
        if url and not is_disallowed_image_url(url):
            detail_inputs.append({"url": url, "source": "detail_area", "source_order": index})

    for sku_index, sku in enumerate(payload.get("skus") or []):
        url = normalize_url(sku.get("image_url"), source_url)
        if url and not is_disallowed_image_url(url):
            sku_by_url.setdefault(url, {"url": url, "source": "sku_option", "source_order": sku_index})

    for group_index, group in enumerate(payload.get("sku_property_groups") or []):
        for value_index, value in enumerate(group.get("values") or []):
            url = normalize_url(value.get("image_url"), source_url)
            if url and not is_disallowed_image_url(url):
                sku_by_url.setdefault(url, {"url": url, "source": "sku_property_value", "source_order": group_index * 100 + value_index})

    return main_inputs, detail_inputs, sku_by_url


def download_url(url: str, timeout: int = 20) -> Tuple[bytes, Optional[str]]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 crossborder-ai-factory-collector/0.2",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
        }
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type")
        return response.read(), content_type


def download_image_group(
    image_inputs: Iterable[Dict[str, Any]],
    output_dir: Path,
    prefix: str,
    url_cache: Dict[str, Dict[str, Any]],
    hash_cache: Dict[str, Dict[str, Any]],
    warnings: List[str]
) -> List[Dict[str, Any]]:
    results = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(image_inputs):
        url = item["url"]
        image_id = f"{prefix}-{index + 1:03d}"
        if url in url_cache:
            cached = dict(url_cache[url])
            cached.update({"id": image_id, "source": item["source"], "source_order": item["source_order"], "download_status": "skipped_duplicate_url"})
            results.append(cached)
            continue
        try:
            last_error: Exception | None = None
            for candidate_url in source_image_candidates(url):
                try:
                    content, content_type = download_url(candidate_url)
                    break
                except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                    last_error = exc
            else:
                raise last_error or OSError("no downloadable image candidate")
            digest = sha256_bytes(content)
            if digest in hash_cache:
                duplicate = dict(hash_cache[digest])
                result = {
                    "id": image_id,
                    "original_url": url,
                    "local_path": duplicate["local_path"],
                    "source": item["source"],
                    "source_order": item["source_order"],
                    "download_status": "skipped_duplicate_content",
                    "sha256": digest,
                    "content_duplicate_of": duplicate["id"],
                    "error": "unknown"
                }
            else:
                extension = url_to_extension(url, content_type)
                local_path = output_dir / f"{image_id}{extension}"
                local_path.write_bytes(content)
                result = {
                    "id": image_id,
                    "original_url": url,
                    "local_path": str(local_path.relative_to(ROOT)),
                    "source": item["source"],
                    "source_order": item["source_order"],
                    "download_status": "downloaded",
                    "sha256": digest,
                    "content_duplicate_of": "unknown",
                    "error": "unknown"
                }
                hash_cache[digest] = result
            url_cache[url] = result
            results.append(result)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            warning = f"Image download failed for {url}: {exc}"
            warnings.append(warning)
            result = {
                "id": image_id,
                "original_url": url,
                "local_path": "unknown",
                "source": item["source"],
                "source_order": item["source_order"],
                "download_status": "failed",
                "sha256": "unknown",
                "content_duplicate_of": "unknown",
                "error": str(exc)
            }
            url_cache[url] = result
            results.append(result)
    return results


def sku_local_path(image_url: Optional[str], image_lookup: Dict[str, Dict[str, Any]]) -> str:
    if not image_url:
        return "unknown"
    item = image_lookup.get(image_url)
    if not item:
        return "unknown"
    return item.get("local_path") or "unknown"


def build_source_json(
    product_id: str,
    payload: Dict[str, Any],
    main_images: List[Dict[str, Any]],
    detail_images: List[Dict[str, Any]],
    sku_images_by_url: Dict[str, Dict[str, Any]],
    warnings: List[str]
) -> Dict[str, Any]:
    source_url = payload["source_url"]
    skus = []
    for index, sku in enumerate(payload.get("skus") or []):
        image_url = normalize_url(sku.get("image_url"), source_url)
        option_values = [coerce_attribute(item) for item in (sku.get("option_values") or []) if isinstance(item, dict)]
        purchase_price = sku.get("purchase_price") if isinstance(sku.get("purchase_price"), (int, float)) else None
        price_source = sku.get("price_source") if sku.get("price_source") in {"sku_specific_price", "price_range", "unknown"} else ("sku_specific_price" if purchase_price is not None else "unknown")
        skus.append(
            {
                "sku_id": unknown_text(sku.get("sku_id") or f"sku-{index + 1}"),
                "sku_name": unknown_text(sku.get("sku_name")),
                "option_values": option_values,
                "price": purchase_price,
                "purchase_price": purchase_price,
                "price_source": price_source,
                "image_url": image_url or "unknown",
                "local_image_path": sku_local_path(image_url, sku_images_by_url),
                "variant_image_url": image_url or "unknown",
                "variant_local_image_path": sku_local_path(image_url, sku_images_by_url),
                "variant_image_source": unknown_text((sku.get("source_data") or {}).get("sku_image_source")),
                "variant_image_prop_id": unknown_text((sku.get("source_data") or {}).get("sku_image_prop_id")),
                "variant_image_value_id": unknown_text((sku.get("source_data") or {}).get("sku_image_prop_value_id")),
                "variant_image_value_name": unknown_text((sku.get("source_data") or {}).get("sku_image_prop_value")),
                "sku_image_missing": bool(sku.get("sku_image_missing")) or image_url is None,
                "availability": sku.get("availability") if sku.get("availability") in {"in_stock", "out_of_stock", "unknown"} else "unknown",
                "selection_order": sku.get("selection_order") if isinstance(sku.get("selection_order"), int) else index + 1,
                "source_data": sku.get("source_data") if isinstance(sku.get("source_data"), dict) else sku
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "source_platform": "1688",
        "source_url": source_url,
        "captured_at": payload["captured_at"],
        "title_cn": unknown_text(payload.get("title_cn")),
        "supplier_name": unknown_text(payload.get("supplier_name")),
        "product_attributes": [coerce_attribute(item) for item in (payload.get("product_attributes") or []) if isinstance(item, dict)],
        "price_information": coerce_price_information(payload),
        "minimum_order_quantity": coerce_minimum_order(payload),
        "main_images": main_images,
        "detail_images": detail_images,
        "sku_property_groups": payload.get("sku_property_groups") or [],
        "skus": skus,
        "raw_capture_file": f"products/{product_id}/input/raw-snapshot.json",
        "capture_warnings": warnings,
        "field_diagnostics": payload.get("field_diagnostics") or [
            build_field_diagnostic("payload", "local_ingest", True, None, 1)
        ]
    }


def build_raw_snapshot(
    payload: Dict[str, Any],
    product_id: str,
    warnings: List[str],
    duplicate_of: Optional[str]
) -> Dict[str, Any]:
    raw_snapshot = payload.get("raw_snapshot") if isinstance(payload.get("raw_snapshot"), dict) else {}
    all_raw_skus = raw_snapshot.get("all_raw_skus") if isinstance(raw_snapshot.get("all_raw_skus"), list) else payload.get("skus") or []
    selected_sku_ids = payload.get("selected_sku_ids") or [sku.get("sku_id") for sku in payload.get("skus") or [] if sku.get("sku_id")]
    available_sku_count = sum(1 for sku in all_raw_skus if sku.get("availability") != "out_of_stock") if isinstance(all_raw_skus, list) else 0
    real_sku_id_count = sum(1 for sku in all_raw_skus if isinstance(sku, dict) and has_acceptable_sku_id(sku.get("sku_id"))) if isinstance(all_raw_skus, list) else 0
    selected_real_sku_id_count = sum(1 for sku in payload.get("skus") or [] if isinstance(sku, dict) and has_acceptable_sku_id(sku.get("sku_id")))
    sku_debug = raw_snapshot.get("sku_debug") if isinstance(raw_snapshot.get("sku_debug"), dict) else {}
    if isinstance(all_raw_skus, list):
        sku_debug = {
            **sku_debug,
            "total_skus": len(all_raw_skus),
            "sku_with_real_id": real_sku_id_count,
            "sku_with_image": sum(1 for sku in all_raw_skus if isinstance(sku, dict) and normalize_url(sku.get("image_url"), payload["source_url"]) and sku.get("sku_image_missing") is not True),
            "sku_with_price": sum(1 for sku in all_raw_skus if isinstance(sku, dict) and isinstance(sku.get("purchase_price"), (int, float))),
            "missing_image_skus": [
                sku.get("sku_id") or sku.get("sku_name") or "unknown"
                for sku in all_raw_skus
                if isinstance(sku, dict) and (not normalize_url(sku.get("image_url"), payload["source_url"]) or sku.get("sku_image_missing") is True)
            ],
            "missing_price_skus": [
                sku.get("sku_id") or sku.get("sku_name") or "unknown"
                for sku in all_raw_skus
                if isinstance(sku, dict) and not isinstance(sku.get("purchase_price"), (int, float))
            ]
        }
    selected_at = (payload.get("sku_selection") or {}).get("selected_at") or raw_snapshot.get("sku_selection_time") or "unknown"
    original_image_urls = []
    for key in ("main_images", "detail_images"):
        for item in payload.get(key) or []:
            url = normalize_url(item.get("url") or item.get("original_url"), payload["source_url"])
            if url:
                original_image_urls.append({"role": key, "url": url})
    for sku in payload.get("skus") or []:
        url = normalize_url(sku.get("image_url"), payload["source_url"])
        if url:
            original_image_urls.append({"role": "sku_images", "url": url})
    return {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "page_url": payload["source_url"],
        "page_title": payload.get("page_title") or "unknown",
        "structured_data_summary": raw_snapshot.get("structured_data_summary", {}),
        "candidate_selectors": raw_snapshot.get("candidate_selectors", {}),
        "field_diagnostics": payload.get("field_diagnostics") or [],
        "original_image_urls": original_image_urls,
        "sku_raw_data": all_raw_skus,
        "sku_debug": sku_debug,
        "sku_property_image_debug": raw_snapshot.get("sku_property_image_debug") or {},
        "sku_selection": {
            "original_sku_count": len(all_raw_skus),
            "available_sku_count": available_sku_count,
            "selected_sku_count": len(payload.get("skus") or []),
            "unselected_sku_count": max(len(all_raw_skus) - len(payload.get("skus") or []), 0),
            "selected_sku_ids": selected_sku_ids,
            "selected_sku_keys": payload.get("selected_sku_keys") or (payload.get("sku_selection") or {}).get("selected_sku_keys") or [],
            "real_sku_id_count": real_sku_id_count,
            "missing_real_sku_id_count": max(len(all_raw_skus) - real_sku_id_count, 0),
            "selected_real_sku_id_count": selected_real_sku_id_count,
            "selected_missing_real_sku_id_count": max(len(payload.get("skus") or []) - selected_real_sku_id_count, 0),
            "selected_at": selected_at
        },
        "capture_warnings": warnings,
        "plugin_version": payload.get("plugin_version") or "unknown",
        "captured_at": payload["captured_at"],
        "duplicate_strategy": {
            "strategy": "create_new_capture_version",
            "duplicate_of": duplicate_of or "unknown"
        },
        "raw_snapshot": raw_snapshot
    }


def ingest_capture(payload: Dict[str, Any], operator: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    schema_errors = validate_json(payload, "collector-capture.schema.json")
    if schema_errors:
        raise HTTPException(status_code=422, detail={"message": "Invalid collector payload", "errors": schema_errors})
    apply_shared_price_tier(payload)

    if "1688." not in urllib.parse.urlparse(payload["source_url"]).netloc:
        raise HTTPException(status_code=422, detail={"message": "Current page is not a supported 1688 product URL"})
    selected_skus = payload.get("skus") or []
    if not selected_skus:
        raise HTTPException(status_code=422, detail={"message": "请至少选择1个SKU。"})
    if len(selected_skus) > MAX_SELECTED_SKUS_PER_PRODUCT:
        raise HTTPException(
            status_code=422,
            detail={"message": f"单个商品最多选择{MAX_SELECTED_SKUS_PER_PRODUCT}个SKU，请先取消其他SKU。"},
        )
    if any(sku.get("availability") == "out_of_stock" for sku in selected_skus if isinstance(sku, dict)):
        raise HTTPException(status_code=422, detail={"message": "所选SKU包含不可购买或无库存SKU。"})
    missing_real_sku_ids = [
        sku.get("sku_name") or sku.get("sku_id") or f"sku-{index + 1}"
        for index, sku in enumerate(selected_skus)
        if isinstance(sku, dict) and not has_acceptable_sku_id(sku.get("sku_id"))
    ]
    if missing_real_sku_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "所选SKU缺少真实1688 sku_id，禁止保存伪造SKU ID。",
                "missing_count": len(missing_real_sku_ids),
                "examples": missing_real_sku_ids[:5]
            }
        )

    try:
        category_selection = build_selection(ROOT, payload, preferences_root=PRODUCTS_DIR.parent)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc
    selection_errors = validate_json(category_selection, "category-selection.schema.json")
    if selection_errors:
        raise HTTPException(status_code=422, detail={"message": "类目选择数据无效", "errors": selection_errors})

    started_at = now_iso()
    warnings = [str(item) for item in payload.get("capture_warnings") or []]
    existing_urls = find_existing_source_urls()
    duplicate_of = existing_urls.get(payload["source_url"])
    if duplicate_of:
        if payload.get("allow_new_version") is not True:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "该产品已经采集过。",
                    "existing_product_id": duplicate_of,
                    "source_url": payload["source_url"],
                    "options": ["open_existing", "create_new_version"]
                }
            )
        warnings.append(f"Duplicate source_url already captured as {duplicate_of}; user chose to create a new capture version.")

    product_id = create_product_id()
    product_dir = PRODUCTS_DIR / product_id
    create_product_dirs(product_dir)
    save_product_owner(product_dir, operator)
    status_collecting = make_status(product_id, "COLLECTING", "collect_source", 10, started_at, None, warnings)
    atomic_write_json(product_dir / "status.json", status_collecting)
    append_log(product_dir, "ingest_started", {"source_url": payload["source_url"], "duplicate_of": duplicate_of})

    try:
        main_inputs, detail_inputs, sku_inputs_by_url = collect_image_inputs(payload)
        url_cache: Dict[str, Dict[str, Any]] = {}
        hash_cache: Dict[str, Dict[str, Any]] = {}
        main_images = download_image_group(main_inputs, product_dir / "input/main-images", "main", url_cache, hash_cache, warnings)
        sku_images = download_image_group(sku_inputs_by_url.values(), product_dir / "input/sku-images", "sku", url_cache, hash_cache, warnings)
        detail_images = download_image_group(detail_inputs, product_dir / "input/detail-images", "detail", url_cache, hash_cache, warnings)
        sku_images_by_url = {item["original_url"]: item for item in sku_images}

        source_json = build_source_json(product_id, payload, main_images, detail_images, sku_images_by_url, warnings)
        raw_snapshot = build_raw_snapshot(payload, product_id, warnings, duplicate_of)

        source_errors = validate_json(source_json, "source.schema.json")
        if source_errors:
            raise ValueError("Generated source.json failed schema validation: " + "; ".join(source_errors))

        atomic_write_json(product_dir / "input/raw-snapshot.json", raw_snapshot)
        atomic_write_json(product_dir / "input/source.json", source_json)
        atomic_write_json(product_dir / "input/category-selection.json", category_selection)

        completed_at = now_iso()
        status_collected = make_status(product_id, "COLLECTED", "collect_source", 100, started_at, completed_at, warnings)
        status_errors = validate_json(status_collected, "status.schema.json")
        if status_errors:
            raise ValueError("Generated status.json failed schema validation: " + "; ".join(status_errors))
        atomic_write_json(product_dir / "status.json", status_collected)
        append_log(
            product_dir,
            "ingest_completed",
            {
                "main_images": len(main_images),
                "sku_images": len(sku_images),
                "detail_images": len(detail_images),
                "warnings": warnings
                ,"category_id": category_selection["category_id"]
                ,"type_id": category_selection["type_id"]
                ,"rules_snapshot_hash": category_selection["rules_snapshot_hash"]
            }
        )
        return {
            "product_id": product_id,
            "status": "COLLECTED",
            "source_path": f"products/{product_id}/input/source.json",
            "status_path": f"products/{product_id}/status.json",
            "duplicate_of": duplicate_of,
            "warnings": warnings,
            "ozon_category": {
                "category_id": category_selection["category_id"],
                "type_id": category_selection["type_id"],
                "category_path": category_selection["category_path"],
                "rules_snapshot_hash": category_selection["rules_snapshot_hash"],
            },
            "counts": {
                "attributes": len(source_json["product_attributes"]),
                "skus": len(source_json["skus"]),
                "main_images": len(main_images),
                "sku_images": len(sku_images),
                "detail_images": len(detail_images)
            }
        }
    except Exception as exc:
        completed_at = now_iso()
        error_message = str(exc)
        warnings.append(error_message)
        status_failed = make_status(
            product_id,
            "FAILED_HARD_BLOCKER",
            "collect_source",
            100,
            started_at,
            completed_at,
            warnings,
            error_code="COLLECTOR_INGEST_FAILED",
            error_message=error_message
        )
        atomic_write_json(product_dir / "status.json", status_failed)
        append_log(product_dir, "ingest_failed", {"error": error_message})
        raise HTTPException(status_code=500, detail={"message": error_message, "product_id": product_id})


def replace_collected_category(product_dir: Path, selection: Dict[str, Any]) -> Dict[str, Any]:
    status_path = product_dir / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    previous_state = str(status.get("status") or "unknown")
    pending_states = {"QUEUED", "PROCESSING", "UPLOADING", "UPLOADED", "PENDING_REMOTE", "OZON_MODERATION", "ACTIVE"}
    if previous_state in pending_states or int(status.get("api_write_count") or 0) > 0:
        raise ValueError("商品已进入批次、远端处理中或已有Ozon写入记录，禁止直接修改类目")
    previous_path = product_dir / "input/category-selection.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else {}
    if (
        previous.get("category_id") == selection.get("category_id")
        and previous.get("type_id") == selection.get("type_id")
        and previous.get("rules_snapshot_hash") == selection.get("rules_snapshot_hash")
    ):
        return {"status": "unchanged", "product_id": product_dir.name, "invalidated": []}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    invalidated_root = product_dir / "logs/category-invalidations" / stamp
    affected = [
        "attributes.json", "ozon-category.json", "ozon-category-tree.json",
        "ozon-category-attributes.json", "ozon-attributes.json", "ozon-preflight.json",
        "variant-decision.json", "variant-grouping-result.json", "platform-grouping-result.json",
        "category-variant-rule-audit.json", "style-profile.json", "image-plan.json",
        "image-qc-report.json", "ozon-draft.json", "ozon-upload-config.json",
        "ozon-upload-payload.json", "ozon-upload-preflight.json", "final-submission-snapshot.json",
        "rich-content.json", "generated-images", "images",
    ]
    invalidated = []
    for name in affected:
        source = product_dir / "output" / name
        if not source.exists():
            continue
        destination = invalidated_root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        invalidated.append(f"output/{name}")
    if previous:
        atomic_write_json(invalidated_root / "previous-category-selection.json", previous)
    atomic_write_json(previous_path, selection)
    status.update({
        "status": "COLLECTED", "current_step": "collect_source", "progress": 100,
        "task_authorized": False, "batch_id": "unknown", "completed_steps": ["collect_source"],
        "failed_step": "unknown", "error_code": "unknown", "error_message": "unknown",
        "next_action": "wait_for_run_task", "api_write_count": 0,
    })
    status["pending_steps"] = [
        "validate_source", "product_analysis", "category_match", "variant_rules",
        "russian_copy", "measurements", "pricing", "marketplace_content",
        "image_generation", "image_qc", "rich_content", "final_upload_check",
        "ozon_upload", "ozon_status",
    ]
    status.setdefault("history", []).append({
        "from": previous_state, "to": "COLLECTED", "at": now_iso(),
        "reason": "User changed the final Ozon category; old attributes, image strategy and payloads were invalidated.",
    })
    atomic_write_json(status_path, status)
    append_log(product_dir, "collector_category_changed", {
        "previous_category_id": previous.get("category_id"),
        "category_id": selection["category_id"], "type_id": selection["type_id"],
        "invalidated": invalidated, "ozon_write_api_calls": 0, "inventory_api_calls": 0,
    })
    return {
        "status": "changed", "product_id": product_dir.name,
        "category_id": selection["category_id"], "type_id": selection["type_id"],
        "invalidated": invalidated, "archive_path": str(invalidated_root.relative_to(product_dir)),
        "ozon_write_api_calls": 0, "inventory_api_calls": 0,
    }


@app.middleware("http")
async def local_network_only(request: Request, call_next):
    client_host = request.client.host if request.client else "unknown"
    loopback = is_loopback_client(client_host)
    config = load_lan_access_config()
    if not loopback:
        if not config.get("enabled") or not config.get("access_code"):
            return JSONResponse(
                status_code=503,
                content={"detail": {"code": "LAN_ACCESS_DISABLED", "message": "主电脑尚未启用工作室局域网访问"}},
            )
        if not client_ip_allowed(client_host, config):
            return JSONResponse(status_code=403, content={"detail": "当前设备不在允许的工作室局域网内"})
    supplied = request.headers.get("X-Factory-Access-Code") or request.cookies.get("caf_access") or ""
    operator: Optional[Dict[str, Any]] = None
    if request.url.path.startswith("/api/") and request.method != "OPTIONS" and request.url.path != "/health":
        operator = authenticate_operator(ROOT, supplied) if supplied else None
        if operator is None and loopback and not supplied:
            # The host Mac is the owner's trusted workstation. LAN devices and
            # any explicitly supplied code are always authenticated normally.
            operator = default_operator(ROOT)
        if operator is None:
            return JSONResponse(
                status_code=401,
                content={"detail": {"code": "ACCESS_CODE_REQUIRED", "message": "请输入你的工作室成员访问码"}},
            )
    token = CURRENT_OPERATOR.set(operator)
    request.state.operator = operator
    try:
        response = await call_next(request)
    finally:
        CURRENT_OPERATOR.reset(token)
    if supplied and operator:
        response.set_cookie(
            "caf_access", supplied, httponly=True, samesite="strict",
            max_age=60 * 60 * 12,
        )
    return response


def secrets_compare(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return hashlib.sha256(left.encode("utf-8")).digest() == hashlib.sha256(right.encode("utf-8")).digest()


def current_operator() -> Dict[str, Any]:
    return CURRENT_OPERATOR.get() or default_operator(ROOT)


def current_operator_id() -> str:
    return str(current_operator().get("id") or DEFAULT_OPERATOR_ID)


def require_owner_role() -> Dict[str, Any]:
    operator = current_operator()
    if operator.get("role") != "owner":
        raise HTTPException(status_code=403, detail="只有工作室负责人可以修改店铺、成员和系统设置")
    return operator


def product_owner(product_dir: Path) -> Dict[str, Any]:
    value = load_optional_json(product_dir / "input/owner.json")
    return {
        "owner_id": str(value.get("owner_id") or DEFAULT_OPERATOR_ID),
        "owner_name": str(value.get("owner_name") or "工作室负责人"),
    }


def product_is_owned(product_dir: Path, operator_id: Optional[str] = None) -> bool:
    return product_owner(product_dir)["owner_id"] == str(operator_id or current_operator_id())


def owned_product_dirs() -> List[Path]:
    return [
        path for path in sorted(PRODUCTS_DIR.glob(WORKBENCH_PRODUCT_GLOB), reverse=True)
        if path.is_dir() and product_is_owned(path)
    ]


def save_product_owner(product_dir: Path, operator: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    operator = operator or current_operator()
    value = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "owner_id": str(operator.get("id") or DEFAULT_OPERATOR_ID),
        "owner_name": str(operator.get("display_name") or "工作室负责人"),
        "assigned_at": now_iso(),
    }
    atomic_write_json(product_dir / "input/owner.json", value)
    return value


def batch_owner_path(batch_id: str) -> Path:
    return batch_path(ROOT, batch_id).with_name("owner.json")


def save_batch_owner(batch_id: str, operator: Optional[Dict[str, Any]] = None) -> None:
    operator = operator or current_operator()
    atomic_write_json(batch_owner_path(batch_id), {
        "schema_version": "1.0.0", "batch_id": batch_id,
        "owner_id": str(operator.get("id") or DEFAULT_OPERATOR_ID),
        "owner_name": str(operator.get("display_name") or "工作室负责人"),
        "created_at": now_iso(),
    })


def batch_is_owned(batch_id: str) -> bool:
    value = load_optional_json(batch_owner_path(batch_id))
    return str(value.get("owner_id") or DEFAULT_OPERATOR_ID) == current_operator_id()


def require_owned_batch(batch_id: str) -> None:
    if not batch_is_owned(batch_id):
        raise HTTPException(status_code=404, detail="任务不存在")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "collector-local-ingest", "root": str(ROOT)}


def read_product_card(product_dir: Path) -> Dict[str, Any]:
    source_path = product_dir / "input/source.json"
    status_path = product_dir / "status.json"
    source = json.loads(source_path.read_text(encoding="utf-8")) if source_path.is_file() else {}
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
    errors = []
    if status.get("error_message") not in {None, "unknown"}:
        errors.append(status["error_message"])
    errors.extend(item.get("reason", str(item)) for item in (status.get("ozon") or {}).get("errors", []))
    return {
        "product_id": product_dir.name,
        "title_cn": source.get("title_cn") or "unknown",
        "source_url": source.get("source_url") or "unknown",
        "selected_sku_count": len(source.get("skus") or []),
        "captured_at": source.get("captured_at") or status.get("started_at") or "unknown",
        "status": status.get("status") or "unknown",
        "current_step": status.get("current_step") or "none",
        "progress": int(status.get("progress") or 0),
        "warnings": status.get("warnings") or [],
        "errors": errors,
        "directory_path": str(product_dir),
        "thumbnail_url": f"/api/inbox/products/{product_dir.name}/thumbnail",
        "retryable": status.get("status") == "FAILED_HARD_BLOCKER",
    }


@app.get("/inbox")
def inbox_page() -> FileResponse:
    trigger_image_cleanup()
    ensure_image_status_monitor()
    sync_remote_ozon_status_once()
    return FileResponse(STATIC_DIR / "inbox.html", media_type="text/html")


@app.get("/inbox.css")
def inbox_css() -> FileResponse:
    return FileResponse(STATIC_DIR / "inbox.css", media_type="text/css")


@app.get("/inbox.js")
def inbox_js() -> FileResponse:
    return FileResponse(STATIC_DIR / "inbox.js", media_type="application/javascript")


@app.get("/api/inbox/products")
def list_inbox_products() -> Dict[str, Any]:
    active_product_ids = {path.name for path in retryable_products(ROOT)}
    products = [
        read_product_card(product_dir)
        for product_dir in sorted(PRODUCTS_DIR.glob("P[0-9]*"), reverse=True)
        if (product_dir / "status.json").is_file()
        and (product_dir / "input/source.json").is_file()
        and product_is_owned(product_dir)
        and "1688.com/offer/" in str(json.loads((product_dir / "input/source.json").read_text(encoding="utf-8")).get("source_url") or "")
    ]
    for item in products:
        item["in_current_inbox"] = item["product_id"] in active_product_ids
    pending = [item for item in products if item["in_current_inbox"]]
    return {
        "products": products,
        "product_count": len(products),
        "pending_product_count": len(pending),
        "pending_sku_count": sum(item["selected_sku_count"] for item in pending),
        "max_selected_skus_per_product": MAX_SELECTED_SKUS_PER_PRODUCT,
    }


def sync_remote_ozon_status_once(product_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """One non-blocking Ozon asynchronous-status sync per pending task, with no write API."""
    selected = set(product_ids or [])
    synced: List[str] = []
    store_checks = 0
    for product_dir in PRODUCTS_DIR.glob("P[0-9]*"):
        if selected and product_dir.name not in selected:
            continue
        status_path = product_dir / "status.json"
        if not status_path.is_file():
            continue
        status = json.loads(status_path.read_text(encoding="utf-8"))
        publications = load_publications(product_dir)
        pending_publications = [
            item for item in (publications.get("stores") or {}).values()
            if str(item.get("status") or "") in {"QUEUED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"}
        ]
        if pending_publications:
            result = refresh_pending_stores(ROOT, product_dir)
            store_checks += len(result.get("checked") or [])
            synced.append(product_dir.name)
            continue
        if status.get("status") != "PENDING_REMOTE":
            continue
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/recover_ozon_results.py"), product_dir.name, "--timeout", "1"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        if completed.returncode == 0:
            synced.append(product_dir.name)
    return {
        "synced_product_ids": synced, "store_checks": store_checks,
        "write_api_calls": 0, "inventory_api_calls": 0,
    }


def ensure_image_status_monitor() -> None:
    queue_path = ROOT / "image-channel-queue.json"
    if not queue_path.is_file() or not (json.loads(queue_path.read_text(encoding="utf-8")).get("items") or []):
        return
    pid_path = ROOT / "logs/image-status-monitor.pid"
    if pid_path.is_file():
        try:
            os.kill(int(pid_path.read_text(encoding="utf-8").strip()), 0)
            return
        except (OSError, TypeError, ValueError):
            pid_path.unlink(missing_ok=True)
    log_path = ROOT / "logs/image-status-monitor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/image_status_monitor.py")],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True,
    )
    log.close()
    pid_path.write_text(str(process.pid), encoding="utf-8")


@app.post("/api/inbox/refresh-ozon-status")
def refresh_ozon_status() -> Dict[str, Any]:
    return sync_remote_ozon_status_once([path.name for path in owned_product_dirs()])


@app.get("/api/inbox/products/{product_id}/thumbnail")
def product_thumbnail(product_id: str) -> FileResponse:
    product_dir = PRODUCTS_DIR / product_id
    if not re.fullmatch(r"P[0-9]{6}", product_id) or not product_dir.is_dir() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    candidates = sorted((product_dir / "input/main-images").glob("*"))
    image = next((path for path in candidates if path.is_file()), None)
    if image is None:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(image)


@app.post("/api/inbox/products/{product_id}/open-directory")
def open_product_directory(product_id: str) -> Dict[str, Any]:
    product_dir = PRODUCTS_DIR / product_id
    if not re.fullmatch(r"P[0-9]{6}", product_id) or not product_dir.is_dir() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    subprocess.Popen(["/usr/bin/open", str(product_dir)], close_fds=True)
    return {"status": "opened", "product_id": product_id, "path": str(product_dir)}


@app.delete("/api/inbox/products/{product_id}")
async def delete_inbox_product(product_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict) or payload.get("confirm_product_id") != product_id:
        raise HTTPException(status_code=422, detail="必须明确确认要彻底删除的商品ID")
    product_dir = PRODUCTS_DIR / product_id
    if not re.fullmatch(r"P[0-9]{6}", product_id) or not product_dir.is_dir() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    result = purge_local_product(ROOT, product_id)
    if result["status"] != "deleted":
        raise HTTPException(status_code=500, detail={"message": "商品未完全删除，可重新执行清理", **result})
    return result


def running_batch_pid() -> Optional[int]:
    if not BATCH_PID_PATH.is_file():
        return None
    try:
        pid = int(BATCH_PID_PATH.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return pid
    except (OSError, TypeError, ValueError):
        BATCH_PID_PATH.unlink(missing_ok=True)
        return None


def active_product_worker(product_dir: Path) -> Optional[Dict[str, Any]]:
    """Return a live registered worker so UI state follows the real process."""
    worker_path = ROOT / "logs/product-workers" / f"{product_dir.name}.json"
    worker = load_optional_json(worker_path)
    try:
        pid = int(worker.get("pid"))
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        if worker_path.is_file():
            worker_path.unlink(missing_ok=True)
        return None
    return worker


def effective_product_status(product_dir: Path, status: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay stale persisted STOPPED/QUEUED values while a real worker is alive."""
    worker = active_product_worker(product_dir)
    if not worker:
        return status
    effective = dict(status)
    effective.update({
        "status": "PROCESSING",
        "current_step": status.get("current_step") if status.get("current_step") not in {None, "", "queue"} else "image_generation",
        "active_step": status.get("active_step") or {
            "name": status.get("current_step") if status.get("current_step") not in {None, "", "queue"} else "image_generation",
            "started_at": worker.get("started_at") or now_iso(),
        },
        "last_run_at": worker.get("started_at") or status.get("last_run_at") or now_iso(),
    })
    return effective


def launch_batch_process(batch: Dict[str, Any]) -> Dict[str, Any]:
    BATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_handle = BATCH_LOG_PATH.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/run_batch.py"), "--batch-id", batch["batch_id"]],
        cwd=ROOT, stdout=log_handle, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True,
    )
    log_handle.close()
    BATCH_PID_PATH.write_text(str(process.pid), encoding="utf-8")
    atomic_write_json(CURRENT_BATCH_PATH, {"batch_id": batch["batch_id"], "pid": process.pid, "started_at": now_iso()})
    return {"pid": process.pid, "batch_id": batch["batch_id"]}


def connected_store_ids() -> List[str]:
    return [store["id"] for store in list_stores(ROOT) if store["enabled"] and store["connection_status"] == "connected"]


def validate_target_stores(store_ids: Iterable[str]) -> List[str]:
    selected = list(dict.fromkeys(str(value) for value in store_ids if str(value).strip()))
    if not selected:
        raise HTTPException(status_code=422, detail="请先明确选择至少一家已验证店铺")
    available = set(connected_store_ids())
    unavailable = [store_id for store_id in selected if store_id not in available]
    if unavailable:
        raise HTTPException(status_code=422, detail="店铺未启用或尚未通过只读验证：" + "、".join(unavailable))
    return selected


def _confirmation_source_label(source: str) -> str:
    return {
        "1688": "1688文字",
        "sku_specification": "SKU文字",
        "product_analysis": "已有商品分析",
        "estimated": "本地同类规则",
    }.get(str(source or ""), "本地规则")


def _source_material(source: Dict[str, Any]) -> Dict[str, Any]:
    material_names = {"材质", "材料", "主体材质", "产品材质", "面料"}
    for item in source.get("product_attributes") or []:
        if str(item.get("name_cn") or "").strip() not in material_names:
            continue
        value = str(item.get("value_cn") or "").strip()
        if value and value != "unknown":
            return {
                "value": value,
                "confidence": 100,
                "source": "1688文字",
                "estimated": False,
                "needs_input": False,
            }
    return {
        "value": "unknown",
        "confidence": 0,
        "source": "没有可靠依据",
        "estimated": False,
        "needs_input": True,
    }


def _confirmation_image_url(product_id: str, image_type: str, index: int) -> str:
    return f"/api/workbench/products/{urllib.parse.quote(product_id)}/source-images/{image_type}/{index}"


def _source_image_entries(product_id: str, source: Dict[str, Any], image_type: str) -> List[Dict[str, Any]]:
    if image_type == "sku":
        values = source.get("skus") or []
        result = []
        for index, item in enumerate(values):
            local_path = item.get("local_image_path") or item.get("variant_local_image_path")
            if not local_path or local_path == "unknown":
                continue
            result.append({
                "index": index,
                "label": str(item.get("sku_name") or item.get("sku_id") or f"SKU {index + 1}"),
                "url": _confirmation_image_url(product_id, "sku", index),
            })
        return result
    source_key = "main_images" if image_type == "main" else "detail_images"
    result = []
    for index, item in enumerate(source.get(source_key) or []):
        local_path = item.get("local_path")
        if not local_path or local_path == "unknown":
            continue
        result.append({
            "index": index,
            "label": "1688主图" if image_type == "main" else "1688详情图",
            "url": _confirmation_image_url(product_id, image_type, index),
        })
    return result


def build_product_confirmation(product_dir: Path) -> Dict[str, Any]:
    source = load_optional_json(product_dir / "input/source.json")
    category = load_optional_json(product_dir / "input/category-selection.json")
    analysis = load_optional_json(product_dir / "output/product-analysis.json", {
        "product_type": source.get("title_cn") or "unknown",
        "category": " / ".join(category.get("category_path_zh") or category.get("category_path") or []),
        "facts": {},
    })
    rules = load_optional_json(PRICING_RULES_PATH)
    profiles = rules.get("measurement_profiles") or []
    package_rules = rules.get("package_estimation") or {}
    if not profiles or not package_rules:
        raise HTTPException(status_code=500, detail="本地重量尺寸规则未配置")
    product_weight = estimate_product_weight(source, analysis, profiles)
    product_dimensions = estimate_product_dimensions(source, analysis, profiles)
    product_weight = fit_estimated_product_weight_to_confirmed_package(source, product_weight, package_rules)
    product_dimensions = fit_estimated_product_dimensions_to_confirmed_package(source, product_dimensions, package_rules)
    package_weight = estimate_package_weight(source, product_weight, package_rules)
    package_dimensions = estimate_package_dimensions(source, product_dimensions, package_rules)
    material = _source_material(source)
    sku_images = _source_image_entries(product_dir.name, source, "sku")
    main_images = _source_image_entries(product_dir.name, source, "main")
    detail_images = _source_image_entries(product_dir.name, source, "detail")
    selected_image = (sku_images or main_images or detail_images or [{}])[0].get("url")
    path_zh = category.get("category_path_zh") or category.get("category_path") or []
    sku_values = []
    for sku in source.get("skus") or []:
        option_text = " / ".join(
            str(item.get("value_cn") or item.get("value") or "")
            for item in sku.get("option_values") or []
            if item.get("value_cn") or item.get("value")
        )
        sku_values.append({
            "sku_id": str(sku.get("sku_id") or "unknown"),
            "name": str(sku.get("sku_name") or option_text or "未命名SKU"),
            "option_text": option_text or str(sku.get("sku_name") or "未确认规格"),
            "purchase_price_cny": sku.get("purchase_price"),
        })
    fields = {
        "product_dimensions": {
            "value": {key: product_dimensions[key] for key in ("length", "width", "height")},
            "unit": "cm", "confidence": int(product_dimensions["confidence"]),
            "source": _confirmation_source_label(product_dimensions["source"]),
            "estimated": bool(product_dimensions["estimated"]),
        },
        "product_weight_g": {
            "value": product_weight["value"], "unit": "g", "confidence": int(product_weight["confidence"]),
            "source": _confirmation_source_label(product_weight["source"]),
            "estimated": bool(product_weight["estimated"]),
        },
        "package_dimensions": {
            "value": {key: package_dimensions[key] for key in ("length", "width", "height")},
            "unit": "cm", "confidence": int(package_dimensions["confidence"]),
            "source": _confirmation_source_label(package_dimensions["source"]),
            "estimated": bool(package_dimensions["estimated"]),
        },
        "package_weight_g": {
            "value": package_weight["value"], "unit": "g", "confidence": int(package_weight["confidence"]),
            "source": _confirmation_source_label(package_weight["source"]),
            "estimated": bool(package_weight["estimated"]),
        },
        "material": material,
    }
    uncertain_count = sum(
        1 for item in fields.values()
        if item.get("estimated") or item.get("needs_input") or int(item.get("confidence") or 0) < 80
    )
    rules_snapshot = category.get("rules_snapshot") or {}
    return {
        "product_id": product_dir.name,
        "title_cn": str(source.get("title_cn") or product_dir.name),
        "source_url": str(source.get("source_url") or "unknown"),
        "category_id": category.get("category_id"),
        "type_id": category.get("type_id"),
        "category_path_zh": path_zh,
        "rules_snapshot_hash": category.get("rules_snapshot_hash") or "unknown",
        "required_attribute_count": len(rules_snapshot.get("required_attribute_ids") or []),
        "aspect_attribute_count": len(rules_snapshot.get("aspect_attribute_ids") or []),
        "sku_count": len(sku_values),
        "skus": sku_values,
        "fields": fields,
        "uncertain_count": uncertain_count,
        "thumbnail_url": selected_image,
        "sku_images": sku_images,
        "main_images": main_images,
        "reference_images": detail_images or main_images,
        "ordinary_field_count": max(0, len(rules_snapshot.get("attributes") or []) - uncertain_count),
        "omitted_without_evidence": ["认证", "承重", "特殊安全功能"],
    }


def build_batch_confirmation(batch: Dict[str, Any]) -> Dict[str, Any]:
    products = [build_product_confirmation(workbench_product_dir(product_id)) for product_id in batch_product_ids(batch)]
    return {
        "schema_version": "1.0.0",
        "batch_id": batch.get("batch_id"),
        "status": batch.get("status"),
        "mode": "auto" if batch.get("auto_upload") else "manual",
        "target_store_ids": batch.get("target_store_ids") or [],
        "product_count": len(products),
        "sku_count": sum(item["sku_count"] for item in products),
        "uncertain_count": sum(item["uncertain_count"] for item in products),
        "estimated_seconds": max(15, min(90, sum(item["uncertain_count"] for item in products) * 5)),
        "products": products,
        "created_at": batch.get("created_at"),
        "confirmed_at": batch.get("confirmed_at") or "unknown",
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def load_workbench_run_queue() -> Dict[str, Any]:
    if not WORKBENCH_RUN_QUEUE_PATH.is_file():
        return {"schema_version": "1.0.0", "items": []}
    try:
        data = json.loads(WORKBENCH_RUN_QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": "1.0.0", "items": []}
    return {"schema_version": "1.0.0", "items": list(data.get("items") or [])}


def save_workbench_run_queue(queue: Dict[str, Any]) -> None:
    atomic_write_json(WORKBENCH_RUN_QUEUE_PATH, {
        "schema_version": "1.0.0", "updated_at": now_iso(), "items": list(queue.get("items") or []),
    })


def batch_product_ids(batch: Dict[str, Any]) -> List[str]:
    return [str(item.get("product_id")) for item in batch.get("products") or [] if item.get("product_id")]


def reserved_product_batches() -> Dict[str, str]:
    reserved: Dict[str, str] = {}
    if running_batch_pid() is not None:
        current = load_optional_json(CURRENT_BATCH_PATH)
        batch_id = str(current.get("batch_id") or "")
        current_batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
        for product_id in batch_product_ids(current_batch):
            reserved[product_id] = batch_id
    for item in load_workbench_run_queue().get("items") or []:
        batch_id = str(item.get("batch_id") or "")
        queued_batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
        for product_id in batch_product_ids(queued_batch):
            reserved.setdefault(product_id, batch_id)
    for path in (ROOT / "batches").glob("B-*/batch.json"):
        waiting_batch = load_optional_json(path)
        if waiting_batch.get("status") != "AWAITING_CONFIRMATION":
            continue
        batch_id = str(waiting_batch.get("batch_id") or path.parent.name)
        for product_id in batch_product_ids(waiting_batch):
            reserved.setdefault(product_id, batch_id)
    return reserved


def dispatch_next_queued_batch() -> Optional[Dict[str, Any]]:
    with BATCH_QUEUE_LOCK:
        if running_batch_pid() is not None:
            return None
        queue = load_workbench_run_queue()
        items = list(queue.get("items") or [])
        while items:
            item = items.pop(0)
            batch_id = str(item.get("batch_id") or "")
            queued_batch = load_optional_json(batch_path(ROOT, batch_id)) if batch_id else {}
            valid_products = [
                product_id for product_id in batch_product_ids(queued_batch)
                if (ROOT / "products" / product_id).is_dir()
                and not deletion_marker_path(ROOT, product_id).is_file()
            ]
            if not queued_batch or not valid_products:
                queue["items"] = items
                save_workbench_run_queue(queue)
                continue
            launched = launch_batch_process(queued_batch)
            queue["items"] = items
            save_workbench_run_queue(queue)
            return {"status": "started", **launched, "queue_position": 0}
        queue["items"] = []
        save_workbench_run_queue(queue)
        return None


def batch_dispatcher_worker() -> None:
    while True:
        try:
            dispatch_next_queued_batch()
        except Exception as exc:
            BATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with BATCH_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(f"[{now_iso()}] batch dispatcher error: {exc}\n")
        BATCH_DISPATCHER_WAKE.wait(2)
        BATCH_DISPATCHER_WAKE.clear()


def ensure_batch_dispatcher() -> None:
    global BATCH_DISPATCHER_STARTED
    with BATCH_DISPATCHER_LOCK:
        if BATCH_DISPATCHER_STARTED:
            BATCH_DISPATCHER_WAKE.set()
            return
        threading.Thread(target=batch_dispatcher_worker, daemon=True, name="workbench-batch-dispatcher").start()
        BATCH_DISPATCHER_STARTED = True


def launch_or_enqueue_batch(batch: Dict[str, Any], source: str) -> Dict[str, Any]:
    with BATCH_QUEUE_LOCK:
        if running_batch_pid() is None:
            launched = launch_batch_process(batch)
            ensure_batch_dispatcher()
            return {"status": "started", **launched, "queue_position": 0}
        queue = load_workbench_run_queue()
        items = list(queue.get("items") or [])
        items.append({
            "batch_id": batch["batch_id"], "source": source,
            "queued_at": now_iso(), "product_count": batch.get("product_count", 0),
        })
        queue["items"] = items
        save_workbench_run_queue(queue)
        ensure_batch_dispatcher()
        return {"status": "queued", "batch_id": batch["batch_id"], "queue_position": len(items)}


@app.post("/api/tasks/run")
def run_collected_tasks() -> Dict[str, Any]:
    selected_stores: List[str] = []
    auto_upload = False
    ensure_image_status_monitor()
    sync_remote_ozon_status_once()
    with BATCH_QUEUE_LOCK:
        reserved = reserved_product_batches()
        product_ids = [
            path.name for path in collected_products(ROOT)
            if path.name not in reserved and product_is_owned(path)
        ]
        if not product_ids:
            return {"status": "empty", "queued_products": 0, "already_queued_products": len(reserved)}
        batch = create_batch(
            ROOT, product_ids=product_ids, target_store_ids=selected_stores, auto_upload=auto_upload,
        )
        save_batch_owner(batch["batch_id"])
        launched = launch_or_enqueue_batch(batch, "collector")
    return {
        "status": launched["status"],
        "pid": launched.get("pid"),
        "batch_id": batch["batch_id"],
        "queue_position": launched.get("queue_position", 0),
        "queued_products": batch["product_count"],
        "queued_skus": batch["sku_count"],
        "max_selected_skus_per_product": MAX_SELECTED_SKUS_PER_PRODUCT,
        "target_store_ids": selected_stores,
        "auto_upload": auto_upload,
    }


def overlay_live_batch_status(batch: Dict[str, Any], products_dir: Path) -> Dict[str, Any]:
    live_products = []
    progress_values = []
    for entry in batch.get("products") or []:
        product_id = str(entry.get("product_id") or "")
        status_path = products_dir / product_id / "status.json"
        if not status_path.is_file():
            live_products.append(entry)
            continue
        status = effective_product_status(
            products_dir / product_id,
            json.loads(status_path.read_text(encoding="utf-8")),
        )
        progress = int(status.get("progress") or 0)
        live_products.append({
            **entry,
            "status": status.get("status", entry.get("status", "unknown")),
            "current_step": status.get("current_step", entry.get("current_step", "none")),
            "progress": progress,
            "started_at": status.get("started_at", entry.get("started_at", "unknown")),
            "completed_at": status.get("completed_at", entry.get("completed_at", "unknown")),
            "warnings": status.get("warnings", entry.get("warnings", [])),
            "errors": [status.get("error_message")]
            if status.get("error_message") not in {None, "unknown"} else [],
        })
        progress_values.append(progress)
    result = {**batch, "products": live_products}
    if progress_values:
        result["progress"] = round(sum(progress_values) / len(progress_values))
    return result


@app.get("/api/tasks/status")
def get_batch_status() -> Dict[str, Any]:
    pid = running_batch_pid()
    current = json.loads(CURRENT_BATCH_PATH.read_text(encoding="utf-8")) if CURRENT_BATCH_PATH.is_file() else {}
    current_path = batch_path(ROOT, current.get("batch_id", "")) if current.get("batch_id") else None
    batch = json.loads(current_path.read_text(encoding="utf-8")) if current_path and current_path.is_file() else None
    if batch and batch_is_owned(str(batch.get("batch_id") or "")):
        batch = overlay_live_batch_status(batch, PRODUCTS_DIR)
    elif batch:
        batch = None
    report_path = ROOT / "batch-result.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
    if report and not batch_is_owned(str(report.get("batch_id") or "")):
        report = None
    return {"running": pid is not None, "pid": pid, "current_batch": batch, "last_result": report}


@app.post("/api/collector/products")
async def create_product(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail={"message": "Payload must be a JSON object"})
    return ingest_capture(payload, current_operator())


@app.get("/api/collector/categories")
def collector_category_search(q: str = "", limit: int = 30) -> Dict[str, Any]:
    items = search_categories(ROOT, q, limit)
    return {"query": q, "items": items, "count": len(items), "ozon_write_api_calls": 0, "inventory_api_calls": 0}


@app.get("/api/collector/categories/tree")
def collector_category_tree(parent_id: str = "root") -> Dict[str, Any]:
    try:
        items = category_tree_children(ROOT, parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc
    cache = load_translated_tree_cache(ROOT)
    return {
        "parent_id": parent_id,
        "items": items,
        "count": len(items),
        "locale": cache.get("locale") or "unknown",
        "cache_version": cache.get("cache_version") or "dynamic-fallback",
        "cache_source": "versioned_local_json" if cache else "dynamic_local_catalog",
        "ozon_write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.get("/api/collector/categories/recommendations")
def collector_category_recommendations(q: str) -> Dict[str, Any]:
    items = recommend_categories(ROOT, q)
    return {"query": q, "items": items[:3], "count": min(len(items), 3), "final_choice_required": True}


@app.get("/api/collector/categories/preferences")
def collector_category_preferences() -> Dict[str, Any]:
    return {**public_preferences(ROOT), "ozon_write_api_calls": 0, "inventory_api_calls": 0}


@app.put("/api/collector/categories/favorite")
async def collector_category_favorite(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return set_favorite(
            ROOT, int(payload.get("category_id")), int(payload.get("type_id")), bool(payload.get("favorite", True))
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc


@app.post("/api/collector/categories/rules")
async def collector_category_rules(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    try:
        return prepare_rules(
            ROOT,
            int(payload.get("category_id")),
            int(payload.get("type_id")),
            str(payload.get("shop_id") or "zhonglian1"),
            allow_fetch=bool(payload.get("allow_readonly_fetch", True)),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)}) from exc
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc


@app.get("/api/collector/products/{product_id}")
def get_product(product_id: str) -> Dict[str, Any]:
    product_dir = PRODUCTS_DIR / product_id
    source_path = product_dir / "input/source.json"
    status_path = product_dir / "status.json"
    if not source_path.is_file() or not status_path.is_file() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    return {
        "product_id": product_id,
        "source": json.loads(source_path.read_text(encoding="utf-8")),
        "status": json.loads(status_path.read_text(encoding="utf-8")),
        "category_selection": json.loads((product_dir / "input/category-selection.json").read_text(encoding="utf-8"))
        if (product_dir / "input/category-selection.json").is_file() else None,
    }


@app.put("/api/collector/products/{product_id}/category")
async def update_collected_product_category(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = PRODUCTS_DIR / product_id
    if not re.fullmatch(r"P[0-9]{6}", product_id) or not product_dir.is_dir() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    payload = await request.json()
    try:
        selection = build_selection(ROOT, {"ozon_category_selection": payload}, preferences_root=PRODUCTS_DIR.parent)
        selection_errors = validate_json(selection, "category-selection.schema.json")
        if selection_errors:
            raise ValueError("类目选择数据无效：" + "；".join(selection_errors))
        return replace_collected_category(product_dir, selection)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)}) from exc


@app.get("/api/collector/products/{product_id}/status")
def get_product_status(product_id: str) -> Dict[str, Any]:
    product_dir = PRODUCTS_DIR / product_id
    status_path = product_dir / "status.json"
    if not status_path.is_file() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="Product not found")
    return json.loads(status_path.read_text(encoding="utf-8"))


@app.get("/api/collector/duplicates")
def get_duplicate(source_url: str) -> Dict[str, Any]:
    duplicate_of = find_existing_source_urls().get(source_url)
    return {
        "exists": duplicate_of is not None,
        "product_id": duplicate_of,
        "source_url": source_url
    }


# ---------------------------------------------------------------------------
# AI product production workbench
# ---------------------------------------------------------------------------

WORKBENCH_EDITABLE_FIELDS = {
    "title_ru", "short_title", "description_ru", "bullets_ru", "tags",
    "attributes", "sku_overrides", "image_order", "selected_shop", "selected_store_ids",
    "auto_advance", "review_mode", "review_depth", "notes", "image_prompts",
}
WORKBENCH_PRODUCT_GLOB = "P[0-9][0-9][0-9][0-9][0-9][0-9]"


def load_optional_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def workbench_settings() -> Dict[str, Any]:
    value = load_optional_json(ROOT / "config/workbench-settings.json", DEFAULT_WORKBENCH_SETTINGS.copy())
    return {**DEFAULT_WORKBENCH_SETTINGS, **(value if isinstance(value, dict) else {})}


def save_workbench_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    settings = workbench_settings()
    if "auto_mode_enabled" in patch:
        settings["auto_mode_enabled"] = bool(patch["auto_mode_enabled"])
        settings["default_review_mode"] = "auto" if settings["auto_mode_enabled"] else "manual"
    settings["learning_threshold"] = 2
    settings["updated_at"] = now_iso()
    atomic_write_json(ROOT / "config/workbench-settings.json", settings)
    return settings


def workbench_product_dir(product_id: str) -> Path:
    if not re.fullmatch(r"P[0-9]{6}", product_id):
        raise HTTPException(status_code=404, detail="商品不存在")
    product_dir = PRODUCTS_DIR / product_id
    if not product_dir.is_dir() or not product_is_owned(product_dir):
        raise HTTPException(status_code=404, detail="商品不存在")
    return product_dir


def public_state(status_name: str) -> str:
    value = str(status_name or "unknown").upper()
    if "FAIL" in value or "ERROR" in value:
        return "失败"
    if value in {"UPLOADED", "OZON_MODERATION", "ACTIVE", "SUCCESS", "IMPORTED"}:
        return "完成"
    if value in {"COLLECTED", "STOPPED", "OZON_READY", "WAITING", "NOT_STARTED", "UNKNOWN"}:
        return "待处理"
    return "处理中"


def workflow_bucket(status_name: str) -> str:
    value = str(status_name or "unknown").upper()
    if value in {"UPLOADED", "ACTIVE"}:
        return "已完成"
    if value in {"UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"}:
        return "上传中"
    if value in {"OZON_READY", "FAILED_HARD_BLOCKER"}:
        return "待人工检查"
    if value in {"QUEUED", "PROCESSING", "CATEGORY_MATCHED", "CONTENT_GENERATED", "IMAGES_GENERATED", "PRICED"}:
        return "生成中"
    if value == "STOPPED":
        return "已停止"
    return "采集箱"


def image_plan_items(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for key in ("main_images", "detail_images", "disclaimer_images", "color_samples"):
        for item in plan.get(key) or []:
            if isinstance(item, dict) and item.get("slot"):
                items.append(item)
    return sorted(items, key=lambda item: int(item.get("workbench_order", len(items))))


def find_image_plan_item(plan: Dict[str, Any], slot: str) -> Tuple[str, Dict[str, Any]]:
    for key in ("main_images", "detail_images", "disclaimer_images", "color_samples"):
        for item in plan.get(key) or []:
            if str(item.get("slot")) == slot:
                return key, item
    raise HTTPException(status_code=404, detail="图片槽位不存在")


def regeneration_slot_names(value: Any) -> List[str]:
    """Accept both legacy slot strings and detailed retry records."""
    names: List[str] = []
    for item in value or []:
        if isinstance(item, dict):
            item = item.get("slot") or item.get("image_slot")
        slot = str(item or "").strip()
        if slot and slot not in names:
            names.append(slot)
    return names


def workbench_images(
    product_dir: Path,
    plan: Dict[str, Any],
    qc: Dict[str, Any],
    status: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    request = load_optional_json(product_dir / "output/image-regeneration-request.json")
    retry_slots = set(regeneration_slot_names(request.get("failed_slots")))
    issue_by_slot: Dict[str, List[Dict[str, Any]]] = {}
    for issue in qc.get("issues") or []:
        for slot in issue.get("image_slots") or []:
            issue_by_slot.setdefault(str(slot), []).append(issue)
    qc_dimensions = qc.get("dimensions") or {}
    score = qc.get("score")
    images = []
    active_generation = bool(
        status and status.get("status") == "PROCESSING" and status.get("current_step") == "image_generation"
    )
    generating_assigned = False
    for index, item in enumerate(image_plan_items(plan)):
        slot = str(item.get("slot"))
        raw_path = str(item.get("output_path") or "")
        image_path = (ROOT / raw_path).resolve() if raw_path else None
        exists = bool(image_path and image_path.is_file() and ROOT.resolve() in image_path.parents)
        issues = issue_by_slot.get(slot, [])
        blocking_issues = [
            entry for entry in issues
            if str(entry.get("severity") or "").lower() in {"high", "critical"}
            or str(entry.get("code") or "") in set(qc.get("critical_failures") or [])
        ]
        if slot in retry_slots:
            state = "RETRYING"
        elif exists and issues and (blocking_issues or qc.get("decision") == "reject"):
            state = "FAIL"
        elif exists and qc:
            state = "PASS" if qc.get("decision") != "reject" else "QC"
        elif exists:
            state = "COMPLETED"
        elif item.get("status") in {"generating", "processing"}:
            state = "GENERATING"
        elif active_generation and not generating_assigned:
            state = "GENERATING"
            generating_assigned = True
        else:
            state = "WAITING"
        images.append({
            "slot": slot,
            "type": item.get("image_type") or item.get("type") or "detail",
            "state": state,
            "url": f"/api/workbench/products/{product_dir.name}/images/{urllib.parse.quote(slot)}" if exists else None,
            "download_url": f"/api/workbench/products/{product_dir.name}/images/{urllib.parse.quote(slot)}?download=1" if exists else None,
            "prompt": item.get("prompt") or item.get("prompt_brief") or "",
            "russian_text": item.get("russian_text") or [],
            "purpose": item.get("selling_goal") or item.get("purpose") or "",
            "variant_scope": item.get("variant_scope") or "shared",
            "shared_across_variants": bool(item.get("shared_across_variants")),
            "score": score,
            "issues": [entry.get("message") or entry.get("code") for entry in issues],
            "qc_dimensions": qc_dimensions,
            "order": index,
        })
    return images


def readable_timeline(status: Dict[str, Any], product_dir: Path) -> List[Dict[str, Any]]:
    step_labels = {
        "collect_source": "完成1688采集", "validate_source": "完成采集数据检查",
        "product_analysis": "完成商品理解", "category_match": "完成Ozon类目匹配",
        "variant_rules": "完成SKU变体判断", "measurements": "完成重量尺寸处理",
        "russian_copy": "完成俄文资料", "image_plan": "完成图片方案",
        "image_generation": "完成图片生成", "image_qc": "完成图片质检",
        "field_completion": "完成Ozon字段整理", "ozon_upload": "提交Ozon",
    }
    result: List[Dict[str, Any]] = []
    for item in status.get("steps") or []:
        label = step_labels.get(str(item.get("name")), str(item.get("name") or "任务更新"))
        if item.get("status") == "failed":
            label = f"{label}失败"
        result.append({
            "at": item.get("finished_at") or item.get("started_at") or "unknown",
            "message": label,
            "level": "error" if item.get("status") == "failed" else "info",
        })
    ozon = status.get("ozon") or {}
    if ozon.get("task_id") not in {None, "unknown", ""}:
        result.append({
            "at": status.get("last_run_at") or "unknown",
            "message": f"已获得Ozon任务号 {ozon.get('task_id')}",
            "level": "info",
        })
    return sorted(result, key=lambda item: str(item.get("at") or ""), reverse=True)[:80]


def calculate_risk(status: Dict[str, Any], category: Dict[str, Any], attributes: Dict[str, Any], qc: Dict[str, Any]) -> Dict[str, Any]:
    items: List[Dict[str, str]] = []
    if "FAIL" in str(status.get("status") or ""):
        items.append({"level": "high", "code": "pipeline_failed", "message": str(status.get("error_message") or "商品处理失败")})
    missing = attributes.get("missing_required_attributes") or []
    if missing:
        names = "、".join(str(item.get("attribute_name") or item.get("attribute_id")) for item in missing[:4])
        items.append({"level": "high", "code": "required_attributes", "message": f"缺少必填属性：{names}"})
    if qc.get("decision") == "reject":
        items.append({"level": "high", "code": "image_qc", "message": f"图片质检未通过，得分 {qc.get('score', '未知')}"})
    confidence = category.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < 0.8:
        items.append({"level": "medium", "code": "category_confidence", "message": f"类目置信度较低：{confidence:.0%}"})
    if any(item["level"] == "high" for item in items):
        level = "high"
    elif items:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "items": items}


def prelisting_assessment(pricing: Dict[str, Any], qc: Dict[str, Any], risk: Dict[str, Any]) -> Dict[str, Any]:
    sku_prices = pricing.get("sku_pricing") or []
    profits = [item.get("estimated_profit_cny") for item in sku_prices if isinstance(item.get("estimated_profit_cny"), (int, float))]
    profit_rates = [item.get("profit_rate_markup") for item in sku_prices if isinstance(item.get("profit_rate_markup"), (int, float))]
    profit_score = min(100, max(0, round(45 + (max(profit_rates or [0]) * 70))))
    image_score = int(qc.get("score") or (85 if qc.get("decision") == "pass" else 55))
    risk_penalty = {"low": 4, "medium": 22, "high": 48}.get(str(risk.get("level")), 30)
    market_score = max(20, 86 - risk_penalty)
    competition_risk = min(100, 32 + risk_penalty)
    return_risk = min(100, 24 + risk_penalty)
    overall = round((profit_score * .32) + (market_score * .24) + (image_score * .24) + ((100 - competition_risk) * .1) + ((100 - return_risk) * .1))
    advice = "优先处理" if overall >= 80 else "可以测试" if overall >= 58 else "暂缓处理"
    selling_prices = [item.get("selling_price_rub") for item in sku_prices if isinstance(item.get("selling_price_rub"), (int, float))]
    costs = [item.get("base_cost_cny") for item in sku_prices if isinstance(item.get("base_cost_cny"), (int, float))]
    exchange_value = pricing.get("exchange_rate") or 12
    if isinstance(exchange_value, dict):
        exchange_value = exchange_value.get("cny_to_rub") or exchange_value.get("value") or 12
    exchange = float(exchange_value)
    rule_price = min(selling_prices) if selling_prices else None
    break_even = round(max(costs or [0]) * exchange * 1.31) if costs else None
    return {
        "profit_potential": profit_score, "russia_fit": market_score, "image_sales_potential": image_score,
        "competition_risk": competition_risk, "return_risk": return_risk,
        "overall_score": overall, "advice": advice,
        "pricing_advice": {
            "break_even_price_rub": break_even, "rule_price_rub": rule_price,
            "suggested_range_rub": [round(rule_price * .97), round(rule_price * 1.08)] if rule_price else [],
            "high_profit_test_price_rub": round(rule_price * 1.12) if rule_price else None,
            "estimated_profit_cny": round(min(profits), 2) if profits else None,
            "minimum_rules_respected": True,
        },
        "source": "现有成本、定价、图片质检和风险结果的上架前计算，不含销量预测",
    }


def build_ai_suggestions(product_dir: Path, content: Dict[str, Any], risk: Dict[str, Any], qc: Dict[str, Any]) -> List[Dict[str, Any]]:
    saved = load_optional_json(product_dir / "output/ai-suggestions.json", {"items": []})
    states = {str(item.get("id")): item for item in saved.get("items") or []}
    candidates: List[Dict[str, Any]] = []
    if len(content.get("tags") or []) != 30:
        candidates.append({"id": "tag_count", "type": "copy", "title": "补齐30个俄文主题标签", "detail": "每个标签单独保存且不超过30个字符。"})
    if qc.get("decision") == "reject":
        candidates.append({"id": "image_qc", "type": "image", "title": "仅重做未通过图片", "detail": "保留合格图片，不重做整套。"})
    for item in risk.get("items") or []:
        candidates.append({"id": f"risk_{item.get('code')}", "type": "risk", "title": "处理阻断风险", "detail": item.get("message")})
    for candidate in candidates:
        candidate.update({"status": states.get(candidate["id"], {}).get("status", "pending"), "non_blocking": True})
    return candidates


def pending_product_question(product_dir: Path) -> Dict[str, Any]:
    value = load_optional_json(product_dir / "input/pending-question.json")
    return value if str(value.get("status") or "").upper() == "OPEN" else {}


def product_primary_action(detail: Dict[str, Any]) -> Dict[str, str]:
    status = str((detail.get("status") or {}).get("status") or "unknown").upper()
    if detail.get("pending_question"):
        return {"key": "answer", "label": "回答问题"}
    if status in {"COLLECTED", "STOPPED"}:
        return {"key": "run", "label": "运行任务"}
    if status == "FAILED_HARD_BLOCKER":
        return {"key": "fix", "label": "查看并继续"}
    if status == "OZON_READY":
        return {"key": "review_upload", "label": "检查并上传"}
    if status in {"UPLOADED", "ACTIVE"}:
        return {"key": "result", "label": "查看上架结果"}
    if status in {"UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"}:
        return {"key": "status", "label": "查看上传状态"}
    return {"key": "status", "label": "查看进度"}


def workbench_product_detail(product_id: str) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    source = load_optional_json(product_dir / "input/source.json")
    status = effective_product_status(product_dir, load_optional_json(product_dir / "status.json"))
    analysis = load_optional_json(product_dir / "output/product-analysis.json")
    copy = load_optional_json(product_dir / "output/copy-ru.json")
    category = load_optional_json(product_dir / "output/ozon-category.json")
    selected_category = load_optional_json(product_dir / "input/category-selection.json")
    selected_catalog_category: Dict[str, Any] = {}
    if selected_category:
        try:
            selected_catalog_category = get_category(
                ROOT, int(selected_category.get("category_id")), int(selected_category.get("type_id"))
            )
        except (TypeError, ValueError):
            selected_catalog_category = {}
    category_name_zh = (selected_category or {}).get("category_name_zh") or selected_catalog_category.get("name_zh")
    category_path_zh = (selected_category or {}).get("category_path_zh") or selected_catalog_category.get("path_zh") or []
    if not category:
        if selected_category:
            category = {
                "category_id": selected_category.get("category_id"),
                "type_id": selected_category.get("type_id"),
                "category_name": category_name_zh or selected_category.get("category_name_ru"),
                "category_name_zh": category_name_zh,
                "category_path": selected_category.get("category_path") or [],
                "category_path_zh": category_path_zh,
                "match_status": "user_selected_at_collection",
                "confidence": 1.0,
                "rules_snapshot_hash": selected_category.get("rules_snapshot_hash"),
            }
    elif selected_category:
        category["category_name_zh"] = category_name_zh
        category["category_path_zh"] = category_path_zh
    attributes = load_optional_json(product_dir / "output/ozon-attributes.json")
    final_attributes = load_optional_json(product_dir / "output/ozon-attributes-final.json")
    final_by_id = {
        str(item.get("attribute_id")): item
        for item in final_attributes.get("attributes") or []
        if item.get("attribute_id") is not None
    }
    def attribute_has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip()) and value.strip().casefold() != "unknown"
        if isinstance(value, (list, dict)):
            return bool(value)
        return True

    for item in attributes.get("attributes") or []:
        final = final_by_id.get(str(item.get("attribute_id")))
        if final:
            for field in (
                "value", "source", "confidence", "dictionary_value_id",
                "evidence", "required",
            ):
                if field in final:
                    item[field] = final[field]
            item["validation_status"] = (
                "estimated" if final.get("source") == "AI_estimated"
                else "valid" if attribute_has_value(final.get("value"))
                else "unknown"
            )
    if final_by_id:
        attribute_items = attributes.get("attributes") or []
        filled = [item for item in attribute_items if attribute_has_value(item.get("value"))]
        missing_required = [
            item for item in attribute_items
            if item.get("required") and not attribute_has_value(item.get("value"))
        ]
        attributes["summary"] = {
            **(attributes.get("summary") or {}),
            "total_count": len(attribute_items),
            "filled_count": len(filled),
            "unknown_count": len(attribute_items) - len(filled),
            "required_count": sum(bool(item.get("required")) for item in attribute_items),
            "required_filled_count": sum(
                bool(item.get("required")) and attribute_has_value(item.get("value"))
                for item in attribute_items
            ),
            "mapped_count": sum(
                bool(item.get("required")) and attribute_has_value(item.get("value"))
                for item in attribute_items
            ),
            "missing_count": len(missing_required),
        }
        attributes["missing_required_attributes"] = missing_required
    attribute_translations = load_optional_json(ATTRIBUTE_TRANSLATIONS_PATH, {"translations": {}}).get("translations") or {}
    for item in attributes.get("attributes") or []:
        item["attribute_name_zh"] = attribute_translations.get(str(item.get("attribute_name") or ""), item.get("attribute_name") or str(item.get("attribute_id")))
    pricing = load_optional_json(product_dir / "output/pricing-result.json")
    plan = load_optional_json(product_dir / "output/image-plan.json")
    qc = load_optional_json(product_dir / "output/image-qc-report.json")
    draft = load_optional_json(product_dir / "output/workbench-draft.json")
    manual_attributes = draft.get("attributes") or {}
    for item in attributes.get("attributes") or []:
        attribute_id = str(item.get("attribute_id"))
        if attribute_id in manual_attributes:
            item["value"] = manual_attributes[attribute_id]
            item["source"] = "人工修改"
            item["validation_status"] = "pending_dictionary_validation"
    sku_overrides = draft.get("sku_overrides") or {}
    for item in pricing.get("sku_pricing") or []:
        override = sku_overrides.get(str(item.get("sku_id"))) or {}
        for field in ("selling_price_cny", "selling_price_rub"):
            if field in override:
                item[field] = override[field]
    rich = {}
    for name in ("ozon-rich-content.json", "rich-content.json", "ozon-draft.json"):
        candidate = load_optional_json(product_dir / "output" / name)
        if candidate:
            rich = candidate
            break
    final_tags = load_optional_json(product_dir / "output/ozon-tags.json")
    tags = final_tags.get("tags") or copy.get("keywords_ru") or copy.get("keywords") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    tags = [value if str(value).startswith("#") else f"#{value}" for value in tags]
    content = {
        "title_ru": copy.get("title_ru") or "",
        "title_zh_reference": source.get("title_cn") or "unknown",
        "short_title": copy.get("short_title") or "",
        "description_ru": copy.get("description_ru") or copy.get("description") or "",
        "description_zh_reference": "；".join(
            str(item.get("text") if isinstance(item, dict) else item)
            for item in (analysis.get("selling_points") or [])[:6]
            if str(item.get("text") if isinstance(item, dict) else item).strip()
        ) or source.get("title_cn") or "unknown",
        "bullets_ru": copy.get("bullets_ru") or [],
        "tags": tags,
    }
    for field in WORKBENCH_EDITABLE_FIELDS:
        if field in draft:
            content[field] = draft[field]
    sku_pricing = {str(item.get("sku_id")): item for item in pricing.get("sku_pricing") or []}
    skus = []
    for sku in source.get("skus") or []:
        price = sku_pricing.get(str(sku.get("sku_id"))) or {}
        options = sku.get("option_values") or []
        option_text = " / ".join(str(value.get("value") if isinstance(value, dict) else value) for value in options)
        skus.append({
            "sku_id": sku.get("sku_id"), "name": sku.get("sku_name"),
            "options": options, "option_text": option_text, "purchase_price_cny": sku.get("purchase_price"),
            "selling_price_cny": price.get("selling_price_cny"),
            "selling_price_rub": price.get("selling_price_rub"),
            "profit_cny": price.get("estimated_profit_cny"),
            "profit_rate": price.get("profit_rate_markup"),
            "weight_g": ((price.get("shipping") or {}).get("weight") or {}).get("actual_weight_g"),
            "offer_id": ((status.get("ozon") or {}).get("offer_id") or "unknown"),
            "variant_decision": analysis.get("variant_decision") or analysis.get("grouping_decision") or "按Ozon变体规则",
            "aspect_basis": analysis.get("is_aspect_basis") or category.get("variant_basis") or "以当前类目is_aspect规则为准",
            "image_missing": bool(sku.get("sku_image_missing")),
        })
    risk = calculate_risk(status, category, attributes, qc)
    stores = list_stores(ROOT)
    publications = load_publications(product_dir, [store["id"] for store in stores])
    assessment = prelisting_assessment(pricing, qc, risk)
    detail = {
        "product_id": product_id,
        "source": {
            "title_cn": source.get("title_cn") or "unknown", "source_url": source.get("source_url") or "unknown",
            "captured_at": source.get("captured_at") or "unknown", "main_image_count": len(source.get("main_images") or []),
            "detail_image_count": len(source.get("detail_images") or []),
        },
        "status": status,
        "public_state": public_state(status.get("status")),
        "progress": int(status.get("progress") or 0),
        "content": content,
        "draft": {"version": int(draft.get("version") or 0), "saved_at": draft.get("saved_at"), "locked_fields": draft.get("locked_fields") or []},
        "analysis": analysis,
        "category": category,
        "attributes": attributes,
        "pricing": pricing,
        "skus": skus,
        "images": workbench_images(product_dir, plan, qc, status),
        "image_qc": qc,
        "rich_content": rich,
        "risk": risk,
        "prelisting_assessment": assessment,
        "stores": stores,
        "publications": publications,
        "publication_summary": publication_summary(publications),
        "ai_suggestions": build_ai_suggestions(product_dir, content, risk, qc),
        "ozon": status.get("ozon") or {},
        "timeline": readable_timeline(status, product_dir),
        "workbench_settings": workbench_settings(),
        "owner": product_owner(product_dir),
        "pending_question": pending_product_question(product_dir),
        "visual_preference": load_optional_json(product_dir / "input/visual-preference.json", {
            "set_hint": "", "slot_hints": {},
        }),
    }
    detail["primary_action"] = product_primary_action(detail)
    detail["attention_required"] = bool(
        detail["pending_question"]
        or str(status.get("status") or "").upper() in {"FAILED_HARD_BLOCKER", "OZON_READY"}
    )
    return detail


def workbench_card(product_dir: Path) -> Dict[str, Any]:
    detail = workbench_product_detail(product_dir.name)
    price_items = detail["pricing"].get("sku_pricing") or []
    prices = [item.get("purchase_cost_cny") for item in price_items if isinstance(item.get("purchase_cost_cny"), (int, float))]
    thumbnail_exists = any(path.is_file() for path in (product_dir / "input/main-images").glob("*"))
    publication_values = list((detail.get("publications", {}).get("stores") or {}).values())
    remote_search = []
    for publication in publication_values:
        remote_search.append(str(publication.get("store_id") or ""))
        for sku in publication.get("sku_publications") or []:
            remote_search.extend(str(sku.get(key) or "") for key in ("sku_id", "offer_id", "task_id", "ozon_product_id"))
    remote_search.extend(str(sku.get("sku_id") or "") for sku in detail["skus"])
    remote_search.extend([str(detail["source"].get("source_url") or ""), str(detail["ozon"].get("task_id") or ""), str(detail["ozon"].get("product_id") or "")])
    return {
        "product_id": detail["product_id"], "title_cn": detail["source"]["title_cn"],
        "title_ru": detail["content"]["title_ru"], "source_url": detail["source"]["source_url"],
        "captured_at": detail["source"]["captured_at"], "state": detail["public_state"],
        "workflow_bucket": workflow_bucket(detail["status"].get("status")),
        "raw_status": detail["status"].get("status") or "unknown", "current_step": detail["status"].get("current_step") or "queue", "progress": detail["progress"],
        "sku_count": len(detail["skus"]), "purchase_price_cny": min(prices) if prices else None,
        "risk": detail["risk"], "image_count": len([item for item in detail["images"] if item.get("url")]),
        "thumbnail_url": f"/api/inbox/products/{detail['product_id']}/thumbnail" if thumbnail_exists else None,
        "batch_id": detail["status"].get("batch_id") or "unknown",
        "selected_store_count": detail["publication_summary"]["selected"],
        "search_terms": " ".join(remote_search),
        "owner": detail["owner"],
        "primary_action": detail["primary_action"],
        "attention_required": detail["attention_required"],
        "pending_question": detail["pending_question"],
    }


def associated_shops(product_dir: Path, status: Dict[str, Any]) -> List[str]:
    shops: set[str] = set()
    known_keys = {"shop", "shop_name", "selected_shop", "store", "store_name"}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in known_keys and isinstance(item, str) and item.strip() and item != "unknown":
                    shops.add(item.strip())
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(status)
    for path in (
        product_dir / "output/workbench-draft.json",
        product_dir / "output/ozon-result.json",
        product_dir / "output/ozon-upload-config.json",
        product_dir / "output/ozon-write-receipt.json",
    ):
        visit(load_optional_json(path))
    publications = load_publications(product_dir)
    for store_id, record in (publications.get("stores") or {}).items():
        if record.get("selected") or str(record.get("status")) not in {"", "NOT_SELECTED"}:
            shops.add(store_id)
    return sorted(shops)


def workbench_delete_preview(product_id: str) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    source = load_optional_json(product_dir / "input/source.json")
    status = load_optional_json(product_dir / "status.json")
    ozon = status.get("ozon") or {}
    result = load_optional_json(product_dir / "output/ozon-result.json")
    items = result.get("items") or result.get("offers") or []
    remote_ids = {
        "task_ids": sorted({str(value) for value in [ozon.get("task_id"), result.get("task_id")] if value not in {None, "", "unknown"}}),
        "offer_ids": sorted({str(value) for value in [ozon.get("offer_id"), *[item.get("offer_id") for item in items if isinstance(item, dict)]] if value not in {None, "", "unknown"}}),
        "product_ids": sorted({str(value) for value in [ozon.get("product_id"), *[item.get("product_id") or item.get("ozon_product_id") for item in items if isinstance(item, dict)]] if value not in {None, "", "unknown"}}),
    }
    submitted = bool(
        int(status.get("api_write_count") or 0) > 0
        or any(remote_ids.values())
        or str(status.get("status") or "").upper() in {"UPLOADING", "PENDING_REMOTE", "IMPORTED", "UPLOADED", "OZON_MODERATION", "ACTIVE"}
    )
    thumbnail_exists = any(path.is_file() for path in (product_dir / "input/main-images").glob("*"))
    return {
        "product_id": product_id,
        "title": source.get("title_cn") or "unknown",
        "thumbnail_url": f"/api/inbox/products/{product_id}/thumbnail" if thumbnail_exists else None,
        "sku_count": len(source.get("skus") or []),
        "status": status.get("status") or "unknown",
        "public_state": public_state(status.get("status")),
        "current_step": status.get("current_step") or "none",
        "submitted_to_ozon": submitted,
        "associated_shops": associated_shops(product_dir, status),
        "remote_ids": remote_ids,
        "remote_warning_required": submitted,
    }


@app.get("/workbench")
def workbench_page() -> FileResponse:
    trigger_image_cleanup()
    ensure_image_status_monitor()
    sync_remote_ozon_status_once()
    return FileResponse(STATIC_DIR / "workbench.html", media_type="text/html")


@app.get("/workbench.css")
def workbench_css() -> FileResponse:
    return FileResponse(STATIC_DIR / "workbench.css", media_type="text/css")


@app.get("/workbench.js")
def workbench_js() -> FileResponse:
    return FileResponse(STATIC_DIR / "workbench.js", media_type="application/javascript")


@app.get("/api/workbench/summary")
def workbench_summary() -> Dict[str, Any]:
    cards = [workbench_card(path) for path in owned_product_dirs() if (path / "status.json").is_file()]
    counts = {name: sum(1 for card in cards if card["state"] == name) for name in ("待处理", "处理中", "完成", "失败")}
    risks = sum(1 for card in cards if card["risk"]["level"] == "high")
    batch = get_batch_status()
    if risks:
        focus = {"type": "risk", "title": f"{risks} 个高风险商品需要处理", "action": "打开风险中心"}
    elif counts["待处理"] or counts["失败"]:
        focus = {"type": "review", "title": "继续处理商品资料", "action": "进入商品审核台"}
    elif batch.get("running"):
        focus = {"type": "batch", "title": "批次正在运行", "action": "查看批次中心"}
    else:
        focus = {"type": "inbox", "title": "采集箱等待新商品", "action": "打开采集箱"}
    return {"counts": counts, "high_risk_count": risks, "focus": focus, "batch": batch}


@app.get("/api/workbench/settings")
def get_workbench_settings() -> Dict[str, Any]:
    operator = current_operator()
    return {**workbench_settings(), "can_manage_settings": operator.get("role") == "owner"}


@app.get("/api/workbench/session")
def workbench_session() -> Dict[str, Any]:
    operator = current_operator()
    return {
        "operator": operator,
        "can_manage_settings": operator.get("role") == "owner",
        "product_visibility": "own_only",
    }


@app.get("/api/workbench/operators")
def workbench_operators() -> Dict[str, Any]:
    require_owner_role()
    return {"items": list_operators(ROOT), "product_visibility": "own_only"}


@app.post("/api/workbench/operators")
async def create_workbench_operator(request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="成员内容格式错误")
    try:
        item, one_time_code = upsert_operator(ROOT, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"created": True, "item": item, "one_time_access_code": one_time_code}


@app.patch("/api/workbench/operators/{operator_id}")
async def edit_workbench_operator(operator_id: str, request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="成员内容格式错误")
    try:
        item, one_time_code = upsert_operator(ROOT, payload, operator_id=operator_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": True, "item": item, "one_time_access_code": one_time_code}


@app.delete("/api/workbench/operators/{operator_id}")
def remove_workbench_operator(operator_id: str) -> Dict[str, Any]:
    operator = require_owner_role()
    if operator_id == operator.get("id"):
        raise HTTPException(status_code=422, detail="不能删除当前正在使用的成员")
    owned_count = sum(product_owner(path)["owner_id"] == operator_id for path in PRODUCTS_DIR.glob(WORKBENCH_PRODUCT_GLOB))
    if owned_count:
        raise HTTPException(status_code=409, detail=f"该成员还有{owned_count}个本地商品；请由成员自行处理或删除商品后再移除访问配置")
    try:
        delete_operator(ROOT, operator_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="成员不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"deleted": True, "operator_id": operator_id}


@app.patch("/api/workbench/settings")
async def update_workbench_settings(request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    if not isinstance(payload, dict) or set(payload) - {"auto_mode_enabled"}:
        raise HTTPException(status_code=422, detail="只允许修改全局自动模式开关")
    settings = save_workbench_settings(payload)
    return {**settings, "inventory_api_calls": 0, "ozon_write_api_calls": 0}


@app.get("/api/workbench/products")
def workbench_products(q: str = "", state: str = "", page: int = 1, page_size: int = 30) -> Dict[str, Any]:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    cards = [workbench_card(path) for path in owned_product_dirs() if (path / "status.json").is_file()]
    query = q.strip().lower()
    if query:
        cards = [card for card in cards if query in json.dumps(card, ensure_ascii=False).lower()]
    if state:
        cards = [card for card in cards if card["state"] == state]
    start = (page - 1) * page_size
    return {"items": cards[start:start + page_size], "total": len(cards), "page": page, "page_size": page_size}


@app.get("/api/workbench/products/{product_id}")
def workbench_product(product_id: str) -> Dict[str, Any]:
    return workbench_product_detail(product_id)


@app.get("/api/workbench/products/{product_id}/delete-preview")
def workbench_product_delete_preview(product_id: str) -> Dict[str, Any]:
    return workbench_delete_preview(product_id)


@app.delete("/api/workbench/products/{product_id}")
async def permanently_delete_workbench_product(product_id: str, request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict) or payload.get("confirm_product_id") != product_id or payload.get("permanent") is not True:
        raise HTTPException(status_code=422, detail="必须明确确认永久删除并提供商品ID")
    product_dir = PRODUCTS_DIR / product_id
    marker = deletion_marker_path(ROOT, product_id)
    if not product_dir.is_dir() and not marker.is_file():
        raise HTTPException(status_code=404, detail="商品不存在")
    result = purge_local_product(ROOT, product_id)
    if result["status"] != "deleted":
        raise HTTPException(status_code=500, detail={"message": "商品未完全删除，可重新执行清理", **result})
    return {
        **result,
        "message": f"商品 {product_id} 的本地资料已彻底删除。",
        "remote_ozon_unchanged": True,
    }


@app.get("/api/workbench/products/{product_id}/images/{slot}")
def workbench_image(product_id: str, slot: str, download: int = 0) -> FileResponse:
    product_dir = workbench_product_dir(product_id)
    plan = load_optional_json(product_dir / "output/image-plan.json")
    item = next((entry for entry in image_plan_items(plan) if str(entry.get("slot")) == slot), None)
    if not item:
        raise HTTPException(status_code=404, detail="图片不存在")
    image_path = (ROOT / str(item.get("output_path") or "")).resolve()
    if ROOT.resolve() not in image_path.parents or not image_path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(image_path, filename=image_path.name if download else None)


@app.get("/api/workbench/products/{product_id}/source-images/{image_type}/{index}")
def workbench_source_image(product_id: str, image_type: str, index: int) -> FileResponse:
    product_dir = workbench_product_dir(product_id)
    source = load_optional_json(product_dir / "input/source.json")
    if image_type == "sku":
        values = source.get("skus") or []
        if not 0 <= index < len(values):
            raise HTTPException(status_code=404, detail="SKU原图不存在")
        local_path = values[index].get("local_image_path") or values[index].get("variant_local_image_path")
    elif image_type in {"main", "detail"}:
        values = source.get("main_images" if image_type == "main" else "detail_images") or []
        if not 0 <= index < len(values):
            raise HTTPException(status_code=404, detail="1688原图不存在")
        local_path = values[index].get("local_path")
    else:
        raise HTTPException(status_code=404, detail="原图类型不存在")
    if not local_path or local_path == "unknown":
        raise HTTPException(status_code=404, detail="原图尚未缓存到本地")
    candidate = Path(str(local_path))
    image_path = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    try:
        image_path.relative_to(product_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="禁止读取商品目录以外的文件") from exc
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="本地原图文件不存在")
    return FileResponse(image_path, media_type=mimetypes.guess_type(image_path.name)[0] or "application/octet-stream")


@app.patch("/api/workbench/products/{product_id}/images/{slot}")
async def update_workbench_image(product_id: str, slot: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    plan_path = product_dir / "output/image-plan.json"
    plan = load_optional_json(plan_path)
    source_key, item = find_image_plan_item(plan, slot)
    payload = await request.json()
    action = str(payload.get("action") or "") if isinstance(payload, dict) else ""
    if action == "set_role":
        role = str(payload.get("role") or "")
        target_key = {"main": "main_images", "detail": "detail_images", "disclaimer": "disclaimer_images", "color_sample": "color_samples"}.get(role)
        if not target_key:
            raise HTTPException(status_code=422, detail="不支持的图片角色")
        plan[source_key] = [entry for entry in plan.get(source_key) or [] if entry is not item]
        item["image_type"] = role
        plan.setdefault(target_key, []).append(item)
    elif action == "move":
        direction = -1 if str(payload.get("direction")) == "up" else 1
        ordered = image_plan_items(plan)
        index = next(index for index, entry in enumerate(ordered) if str(entry.get("slot")) == slot)
        target = max(0, min(len(ordered) - 1, index + direction))
        ordered[index], ordered[target] = ordered[target], ordered[index]
        for order, entry in enumerate(ordered):
            entry["workbench_order"] = order
    elif action == "reorder":
        requested = [str(value) for value in payload.get("order") or []]
        ordered = image_plan_items(plan)
        existing = [str(entry.get("slot")) for entry in ordered]
        if len(requested) != len(existing) or set(requested) != set(existing):
            raise HTTPException(status_code=422, detail="图片排序列表与当前图片不一致")
        by_slot = {str(entry.get("slot")): entry for entry in ordered}
        for order, requested_slot in enumerate(requested):
            by_slot[requested_slot]["workbench_order"] = order
    elif action == "keep":
        item["kept_at"] = now_iso()
        item["kept_by"] = current_operator_id()
    else:
        raise HTTPException(status_code=422, detail="不支持的图片操作")
    atomic_write_json(plan_path, plan)
    learning = record_image_feedback(
        ROOT, product_dir, item, action, now_iso(),
        threshold=int(workbench_settings()["learning_threshold"]),
    )
    append_log(product_dir, "workbench_image_updated", {"slot": slot, "action": action})
    return {"saved": True, "slot": slot, "action": action, "learning": learning, "write_api_calls": 0, "inventory_api_calls": 0}


@app.put("/api/workbench/products/{product_id}/images/{slot}/content")
async def replace_workbench_image(product_id: str, slot: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    plan = load_optional_json(product_dir / "output/image-plan.json")
    _, item = find_image_plan_item(plan, slot)
    payload = await request.json()
    encoded = str(payload.get("data_url") or "") if isinstance(payload, dict) else ""
    match = re.fullmatch(r"data:(image/(?:jpeg|png|webp));base64,(.+)", encoded, flags=re.DOTALL)
    if not match:
        raise HTTPException(status_code=422, detail="只支持JPEG、PNG或WebP图片")
    try:
        content = base64.b64decode(match.group(2), validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="图片内容无效") from exc
    if not content or len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="图片大小必须在1字节到20MB之间")
    image_path = (ROOT / str(item.get("output_path") or "")).resolve()
    if ROOT.resolve() not in image_path.parents:
        raise HTTPException(status_code=422, detail="图片路径不安全")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(content)
    item.update({"status": "replaced", "replaced_at": now_iso(), "replaced_by": current_operator_id()})
    atomic_write_json(product_dir / "output/image-plan.json", plan)
    record_image_feedback(
        ROOT, product_dir, item, "replace", now_iso(),
        threshold=int(workbench_settings()["learning_threshold"]),
    )
    append_log(product_dir, "workbench_image_replaced", {"slot": slot, "bytes": len(content)})
    return {"saved": True, "slot": slot, "bytes": len(content), "write_api_calls": 0, "inventory_api_calls": 0}


@app.delete("/api/workbench/products/{product_id}/images/{slot}")
def delete_workbench_image(product_id: str, slot: str) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    plan_path = product_dir / "output/image-plan.json"
    plan = load_optional_json(plan_path)
    _, item = find_image_plan_item(plan, slot)
    image_path = (ROOT / str(item.get("output_path") or "")).resolve()
    if ROOT.resolve() in image_path.parents:
        image_path.unlink(missing_ok=True)
    record_image_feedback(
        ROOT, product_dir, item, "delete", now_iso(),
        threshold=int(workbench_settings()["learning_threshold"]),
    )
    item.update({"status": "deleted", "deleted_at": now_iso(), "output_path": "unknown"})
    atomic_write_json(plan_path, plan)
    append_log(product_dir, "workbench_image_deleted", {"slot": slot})
    return {"deleted": True, "slot": slot, "write_api_calls": 0, "inventory_api_calls": 0}


@app.patch("/api/workbench/products/{product_id}/draft")
async def save_workbench_draft(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="草稿内容格式错误")
    unknown_fields = set(payload) - WORKBENCH_EDITABLE_FIELDS
    if unknown_fields:
        raise HTTPException(status_code=422, detail=f"不支持编辑：{', '.join(sorted(unknown_fields))}")
    if "tags" in payload:
        tags = payload["tags"]
        if not isinstance(tags, list):
            raise HTTPException(status_code=422, detail="标签必须逐条保存")
        normalized = []
        for value in tags:
            tag = str(value).strip()
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = f"#{tag}"
            if len(tag) > 30:
                raise HTTPException(status_code=422, detail=f"标签不能超过30个字符：{tag}")
            if tag not in normalized:
                normalized.append(tag)
        payload["tags"] = normalized[:30]
    if "sku_overrides" in payload:
        if not isinstance(payload["sku_overrides"], dict):
            raise HTTPException(status_code=422, detail="SKU价格修改格式错误")
        settings = workbench_settings()
        rate = float(settings["fixed_cny_to_rub"])
        rounding = max(1, int(settings["rub_rounding"]))
        normalized_overrides: Dict[str, Dict[str, Any]] = {}
        for sku_id, raw_values in payload["sku_overrides"].items():
            if not isinstance(raw_values, dict):
                raise HTTPException(status_code=422, detail="SKU价格修改格式错误")
            values = dict(raw_values)
            if "selling_price_cny" in values:
                try:
                    cny = round(float(values["selling_price_cny"]), 2)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status_code=422, detail="人民币售价必须是数字") from exc
                if cny <= 0:
                    raise HTTPException(status_code=422, detail="人民币售价必须大于0")
                values["selling_price_cny"] = cny
                values["selling_price_rub"] = int(round((cny * rate) / rounding) * rounding)
            normalized_overrides[str(sku_id)] = values
        payload["sku_overrides"] = normalized_overrides
    draft_path = product_dir / "output/workbench-draft.json"
    before = load_optional_json(draft_path)
    changed = [field for field, value in payload.items() if before.get(field) != value]
    draft = dict(before)
    for field, value in payload.items():
        if field in {"attributes", "sku_overrides", "image_prompts"} and isinstance(value, dict):
            draft[field] = {**(draft.get(field) or {}), **value}
        else:
            draft[field] = value
    draft.update({
        "schema_version": "1.0.0", "product_id": product_id,
        "version": int(before.get("version") or 0) + 1, "saved_at": now_iso(),
        "modified_by": current_operator_id(),
        "locked_fields": sorted(set(before.get("locked_fields") or []) | set(changed)),
        "dirty": True,
    })
    atomic_write_json(draft_path, draft)
    history_path = product_dir / "output/workbench-versions.json"
    history = load_optional_json(history_path, {"product_id": product_id, "versions": []})
    history.setdefault("versions", []).append({
        "version": draft["version"], "saved_at": draft["saved_at"],
        "modified_by": current_operator_id(), "changed_fields": changed,
        "before": {field: before.get(field) for field in changed},
        "after": {field: draft.get(field) for field in changed},
    })
    history["versions"] = history["versions"][-50:]
    atomic_write_json(history_path, history)
    learning = record_workbench_edits(
        ROOT, product_dir, payload, draft["saved_at"],
        threshold=int(workbench_settings()["learning_threshold"]),
    )
    append_log(product_dir, "workbench_draft_saved", {"version": draft["version"], "changed_fields": changed})
    return {
        "saved": True, "version": draft["version"], "saved_at": draft["saved_at"],
        "locked_fields": draft["locked_fields"], "learning": learning,
    }


@app.put("/api/workbench/products/{product_id}/visual-preference")
async def save_visual_preference(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="图片风格意见格式错误")
    hint = str(payload.get("set_hint") or "").strip()
    if len(hint) > 120:
        raise HTTPException(status_code=422, detail="整套图片风格意见不能超过120个字符")
    raw_slot_hints = payload.get("slot_hints") or {}
    if not isinstance(raw_slot_hints, dict):
        raise HTTPException(status_code=422, detail="单图意见格式错误")
    slot_hints = {
        str(slot): str(value).strip()[:200]
        for slot, value in raw_slot_hints.items()
        if str(slot).strip() and str(value).strip()
    }
    value = {
        "schema_version": "1.0.0", "product_id": product_id,
        "set_hint": hint, "slot_hints": slot_hints,
        "updated_at": now_iso(), "updated_by": current_operator_id(),
    }
    atomic_write_json(product_dir / "input/visual-preference.json", value)
    status_path = product_dir / "status.json"
    status = load_optional_json(status_path)
    remote_states = {"UPLOADING", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE"}
    invalidated: List[str] = []
    if str(status.get("status") or "").upper() not in remote_states and int(status.get("api_write_count") or 0) == 0:
        reset_steps = {
            "style_selector", "image_plan", "image_generation", "image_qc",
            "marketplace_content", "field_completion", "ozon_upload",
        }
        completed = list(status.get("completed_steps") or [])
        status["completed_steps"] = [step for step in completed if step not in reset_steps]
        invalidated = [step for step in completed if step in reset_steps]
        pipeline_steps = [
            "validate_source", "product_analysis", "category_match", "variant_rules", "measurements",
            "offer_exists_check", "upload_feasibility", "product_positioning", "russian_copy",
            "style_selector", "image_plan", "image_generation", "image_qc", "marketplace_content",
            "field_completion", "ozon_upload",
        ]
        status["pending_steps"] = [step for step in pipeline_steps if step not in status["completed_steps"]]
        status["next_action"] = status["pending_steps"][0] if status["pending_steps"] else "complete"
        if status.get("status") not in {"COLLECTED", "FAILED_HARD_BLOCKER"}:
            status["status"] = "STOPPED"
        status["task_authorized"] = False
        atomic_write_json(status_path, status)
    append_log(product_dir, "visual_preference_saved", {"set_hint": hint, "slot_hint_count": len(slot_hints), "invalidated": invalidated})
    return {"saved": True, "preference": value, "invalidated_steps": invalidated}


@app.get("/api/workbench/notifications")
def workbench_notifications() -> Dict[str, Any]:
    items = []
    for product_dir in owned_product_dirs():
        if not (product_dir / "status.json").is_file():
            continue
        source = load_optional_json(product_dir / "input/source.json")
        status = load_optional_json(product_dir / "status.json")
        question = pending_product_question(product_dir)
        if question:
            items.append({
                "id": f"question:{product_dir.name}:{question.get('question_id') or 'current'}",
                "type": "question", "product_id": product_dir.name,
                "title": "商品需要你回答一个问题",
                "message": str(question.get("question") or "请确认商品关键信息"),
                "product_title": source.get("title_cn") or product_dir.name,
                "created_at": question.get("created_at") or "unknown",
                "requires_action": True,
            })
            continue
        raw_status = str(status.get("status") or "").upper()
        if raw_status == "FAILED_HARD_BLOCKER":
            items.append({
                "id": f"failure:{product_dir.name}:{status.get('last_run_at') or status.get('completed_at') or 'current'}",
                "type": "failure", "product_id": product_dir.name,
                "title": "商品任务需要处理",
                "message": str(status.get("error_message") or "任务失败，请查看原因后继续"),
                "product_title": source.get("title_cn") or product_dir.name,
                "created_at": status.get("last_run_at") or "unknown",
                "requires_action": True,
            })
        elif raw_status == "OZON_READY":
            items.append({
                "id": f"review:{product_dir.name}:{status.get('last_run_at') or 'current'}",
                "type": "review", "product_id": product_dir.name,
                "title": "商品已经生成完成",
                "message": "请检查商品资料和图片，然后选择店铺上传",
                "product_title": source.get("title_cn") or product_dir.name,
                "created_at": status.get("last_run_at") or "unknown",
                "requires_action": True,
            })
    return {"items": items, "count": len(items), "owner_id": current_operator_id()}


@app.post("/api/workbench/products/{product_id}/question/answer")
async def answer_product_question(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    question_path = product_dir / "input/pending-question.json"
    question = pending_product_question(product_dir)
    if not question:
        raise HTTPException(status_code=409, detail="当前商品没有等待回答的问题")
    payload = await request.json()
    answer = str((payload or {}).get("answer") or "").strip() if isinstance(payload, dict) else ""
    if not answer or len(answer) > 1000:
        raise HTTPException(status_code=422, detail="回答必须为1至1000个字符")
    guidance_path = product_dir / "input/operator-guidance.json"
    guidance = load_optional_json(guidance_path, {"schema_version": "1.0.0", "product_id": product_id, "answers": []})
    guidance.setdefault("answers", []).append({
        "question_id": question.get("question_id") or "current",
        "question": question.get("question") or "unknown",
        "answer": answer, "answered_at": now_iso(), "answered_by": current_operator_id(),
    })
    atomic_write_json(guidance_path, guidance)
    question.update({"status": "ANSWERED", "answer": answer, "answered_at": now_iso(), "answered_by": current_operator_id()})
    atomic_write_json(question_path, question)
    status_path = product_dir / "status.json"
    status = load_optional_json(status_path)
    if str(status.get("status") or "").upper() == "FAILED_HARD_BLOCKER":
        status.update({
            "status": "STOPPED", "error_code": "unknown", "error_message": "unknown",
            "next_action": status.get("failed_step") if status.get("failed_step") not in {None, "", "unknown"} else "validate_source",
            "task_authorized": False, "last_run_at": now_iso(),
        })
        atomic_write_json(status_path, status)
    append_log(product_dir, "operator_question_answered", {"question_id": question.get("question_id"), "answered_by": current_operator_id()})
    return {"saved": True, "product_id": product_id, "next_action": "run"}


@app.put("/api/workbench/products/{product_id}/stores")
async def save_product_store_selection(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="店铺选择格式错误")
    selected = validate_target_stores(payload.get("store_ids") or [])
    data = select_stores(product_dir, selected, connected_store_ids(), payload.get("overrides") or {})
    append_log(product_dir, "target_stores_selected", {"store_ids": selected})
    return {"saved": True, "store_ids": selected, "publications": data, "summary": publication_summary(data)}


@app.post("/api/workbench/products/{product_id}/stores/{store_id}/retry")
def retry_failed_store(product_id: str, store_id: str) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    if running_batch_pid() is not None:
        raise HTTPException(status_code=409, detail="当前有任务正在运行，请完成或安全停止后再重试")
    if product_id in reserved_product_batches():
        raise HTTPException(status_code=409, detail="该商品已在任务队列中")
    if store_id not in connected_store_ids():
        raise HTTPException(status_code=409, detail="该店铺未启用或尚未验证")
    publications = load_publications(product_dir, [store_id])
    record = (publications.get("stores") or {}).get(store_id) or {}
    if str(record.get("status") or "") != "FAILED":
        raise HTTPException(status_code=409, detail="只允许重试明确失败的店铺")
    if not definitely_retryable(record):
        raise HTTPException(status_code=409, detail="该店铺状态不明确或已有远端任务，禁止重传")
    status_path = product_dir / "status.json"
    original_status = load_optional_json(status_path)
    status = dict(original_status)
    status.update({
        "status": "OZON_READY", "current_step": "field_completion", "progress": 95,
        "failed_step": "unknown", "error_code": "unknown", "error_message": "unknown",
        "next_action": "ozon_upload", "target_store_ids_for_run": [store_id],
        "task_authorized": True, "last_run_at": now_iso(),
    })
    status["completed_steps"] = [step for step in status.get("completed_steps") or [] if step != "ozon_upload"]
    status["pending_steps"] = list(dict.fromkeys([*(status.get("pending_steps") or []), "ozon_upload"]))
    atomic_write_json(status_path, status)
    try:
        batch = create_batch(ROOT, [product_id], target_store_ids=[store_id], auto_upload=True)
        save_batch_owner(batch["batch_id"])
        final_snapshot(product_dir, [store_id], batch["batch_id"])
        launched = launch_or_enqueue_batch(batch, "retry_failed_store")
    except Exception:
        atomic_write_json(status_path, original_status)
        raise
    append_log(product_dir, "failed_store_retry_started", {"store_id": store_id, "batch_id": batch["batch_id"]})
    return {
        **launched, "batch_id": batch["batch_id"], "product_id": product_id,
        "store_id": store_id, "write_api_calls": 0, "inventory_api_calls": 0,
    }


@app.post("/api/workbench/products/{product_id}/suggestions/{suggestion_id}")
async def handle_ai_suggestion(product_id: str, suggestion_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    payload = await request.json()
    action = str(payload.get("action") or "") if isinstance(payload, dict) else ""
    if action not in {"accept", "ignore", "mute_similar"}:
        raise HTTPException(status_code=422, detail="不支持的建议操作")
    path = product_dir / "output/ai-suggestions.json"
    data = load_optional_json(path, {"schema_version": "1.0.0", "product_id": product_id, "items": []})
    items = [item for item in data.get("items") or [] if item.get("id") != suggestion_id]
    items.append({"id": suggestion_id, "status": action, "updated_at": now_iso(), "updated_by": current_operator_id()})
    data.update({"items": items, "updated_at": now_iso()})
    atomic_write_json(path, data)
    append_log(product_dir, "ai_suggestion_action", {"suggestion_id": suggestion_id, "action": action})
    return {"saved": True, "suggestion_id": suggestion_id, "action": action}


@app.post("/api/workbench/products/{product_id}/images/{slot}/regenerate")
async def queue_single_image_regeneration(product_id: str, slot: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    plan = load_optional_json(product_dir / "output/image-plan.json")
    if not any(str(item.get("slot")) == slot for item in image_plan_items(plan)):
        raise HTTPException(status_code=404, detail="图片槽位不存在")
    payload = await request.json()
    prompt = str(payload.get("prompt") or "").strip() if isinstance(payload, dict) else ""
    draft_path = product_dir / "output/workbench-draft.json"
    draft = load_optional_json(draft_path)
    if prompt:
        draft.setdefault("image_prompts", {})[slot] = prompt
    draft.update({"schema_version": "1.0.0", "product_id": product_id, "version": int(draft.get("version") or 0) + 1, "saved_at": now_iso(), "dirty": True})
    atomic_write_json(draft_path, draft)
    request_path = product_dir / "output/image-regeneration-request.json"
    regeneration = load_optional_json(request_path)
    slots = regeneration_slot_names(regeneration.get("failed_slots"))
    if slot not in slots:
        slots.append(slot)
    atomic_write_json(request_path, {
        "product_id": product_id, "failed_slots": slots, "attempt": "manual",
        "reason": "用户在工作台请求单张重新生成", "requested_at": now_iso(),
        "preserve_passed_images": True, "prompt_overrides": draft.get("image_prompts") or {},
    })
    plan_item = next(item for item in image_plan_items(plan) if str(item.get("slot")) == slot)
    record_image_feedback(
        ROOT, product_dir, plan_item, "regenerate", now_iso(), prompt=prompt,
        threshold=int(workbench_settings()["learning_threshold"]),
    )
    append_log(product_dir, "single_image_regeneration_queued", {"slot": slot})
    return {"queued": True, "slot": slot, "message": "已加入单图重生成队列；不会重做其他图片"}


@app.post("/api/workbench/products/{product_id}/run")
async def run_single_workbench_product(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    raw = await request.body()
    payload = json.loads(raw) if raw else {}
    selected_stores = validate_target_stores(payload.get("store_ids") or [])
    auto_upload = bool(payload.get("auto_upload", False))
    status = load_optional_json(product_dir / "status.json")
    if status.get("status") == "PENDING_REMOTE":
        raise HTTPException(status_code=409, detail="Ozon仍在处理，禁止重复提交")
    if status.get("status") in {"UPLOADED", "OZON_MODERATION", "ACTIVE"}:
        raise HTTPException(status_code=409, detail="商品已提交；修改后应由现有UPDATE流程处理")
    if int(status.get("api_write_count") or 0) > 0 and (status.get("ozon") or {}).get("upload_status") not in {"failed", "not_started"}:
        raise HTTPException(status_code=409, detail="已有Ozon写入记录，当前状态不允许重试")
    with BATCH_QUEUE_LOCK:
        existing_batch_id = reserved_product_batches().get(product_id)
        if existing_batch_id:
            return {
                "status": "already_queued", "batch_id": existing_batch_id,
                "write_api_calls": 0, "inventory_api_calls": 0,
                "target_store_ids": selected_stores,
            }
        select_stores(product_dir, selected_stores, connected_store_ids(), payload.get("overrides") or {})
        materialize_active_experience(ROOT, product_dir, now_iso())
        batch = create_batch(ROOT, [product_id], target_store_ids=selected_stores, auto_upload=auto_upload)
        save_batch_owner(batch["batch_id"])
        if batch.get("product_count") != 1:
            raise HTTPException(status_code=409, detail="当前商品状态不允许进入任务")
        if auto_upload:
            final_snapshot(product_dir, selected_stores, batch["batch_id"])
            launched = launch_or_enqueue_batch(batch, "single_product")
        else:
            batch["status"] = "AWAITING_CONFIRMATION"
            batch["products"][0].update({"status": "AWAITING_CONFIRMATION", "current_step": "manual_confirmation"})
            atomic_write_json(batch_path(ROOT, batch["batch_id"]), batch)
            launched = {"status": "awaiting_confirmation", "batch_id": batch["batch_id"], "queue_position": 0}
    append_log(product_dir, "workbench_product_run", {"batch_id": batch["batch_id"], "launch_status": launched["status"]})
    return {
        "status": launched["status"], "batch_id": batch["batch_id"], "pid": launched.get("pid"),
        "queue_position": launched.get("queue_position", 0), "write_api_calls": 0,
        "inventory_api_calls": 0, "target_store_ids": selected_stores,
    }


@app.get("/api/workbench/batches")
def workbench_batches() -> Dict[str, Any]:
    items = []
    active_pid = running_batch_pid()
    current = load_optional_json(CURRENT_BATCH_PATH)
    queued_positions = {
        str(item.get("batch_id")): index
        for index, item in enumerate(load_workbench_run_queue().get("items") or [], start=1)
    }
    for path in sorted((ROOT / "batches").glob("B-*/batch.json"), reverse=True):
        batch = load_optional_json(path)
        if batch and batch_is_owned(str(batch.get("batch_id") or path.parent.name)):
            result = load_optional_json(path.with_name("batch-result.json"))
            batch["result"] = result
            batch["queue_position"] = queued_positions.get(str(batch.get("batch_id")), 0)
            batch["display_status"] = (
                "排队中"
                if batch["queue_position"]
                else
                "已中断"
                if batch.get("status") == "RUNNING" and not (
                    active_pid and current.get("batch_id") == batch.get("batch_id")
                )
                else batch.get("status")
            )
            items.append(batch)
    return {"items": items[:100], "running_pid": active_pid, "queued_count": len(queued_positions)}


@app.post("/api/workbench/batches/create")
async def create_workbench_batch(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="批次内容格式错误")
    selected_stores = validate_target_stores(payload.get("store_ids") or [])
    product_ids = payload.get("product_ids")
    if product_ids is not None and not isinstance(product_ids, list):
        raise HTTPException(status_code=422, detail="商品列表格式错误")
    overrides = payload.get("product_store_overrides") or {}
    with BATCH_QUEUE_LOCK:
        reserved = reserved_product_batches()
        requested_ids = product_ids or [path.name for path in collected_products(ROOT) if product_is_owned(path)]
        requested_ids = [
            str(value) for value in requested_ids
            if (PRODUCTS_DIR / str(value)).is_dir() and product_is_owned(PRODUCTS_DIR / str(value))
        ]
        available_ids = [str(value) for value in requested_ids if str(value) not in reserved]
        if not available_ids:
            return {
                "status": "already_queued" if requested_ids else "empty", "product_count": 0,
                "existing_batch_ids": sorted({reserved[str(value)] for value in requested_ids if str(value) in reserved}),
            }
        auto_upload = bool(workbench_settings()["auto_mode_enabled"])
        batch = create_batch(
            ROOT, available_ids, target_store_ids=selected_stores,
            auto_upload=auto_upload, product_store_overrides=overrides,
        )
        save_batch_owner(batch["batch_id"])
        for entry in batch["products"]:
            product_dir = workbench_product_dir(entry["product_id"])
            stores_for_product = entry.get("target_store_ids") or selected_stores
            select_stores(product_dir, stores_for_product, connected_store_ids())
            materialize_active_experience(ROOT, product_dir, now_iso())
            if auto_upload:
                final_snapshot(product_dir, stores_for_product, batch["batch_id"])
        if auto_upload:
            launched = launch_or_enqueue_batch(batch, "workbench_batch")
        else:
            batch["status"] = "AWAITING_CONFIRMATION"
            for entry in batch["products"]:
                entry.update({"status": "AWAITING_CONFIRMATION", "current_step": "manual_confirmation"})
            atomic_write_json(batch_path(ROOT, batch["batch_id"]), batch)
            launched = {"status": "awaiting_confirmation", "batch_id": batch["batch_id"], "queue_position": 0}
    return {
        **launched, "product_count": batch["product_count"], "target_store_ids": selected_stores,
        "auto_upload": batch["auto_upload"], "write_api_calls": 0, "inventory_api_calls": 0,
    }


@app.get("/api/workbench/batches/{batch_id}/confirmation")
def get_workbench_batch_confirmation(batch_id: str) -> Dict[str, Any]:
    require_owned_batch(batch_id)
    batch = load_optional_json(batch_path(ROOT, batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if batch.get("auto_upload"):
        raise HTTPException(status_code=409, detail="自动模式批次不需要人工确认")
    if batch.get("status") not in {"AWAITING_CONFIRMATION", "QUEUED"}:
        raise HTTPException(status_code=409, detail="当前批次已离开人工确认阶段")
    return build_batch_confirmation(batch)


def _positive_confirmation_number(value: Any, field_name: str) -> float:
    try:
        number_value = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field_name}必须是数字") from exc
    if number_value <= 0:
        raise HTTPException(status_code=422, detail=f"{field_name}必须大于0")
    return round(number_value, 2)


def _confirmed_dimensions(value: Any, field_name: str) -> Dict[str, float]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail=f"{field_name}格式错误")
    return {
        key: _positive_confirmation_number(value.get(key), f"{field_name}{label}")
        for key, label in (("length", "长"), ("width", "宽"), ("height", "高"))
    }


@app.post("/api/workbench/batches/{batch_id}/confirm")
async def confirm_workbench_batch(batch_id: str, request: Request) -> Dict[str, Any]:
    require_owned_batch(batch_id)
    payload = await request.json()
    confirmations = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(confirmations, list):
        raise HTTPException(status_code=422, detail="批量确认内容格式错误")
    by_product = {str(item.get("product_id")): item for item in confirmations if isinstance(item, dict)}
    with BATCH_QUEUE_LOCK:
        batch_file = batch_path(ROOT, batch_id)
        batch = load_optional_json(batch_file)
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        if batch.get("status") != "AWAITING_CONFIRMATION":
            raise HTTPException(status_code=409, detail="批次已确认或已经启动，禁止重复确认")
        if batch.get("auto_upload"):
            raise HTTPException(status_code=409, detail="自动模式批次不需要人工确认")
        expected = batch_product_ids(batch)
        if set(by_product) != set(expected):
            raise HTTPException(status_code=422, detail="必须一次确认本批次的全部商品")
        normalized: Dict[str, Dict[str, Any]] = {}
        for product_id in expected:
            item = by_product[product_id]
            fields = item.get("fields") or {}
            product_dimensions = _confirmed_dimensions(fields.get("product_dimensions"), "商品尺寸")
            package_dimensions = _confirmed_dimensions(fields.get("package_dimensions"), "包装尺寸")
            product_weight = _positive_confirmation_number(fields.get("product_weight_g"), "商品净重")
            package_weight = _positive_confirmation_number(fields.get("package_weight_g"), "包装重量")
            if package_weight <= product_weight:
                raise HTTPException(status_code=422, detail=f"{product_id}：包装重量必须大于商品净重")
            if any(package_dimensions[key] <= product_dimensions[key] for key in ("length", "width", "height")):
                raise HTTPException(status_code=422, detail=f"{product_id}：包装长宽高必须分别大于商品长宽高")
            material = str(fields.get("material") or "unknown").strip() or "unknown"
            sku_prices = item.get("sku_prices") or {}
            source = load_optional_json(workbench_product_dir(product_id) / "input/source.json")
            expected_skus = {str(sku.get("sku_id")) for sku in source.get("skus") or []}
            if set(str(key) for key in sku_prices) != expected_skus:
                raise HTTPException(status_code=422, detail=f"{product_id}：必须确认全部SKU的人民币进价")
            normalized_prices = {
                str(sku_id): _positive_confirmation_number(value, f"{product_id} SKU {sku_id}进价")
                for sku_id, value in sku_prices.items()
            }
            normalized[product_id] = {
                "schema_version": "1.0.0",
                "product_id": product_id,
                "batch_id": batch_id,
                "confirmed_at": now_iso(),
                "confirmed_by": "workbench_manual_batch_confirmation",
                "fields": {
                    "product_dimensions": {**product_dimensions, "unit": "cm"},
                    "product_weight": {"value_g": product_weight},
                    "package_dimensions": {**package_dimensions, "unit": "cm"},
                    "package_weight": {"value_g": package_weight},
                    "material": material,
                },
                "sku_purchase_prices_cny": normalized_prices,
                "provenance": "estimated_human_approved",
                "inventory_submission_enabled": False,
            }
        for entry in batch["products"]:
            product_id = str(entry["product_id"])
            product_dir = workbench_product_dir(product_id)
            atomic_write_json(product_dir / "input/manual-confirmation.json", normalized[product_id])
            stores_for_product = entry.get("target_store_ids") or batch.get("target_store_ids") or []
            final_snapshot(product_dir, stores_for_product, batch_id)
            append_log(product_dir, "manual_batch_confirmation_saved", {
                "batch_id": batch_id,
                "confirmed_fields": ["product_dimensions", "product_weight", "package_dimensions", "package_weight", "material", "sku_purchase_prices_cny"],
            })
            entry.update({"status": "QUEUED", "current_step": "queue"})
        batch.update({"status": "QUEUED", "confirmed_at": now_iso(), "confirmation_count": len(normalized)})
        atomic_write_json(batch_file, batch)
        launched = launch_or_enqueue_batch(batch, "manual_confirmation")
    return {
        **launched,
        "product_count": batch.get("product_count", 0),
        "confirmed_product_count": len(normalized),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


@app.post("/api/workbench/batches/control")
async def control_batch(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    action = str(payload.get("action") or "") if isinstance(payload, dict) else ""
    pid = running_batch_pid()
    if action == "cancel_confirmation":
        batch_id = str(payload.get("batch_id") or "")
        require_owned_batch(batch_id)
        batch_file = batch_path(ROOT, batch_id) if batch_id else None
        batch = load_optional_json(batch_file) if batch_file else {}
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        if batch.get("status") != "AWAITING_CONFIRMATION":
            raise HTTPException(status_code=409, detail="只有尚未确认、尚未启动的批次可以直接取消")
        batch.update({"status": "CANCELLED", "cancelled_at": now_iso(), "cancel_reason": "user_cancelled_before_generation"})
        for entry in batch.get("products") or []:
            entry.update({"status": "CANCELLED", "current_step": "cancelled_before_generation"})
            product_dir = workbench_product_dir(str(entry.get("product_id")))
            append_log(product_dir, "manual_confirmation_batch_cancelled", {"batch_id": batch_id})
        atomic_write_json(batch_file, batch)
        return {
            "status": "cancelled", "batch_id": batch_id,
            "message": "本次任务已取消，商品仍保留在采集箱，可以重新运行",
            "write_api_calls": 0, "inventory_api_calls": 0,
        }
    if action == "retry_failed":
        if pid is not None:
            raise HTTPException(status_code=409, detail="当前批次仍在运行")
        failed = [
            path.name for path in retryable_products(ROOT)
            if product_is_owned(path) and load_optional_json(path / "status.json").get("status") == "FAILED_HARD_BLOCKER"
        ]
        if not failed:
            return {"status": "empty", "message": "没有可重试的失败商品"}
        selected_stores = validate_target_stores(payload.get("store_ids") or [])
        batch = create_batch(ROOT, failed, target_store_ids=selected_stores, auto_upload=bool(payload.get("auto_upload", False)))
        save_batch_owner(batch["batch_id"])
        for product_id in failed:
            select_stores(workbench_product_dir(product_id), selected_stores, connected_store_ids())
        launched = launch_or_enqueue_batch(batch, "retry_failed")
        return {**launched, "batch_id": batch["batch_id"], "product_count": len(failed)}
    if pid is None:
        raise HTTPException(status_code=409, detail="当前没有运行中的批次")
    if action == "stop":
        current = load_optional_json(CURRENT_BATCH_PATH)
        require_owned_batch(str(current.get("batch_id") or ""))
        atomic_write_json(SAFE_STOP_REQUEST_PATH, {
            "batch_id": current.get("batch_id"), "pid": pid,
            "requested_at": now_iso(), "mode": "interrupt_active_child_preserve_checkpoints",
        })
        return {
            "status": "stopping_safely", "pid": pid,
            "message": "正在安全停止当前子任务；已经逐张保存的图片保留，未完成图片不再继续生成",
        }
    signals = {"pause": signal.SIGSTOP, "continue": signal.SIGCONT}
    if action not in signals:
        raise HTTPException(status_code=422, detail="不支持的批次操作")
    current = load_optional_json(CURRENT_BATCH_PATH)
    require_owned_batch(str(current.get("batch_id") or ""))
    os.kill(pid, signals[action])
    return {"status": action, "pid": pid}


@app.get("/api/workbench/risks")
def workbench_risks() -> Dict[str, Any]:
    items = []
    for path in owned_product_dirs():
        if not (path / "status.json").is_file():
            continue
        detail = workbench_product_detail(path.name)
        for risk in detail["risk"]["items"]:
            items.append({"product_id": path.name, "title": detail["source"]["title_cn"], **risk})
    rules_path = ROOT / "config/workbench-risk-rules.json"
    rules = load_optional_json(rules_path, {
        "rules": [
            {"id": "product_truth", "name": "产品真实性", "action": "block", "immutable": True},
            {"id": "ozon_hard_rule", "name": "Ozon平台硬规则", "action": "block", "immutable": True},
            {"id": "duplicate_create", "name": "重复CREATE", "action": "block", "immutable": True},
            {"id": "inventory_api", "name": "库存接口", "action": "block", "immutable": True},
            {"id": "sku_merge", "name": "SKU错误合并", "action": "block", "immutable": True},
            {"id": "category_confidence", "name": "类目置信度偏低", "action": "review", "immutable": False},
        ]
    })
    return {"items": items, "rules": rules.get("rules") or []}


@app.patch("/api/workbench/risk-rules/{rule_id}")
async def update_workbench_risk_rule(rule_id: str, request: Request) -> Dict[str, Any]:
    require_owner_role()
    path = ROOT / "config/workbench-risk-rules.json"
    current = workbench_risks()["rules"]
    rule = next((item for item in current if item.get("id") == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="风险规则不存在")
    if rule.get("immutable"):
        raise HTTPException(status_code=422, detail="该硬规则永远禁止降级")
    payload = await request.json()
    action = str(payload.get("action") or "") if isinstance(payload, dict) else ""
    if action not in {"allow", "review", "block"}:
        raise HTTPException(status_code=422, detail="风险动作必须是自动通过、人工确认或禁止跳过")
    rule["action"] = action
    rule["updated_at"] = now_iso()
    atomic_write_json(path, {"schema_version": "1.0.0", "rules": current})
    return {"saved": True, "rule": rule, "write_api_calls": 0, "inventory_api_calls": 0}


@app.get("/api/workbench/shops")
def workbench_shops() -> Dict[str, Any]:
    registry = load_registry(ROOT)
    items = list_stores(ROOT)
    counts = {str(item.get("id")): {"associated": 0, "pending": 0} for item in items}
    for product_dir in owned_product_dirs():
        publications = load_publications(product_dir)
        for store_id, record in (publications.get("stores") or {}).items():
            if store_id not in counts:
                continue
            if record.get("selected") or str(record.get("status") or "") not in {"", "NOT_SELECTED"}:
                counts[store_id]["associated"] += 1
            if str(record.get("status") or "") in {"QUEUED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"}:
                counts[store_id]["pending"] += 1
    for item in items:
        item["associated_product_count"] = counts[str(item.get("id"))]["associated"]
        item["pending_task_count"] = counts[str(item.get("id"))]["pending"]
    return {"items": items, "default_shop": registry.get("default_read_shop")}


@app.post("/api/workbench/shops")
async def create_workbench_shop(request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    try:
        item = upsert_store(ROOT, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"created": True, "item": item, "write_api_calls": 0, "inventory_api_calls": 0}


@app.patch("/api/workbench/shops/{store_id}")
async def edit_workbench_shop(store_id: str, request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    try:
        item = upsert_store(ROOT, payload, store_id=store_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": True, "item": item, "write_api_calls": 0, "inventory_api_calls": 0}


@app.post("/api/workbench/shops/{store_id}/validate")
def validate_workbench_shop(store_id: str) -> Dict[str, Any]:
    require_owner_role()
    try:
        return validate_store_read_only(ROOT, store_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="店铺不存在") from exc


@app.post("/api/workbench/shops/{store_id}/enabled")
async def toggle_workbench_shop(store_id: str, request: Request) -> Dict[str, Any]:
    require_owner_role()
    payload = await request.json()
    try:
        item = set_enabled(ROOT, store_id, bool(payload.get("enabled")))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="店铺不存在") from exc
    return {"saved": True, "item": item, "write_api_calls": 0, "inventory_api_calls": 0}


@app.delete("/api/workbench/shops/{store_id}")
def delete_workbench_shop(store_id: str) -> Dict[str, Any]:
    require_owner_role()
    try:
        delete_store(ROOT, store_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="店铺不存在") from exc
    return {"deleted": True, "store_id": store_id, "remote_ozon_unchanged": True, "write_api_calls": 0, "inventory_api_calls": 0}


@app.get("/api/workbench/skills")
def workbench_skills() -> Dict[str, Any]:
    roots = [(ROOT / ".agents/skills", "项目"), (Path.home() / ".codex/skills", "本机")]
    items = []
    for skill_root, origin in roots:
        if not skill_root.is_dir():
            continue
        for skill_path in sorted(skill_root.glob("*/SKILL.md")):
            content = skill_path.read_text(encoding="utf-8", errors="ignore")
            items.append({"name": skill_path.parent.name, "source": origin, "enabled": True, "summary": next((line.strip("# ") for line in content.splitlines() if line.strip() and not line.startswith("---")), "本地Skill")})
    return {"items": items}


@app.get("/api/workbench/logs")
def workbench_logs(product_id: str = "") -> Dict[str, Any]:
    if product_id:
        detail = workbench_product_detail(product_id)
        return {"items": detail["timeline"]}
    items = []
    for path in owned_product_dirs():
        if not (path / "status.json").is_file():
            continue
        for entry in readable_timeline(load_optional_json(path / "status.json"), path)[:5]:
            items.append({"product_id": path.name, **entry})
    return {"items": sorted(items, key=lambda item: str(item.get("at") or ""), reverse=True)[:200]}


def export_directory() -> Path:
    path = ROOT / "logs/workbench-exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_export_cards() -> List[Dict[str, Any]]:
    cards = [workbench_card(path) for path in owned_product_dirs() if (path / "status.json").is_file()]
    return [{key: value for key, value in card.items() if key not in {"search_terms"}} for card in cards]


@app.get("/api/workbench/export/{export_type}")
def export_workbench(export_type: str) -> FileResponse:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = export_directory()
    cards = safe_export_cards()
    if export_type == "json":
        path = directory / f"products-{stamp}.json"
        atomic_write_json(path, {"exported_at": now_iso(), "items": cards, "secrets_included": False})
    elif export_type == "csv":
        path = directory / f"products-{stamp}.csv"
        fields = ["product_id", "title_cn", "title_ru", "source_url", "state", "raw_status", "sku_count", "purchase_price_cny", "image_count", "batch_id", "selected_store_count"]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for card in cards:
                writer.writerow({field: card.get(field) for field in fields})
    elif export_type == "xlsx":
        from openpyxl import Workbook
        path = directory / f"products-{stamp}.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "商品"
        fields = ["product_id", "title_cn", "title_ru", "source_url", "state", "sku_count", "purchase_price_cny", "image_count", "batch_id", "selected_store_count"]
        sheet.append(fields)
        for card in cards:
            sheet.append([card.get(field) for field in fields])
        workbook.save(path)
    elif export_type in {"backup", "migration"}:
        path = directory / f"crossborder-ai-factory-{export_type}-{stamp}.zip"
        included_roots = ["products", "batches", "config", "templates", "ozon-rules", "collector", "scripts"]
        excluded_names = {"__pycache__", ".pytest_cache", "node_modules", ".git", "workbench-exports"}
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative_root in included_roots:
                base = ROOT / relative_root
                if not base.exists():
                    continue
                for source in base.rglob("*"):
                    if not source.is_file() or any(part in excluded_names for part in source.parts):
                        continue
                    relative = source.relative_to(ROOT)
                    if relative.parts and relative.parts[0] == "products" and len(relative.parts) > 1:
                        product_dir = ROOT / "products" / relative.parts[1]
                        if product_dir.name != ".gitkeep" and product_dir.is_dir() and not product_is_owned(product_dir):
                            continue
                    if relative.parts and relative.parts[0] == "batches" and len(relative.parts) > 1:
                        batch_id = relative.parts[1]
                        if batch_id.startswith("B-") and not batch_is_owned(batch_id):
                            continue
                    if relative.as_posix() in {"config/lan-access.json", "config/operators.json"}:
                        continue
                    if source.name.startswith(".env") or source.suffix in {".log", ".pid"}:
                        continue
                    archive.write(source, source.relative_to(ROOT))
            archive.writestr("EXPORT_INFO.json", json.dumps({"created_at": now_iso(), "type": export_type, "secrets_included": False}, ensure_ascii=False, indent=2))
    else:
        raise HTTPException(status_code=404, detail="不支持的导出格式")
    return FileResponse(path, filename=path.name)


@app.get("/api/workbench/products/{product_id}/export-images")
def export_product_images(product_id: str) -> FileResponse:
    product_dir = workbench_product_dir(product_id)
    plan = load_optional_json(product_dir / "output/image-plan.json")
    path = export_directory() / f"{product_id}-images.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in image_plan_items(plan):
            image_path = (ROOT / str(item.get("output_path") or "")).resolve()
            if ROOT.resolve() in image_path.parents and image_path.is_file():
                archive.write(image_path, f"{item.get('slot')}{image_path.suffix.lower()}")
    return FileResponse(path, filename=path.name)
