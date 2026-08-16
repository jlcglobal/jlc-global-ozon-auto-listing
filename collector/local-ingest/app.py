from __future__ import annotations

import base64
import copy as copy_module
import csv
import hashlib
import html
import ipaddress
import json
import math
import mimetypes
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import zipfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
PRODUCTS_DIR = ROOT / "products"
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
COMMAND_CENTER_DIST_DIR = ROOT / "collector" / "workbench-command-center" / "dist"
COMMAND_CENTER_VERSION = "2026-08-16-ui-v4-bento-light"


def project_relative(path: Path) -> str:
    """Return a stable project-relative path across macOS /var and /private/var aliases."""
    return str(path.resolve().relative_to(ROOT.resolve()))


sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "pricing-engine"))
sys.path.insert(0, str(ROOT / "market-intelligence"))
sys.path.insert(0, str(ROOT / "finance-center"))
sys.path.insert(0, str(ROOT / "ozon-adapter"))
sys.path.insert(0, str(ROOT / "ozon-uploader"))
from pipeline_runtime import (  # noqa: E402
    PIPELINE_STEPS,
    batch_path,
    collected_products,
    create_batch,
    normalize_checkpoint,
    reconcile_completed_artifacts,
    retryable_products,
)
from image_cache_cleanup import cleanup_images  # noqa: E402
from image_asset_boundaries import (  # noqa: E402
    accept_candidate,
    asset_contract_path,
    asset_boundaries_enabled,
    asset_inventory,
    classify_path,
    invalidate_accepted_candidate,
    reject_candidate,
    validate_accepted_manifest,
    validate_generated_output,
    write_asset_contract,
)
from production_input_guard import (  # noqa: E402
    ProductionInputError,
    sha256_file,
    validate_formal_product_input,
    validate_registered_input_file,
    write_source_manifest,
)
from image_source_preflight import (  # noqa: E402
    ALLOWED_OZON_REFERENCE_IMAGE_HOST_SUFFIXES,
    build_preflight,
    open_source_image_url,
    read_source_image_response,
    source_image_candidates,
)
from sku_image_bindings import (  # noqa: E402
    available_binding_candidates,
    load_sku_image_bindings,
    save_sku_image_binding,
)
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
    read_secret,
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
from multi_store_upload import definitely_retryable, image_repair_retryable, refresh_pending_stores, variant_repair_retryable  # noqa: E402
from task_database import cutover_active, product_snapshot, product_snapshots  # noqa: E402
from collector_categories import (  # noqa: E402
    build_selection,
    category_tree_children,
    effective_tree_cache_path,
    get_category,
    load_translated_tree_cache,
    prepare_rules,
    public_preferences,
    recommend_categories,
    search_categories,
    set_favorite,
)
from ozon_metadata_prewarm import prewarm_category_tree  # noqa: E402
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
from russian_color_rules import normalize_russian_color_name  # noqa: E402
from russian_seo_rules import canonical_hashtag  # noqa: E402
from market_intelligence import (  # noqa: E402
    MarketEnricher,
    MarketStore,
    OzonAnalyticsApiError,
    OzonAnalyticsPermissionError,
    OzonAnalyticsReadOnlyClient,
    build_search_visibility_plan,
    build_traffic_performance_plan,
    collect_seller_search_visibility,
    normalize_seerfar_keyword_rows,
    normalize_yandex_wordstat_rows,
    parse_ozon_product_query_text,
    parse_yandex_wordstat_text,
)
from ozon_adapter import OzonReadOnlyClient  # noqa: E402
from ozon_adapter.config import OzonConfig  # noqa: E402
from ozon_uploader.client import OzonUploadApiError, OzonWriteClient  # noqa: E402
from finance_center import FinanceCenter  # noqa: E402

SCHEMA_VERSION = "1.0.0"
OZON_HASHTAG_ATTRIBUTE_ID = 23171
OZON_ANNOTATION_ATTRIBUTE_ID = 4191
OZON_RICH_CONTENT_ATTRIBUTE_ID = 11254
OZON_PRODUCT_COLOR_ATTRIBUTE_ID = 10096
OZON_COLOR_NAME_ATTRIBUTE_ID = 10097
OZON_COUNTRY_ATTRIBUTE_ID = 4389
OZON_CHINA_DICTIONARY_VALUE_ID = 90296
OZON_SUBJECT_TAG_MAX_BODY_LENGTH = 30
SEARCH_VISIBILITY_BLOCKED_TAG_FRAGMENTS = {
    "apple", "iphone", "ipad", "magsafe", "samsung", "xiaomi", "redmi", "huawei", "honor",
    "lenovo", "asus", "acer", "bosch", "philips", "dyson", "polaris", "vitek", "bork",
    "tohatsu", "nibbi", "dozawa", "collonil", "zenden", "ingco", "mindeo", "pantasy",
    "astroboy", "pirateflag", "happyhair", "natureza", "civitarese", "evoque", "felps",
    "copacabana", "leomax", "homeelement", "ксиоми", "сяоми", "хуавей", "айфон",
    "самсунг", "поларис", "витек", "борк", "нибби", "дозава", "зенден", "ингко",
    "миндео", "пантаси", "астробой", "леомакс", "скидк", "распродаж", "акци",
    "промокод", "дешев", "недорог", "лучший", "лучш", "топ", "хит", "премиум",
    "premium", "оригинал", "original", "официаль", "сертифик", "гарант", "возврат",
    "доставка", "магазин", "чат", "отзыв", "рекомендуем", "идеальн", "профессиональн",
    "качественн", "долговечн", "выгодн", "бренд",
}
SEARCH_VISIBILITY_INTRO_RISK_FRAGMENTS = {
    "скидк", "распродаж", "акци", "промокод", "дешев", "лучший", "лучш", "топ",
    "хит", "премиум", "premium", "оригинал", "original", "официаль", "сертифик",
    "гарант", "возврат", "доставка", "магазин", "чат", "отзыв", "рекомендуем",
    "бренд", "сервис", "импортер", "пишите", "обратиться", "негатив", "проблем",
}
MAX_SELECTED_SKUS_PER_PRODUCT = 10
ID_LOCK = threading.Lock()
BATCH_PID_PATH = ROOT / "logs/batch-runner.pid"
BATCH_LOG_PATH = ROOT / "logs/batch-runner.log"
CURRENT_BATCH_PATH = ROOT / "logs/current-batch.json"
WORKBENCH_RUN_QUEUE_PATH = ROOT / "logs/workbench-run-queue.json"
OZON_REFERENCE_TASKS_FILENAME = "ozon-reference-tasks.json"
SAFE_STOP_REQUEST_PATH = ROOT / "logs/safe-stop-request.json"
WORKBENCH_STOP_REQUEST_PATH = ROOT / "runtime/workbench-stop-requested"
REMOTE_STATUS_WORKER_PID_PATH = ROOT / "logs/remote-status-worker.pid"
DEVICE_ACTIVITY_LOG_PATH = ROOT / "logs/device-activity.jsonl"
WORKBENCH_SETTINGS_PATH = ROOT / "config/workbench-settings.json"
ATTRIBUTE_TRANSLATIONS_PATH = ROOT / "config/ozon-attribute-translations-zh.json"
LAN_ACCESS_CONFIG_PATH = ROOT / "config/lan-access.json"
PRICING_RULES_PATH = ROOT / "pricing-engine/pricing_rules.json"
MARKET_DB_PATH = ROOT / "market-intelligence/market.sqlite"
MARKET_CATEGORIES_PATH = ROOT / "market-intelligence/config/categories.json"
MARKET_TREND_REPORT_PATH = ROOT / "market-intelligence/reports/latest.json"
MARKET_IMAGE_CACHE_DIR = ROOT / "runtime/market-intelligence/images"
MARKET_SEARCH_VISIBILITY_PLAN_PATH = ROOT / "market-intelligence/reports/search-visibility-plan-latest.json"
MARKET_SEARCH_VISIBILITY_PLAN_CACHE_DIR = ROOT / "market-intelligence/reports/search-visibility-plans"
MARKET_SEARCH_VISIBILITY_UPLOAD_DIR = ROOT / "market-intelligence/reports/search-visibility-uploads"
MARKET_YANDEX_WORDSTAT_IMPORT_DIR = ROOT / "market-intelligence/reports/yandex-wordstat-imports"
MARKET_OZON_PRODUCT_QUERY_IMPORT_DIR = ROOT / "market-intelligence/reports/ozon-product-query-imports"
MARKET_SEERFAR_KEYWORD_IMPORT_DIR = ROOT / "market-intelligence/reports/seerfar-keyword-imports"
MARKET_SEERFAR_KEYWORD_JOBS_PATH = ROOT / "runtime/seerfar-keyword-jobs.json"
MARKET_TRAFFIC_PERFORMANCE_PLAN_PATH = ROOT / "market-intelligence/reports/traffic-performance-plan-latest.json"
FINANCE_CENTER = FinanceCenter(ROOT)
FINANCE_SCHEDULER_LOCK = threading.Lock()
FINANCE_SCHEDULER_STARTED = False
IMAGE_CLEANUP_THREAD_LOCK = threading.Lock()
BATCH_QUEUE_LOCK = threading.RLock()
SEERFAR_KEYWORD_JOB_LOCK = threading.RLock()
BATCH_DISPATCHER_LOCK = threading.Lock()
BATCH_DISPATCHER_WAKE = threading.Event()
BATCH_DISPATCHER_STARTED = False
OZON_REFERENCE_DISPATCHER_LOCK = threading.Lock()
OZON_REFERENCE_DISPATCHER_WAKE = threading.Event()
OZON_REFERENCE_DISPATCHER_STARTED = False
OZON_REFERENCE_CAPTURE_LIMIT = 2
OZON_REFERENCE_AI_DESIGN_LIMIT = 1
OZON_REFERENCE_IMAGE_WORKER_LOCK = threading.Lock()
OZON_REFERENCE_IMAGE_WORKERS: set[str] = set()
DEVICE_ACTIVITY_LOCK = threading.Lock()
DEFAULT_LAN_CIDRS = (
    "127.0.0.0/8", "::1/128", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12",
)
DEFAULT_WORKBENCH_SETTINGS = {
    "schema_version": "1.0.0",
    # A Run Task click is the only production authorization.  Once the user
    # has selected SKU/category/stores, the local pipeline must keep going
    # through the image channel and the selected-store submission itself.
    "auto_mode_enabled": True,
    "default_review_mode": "automatic",
    "learning_threshold": 2,
    "fixed_cny_to_rub": 12.0,
    "rub_rounding": 10,
}
TERMINAL_PUBLICATION_STATES = {
    "CREATED", "UPLOADED", "ACTIVE",
}
REMOTE_PENDING_PUBLICATION_STATES = {"SUBMITTED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION", "HANDED_OFF_TO_OZON"}
ATTENTION_STATES = {"NEEDS_ATTENTION", "FAILED"}

CURRENT_OPERATOR: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_workbench_operator", default=None)


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    # Test clients may patch ROOT and queue paths for only part of their
    # lifetime.  Starting persistent background threads there lets those
    # threads outlive the patch and write into the real workspace.  Production
    # startup still performs recovery and starts both local schedulers.
    if "pytest" not in sys.modules:
        recover_interrupted_batch()
        reconcile_priority_upload_queue()
        ensure_batch_dispatcher()
        ensure_ozon_reference_dispatcher()
        ensure_finance_scheduler()
        start_category_tree_refresh()
    yield


app = FastAPI(title="crossborder-ai-factory local ingest", version="0.2.0", lifespan=app_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"]
)


def start_category_tree_refresh() -> threading.Thread | None:
    """Refresh the official Ozon tree without delaying or blocking startup."""
    settings_path = ROOT / "config/pipeline-settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    if not settings.get("ozon_metadata_prewarm_enabled", True):
        return None
    registry = load_registry(ROOT)
    shop_id = str(settings.get("shop_name") or registry.get("default_read_shop") or "default")
    shop = next(
        (item for item in registry.get("shops") or [] if str(item.get("id") or item.get("name")) == shop_id),
        None,
    )
    if shop is None:
        return None
    values = {**os.environ, **read_secret(ROOT, shop)}
    if not values.get(str(shop.get("client_id_env"))) or not values.get(str(shop.get("api_key_env"))):
        return None

    def worker() -> None:
        log_path = ROOT / "logs/ozon-category-refresh.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            config = OzonConfig.from_shop(shop_id, ROOT / "ozon-adapter/shops.json", environ=values)
            client = OzonReadOnlyClient(config)
            result = prewarm_category_tree({**settings, "shop_name": shop_id}, root=ROOT, client=client)
            message = json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            # The checked-in official tree remains the safe offline fallback.
            message = json.dumps({
                "status": "bundled_fallback",
                "shop": shop_id,
                "reason": f"{type(exc).__name__}: {exc}",
                "ozon_write_api_calls": 0,
                "inventory_api_calls": 0,
            }, ensure_ascii=False)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"{now_iso()} {message}\n")

    thread = threading.Thread(target=worker, name="ozon-category-refresh", daemon=True)
    thread.start()
    return thread


def ensure_finance_scheduler() -> None:
    """Start the local 15:00 read-only finance sync with launch catch-up."""
    global FINANCE_SCHEDULER_STARTED
    # Finance has its own service boundary.  Never let opening the workbench
    # start a finance loop unless an operator explicitly opts in.
    if os.getenv("CAF_FINANCE_SCHEDULER", "0") == "0" or "pytest" in sys.modules:
        return
    with FINANCE_SCHEDULER_LOCK:
        if FINANCE_SCHEDULER_STARTED:
            return
        FINANCE_CENTER.recover_interrupted_syncs()
        FINANCE_CENTER.repair_invalid_ad_matches(apply=True, created_by="workbench_startup")
        FINANCE_SCHEDULER_STARTED = True

    def worker() -> None:
        while True:
            try:
                FINANCE_CENTER.scheduler_tick()
            except Exception:
                # Failure details are persisted in the finance sync ledger and shown in the UI.
                pass
            # The persistent scheduler guard decides whether an API batch is due.
            # A five-minute local status check avoids noisy DB churn while keeping
            # the daily 15:00 run close to its target time.
            threading.Event().wait(300)

    threading.Thread(target=worker, name="finance-center-scheduler", daemon=True).start()


def ensure_remote_status_worker() -> None:
    """Remote status polling is permanently disabled in the local workbench."""
    return None


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
    # Dispatchers can save the same queue concurrently. A shared `.tmp` name
    # lets one writer replace the other's file before it performs its rename.
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
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


def request_origin_is_trusted(request: Request) -> bool:
    """Allow the workbench and collector extension, never an arbitrary website."""
    origin = str(request.headers.get("origin") or "").strip()
    fetch_site = str(request.headers.get("sec-fetch-site") or "").strip().lower()
    if not origin:
        return fetch_site not in {"cross-site", "same-site"}
    try:
        parsed = urllib.parse.urlparse(origin)
        request_host = str(request.headers.get("host") or "").strip().lower()
        origin_host = str(parsed.netloc or "").strip().lower()
        if parsed.scheme in {"http", "https"} and request_host and origin_host == request_host:
            return True
        collector_request = request.url.path.startswith("/api/collector/")
        extension_origin = parsed.scheme in {"chrome-extension", "ms-browser-extension"}
        host = str(parsed.hostname or "").lower()
        trusted_1688_origin = parsed.scheme == "https" and (host == "1688.com" or host.endswith(".1688.com"))
        if extension_origin:
            return True
        return collector_request and trusted_1688_origin
    except ValueError:
        return False


def loopback_can_use_implicit_owner(request: Request) -> bool:
    """Backward-compatible name used by older tests and local integrations."""
    return request_origin_is_trusted(request)


def is_loopback_client(client_host: str) -> bool:
    host = str(client_host or "").split("%", 1)[0]
    if host in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def request_from_host_machine(request: Request, client_host: str) -> bool:
    """Recognize the host when it opens the service through its own LAN IP."""
    if is_loopback_client(client_host):
        return True
    server = request.scope.get("server")
    if not isinstance(server, (tuple, list)) or not server:
        return False
    client_value = str(client_host or "").split("%", 1)[0]
    server_value = str(server[0] or "").split("%", 1)[0]
    try:
        client_address = ipaddress.ip_address(client_value)
        server_address = ipaddress.ip_address(server_value)
    except ValueError:
        return False
    return not server_address.is_unspecified and client_address == server_address


def _safe_device_value(value: Any, *, limit: int = 80) -> str:
    text = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff ]+", "", str(value or "").strip())
    return text[:limit]


def automatic_device_operator(request: Request, client_host: str, *, loopback: bool) -> Dict[str, Any]:
    """Identify a LAN workstation automatically; no access code is required."""
    client_device_id = _safe_device_value(request.headers.get("X-Factory-Device-Id"), limit=96)
    if loopback:
        operator = dict(default_operator(ROOT))
        operator.update({
            "device_id": "host-main",
            "client_device_id": client_device_id or "host-main",
            "device_name": "主电脑",
            "client_ip": client_host,
            "is_host_device": True,
            "authentication": "automatic_device",
        })
        return operator
    network_id = hashlib.sha256(str(client_host).encode("utf-8")).hexdigest()[:12]
    requested_name = _safe_device_value(request.headers.get("X-Factory-Device-Name"))
    try:
        address = ipaddress.ip_address(str(client_host).split("%", 1)[0])
        short_host = str(address).split(".")[-1] if address.version == 4 else str(address)[-6:]
    except ValueError:
        short_host = network_id[:6]
    display_name = requested_name or f"工作室电脑 {short_host}"
    return {
        "id": f"device-{network_id}",
        "display_name": display_name,
        "role": "member",
        "enabled": True,
        "device_id": f"lan-{network_id}",
        "client_device_id": client_device_id or f"lan-{network_id}",
        "device_name": display_name,
        "client_ip": client_host,
        "is_host_device": False,
        "authentication": "automatic_device",
    }


def record_device_activity(request: Request, operator: Optional[Dict[str, Any]], status_code: int) -> None:
    """Keep a metadata-only audit trail for actions; never store request bodies or secrets."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or not request.url.path.startswith("/api/"):
        return
    DEVICE_ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": now_iso(),
        "method": request.method,
        "path": request.url.path,
        "status_code": int(status_code),
        "device_id": str((operator or {}).get("device_id") or "unknown"),
        "device_name": str((operator or {}).get("device_name") or (operator or {}).get("display_name") or "unknown"),
        "client_ip": str((operator or {}).get("client_ip") or "unknown"),
    }
    with DEVICE_ACTIVITY_LOCK:
        DEVICE_ACTIVITY_LOG_PATH.touch(mode=0o600, exist_ok=True)
        DEVICE_ACTIVITY_LOG_PATH.chmod(0o600)
        with DEVICE_ACTIVITY_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


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
    return bool(re.match(r"^(script-sku|dom-sku|dom-combo|combo-sku|local-spec-variant-offer-key)-", text, re.IGNORECASE))


def has_acceptable_sku_id(value: Any) -> bool:
    text = safe_text(value)
    return bool(text and text.lower() != "unknown" and not is_generated_sku_id(text))


def is_single_specification_sku(sku: Any) -> bool:
    """Accept the one deterministic offer key only when 1688 exposes no variants."""
    return (
        isinstance(sku, dict)
        and str(sku.get("sku_identity_type") or "") == "single_specification"
        and bool(re.fullmatch(
            r"(?:local-spec-single-offer-key|single-spec)-\d{6,}",
            str(sku.get("sku_id") or ""),
        ))
    )


def is_visible_variant_sku(sku: Any) -> bool:
    """Accept a page-visible variant only when the option and its own image were captured."""
    if not isinstance(sku, dict) or str(sku.get("sku_identity_type") or "") != "visible_variant":
        return False
    if not re.fullmatch(r"local-spec-variant-offer-key-\d{6,}-[A-Za-z0-9]+", str(sku.get("sku_id") or "")):
        return False
    if not safe_text(sku.get("image_url")) or str(sku.get("image_url")) == "unknown":
        return False
    option_values = sku.get("option_values")
    source_data = sku.get("source_data") if isinstance(sku.get("source_data"), dict) else {}
    return bool(
        isinstance(option_values, list)
        and option_values
        and source_data.get("identity_source") == "visible_sku_option"
    )


def capture_has_variant_evidence(payload: Dict[str, Any]) -> bool:
    groups = payload.get("sku_property_groups") or []
    if any(isinstance(group, dict) and (group.get("values") or []) for group in groups):
        return True
    raw = ((payload.get("raw_snapshot") or {}).get("all_raw_skus") or [])
    return any(isinstance(item, dict) for item in raw)


def materialize_single_specification_sku(payload: Dict[str, Any]) -> bool:
    """Create one traceable local SKU for a genuinely single-spec 1688 offer."""
    if payload.get("skus") or capture_has_variant_evidence(payload):
        return False
    match = re.search(r"/offer/(\d{6,})", str(payload.get("source_url") or ""))
    if not match:
        return False
    offer_id = match.group(1)
    payload["skus"] = [{
        "sku_id": f"local-spec-single-offer-key-{offer_id}",
        "sku_identity_type": "single_specification",
        "sku_name": "单规格",
        "option_values": [],
        "availability": "unknown",
        "sku_image_missing": True,
        "source_data": {"offer_id": offer_id, "identity_source": "1688_offer_url"},
    }]
    payload["selected_sku_ids"] = [f"local-spec-single-offer-key-{offer_id}"]
    payload.setdefault("capture_warnings", []).append("页面未提供SKU组合表，已按单规格商品采集。")
    return True


NON_SKU_SPEC_TEXT = re.compile(r"(?:sku\s*列表|¥|库存|起订量|套起批|\bunknown\b)", re.IGNORECASE)


def capture_sku_id(sku: Dict[str, Any]) -> Optional[str]:
    """Return the first real 1688 SKU/spec identifier from one captured row."""
    source_data = sku.get("source_data") if isinstance(sku.get("source_data"), dict) else {}
    for source in (sku, source_data):
        for key in (
            "sku_id", "skuId", "skuID", "sku_id_str", "skuIdStr",
            "specId", "specID", "spec_id", "spec_id_str", "specIdStr",
        ):
            value = safe_text(source.get(key))
            if has_acceptable_sku_id(value):
                return value
    return None


def has_non_sku_spec_text(sku: Dict[str, Any]) -> bool:
    values = [sku.get("sku_name")]
    for item in sku.get("option_values") or []:
        if not isinstance(item, dict):
            continue
        values.extend([
            item.get("name_cn"), item.get("name"), item.get("value_cn"),
            item.get("value"), item.get("source_text"),
        ])
    return any(NON_SKU_SPEC_TEXT.search(str(value or "")) for value in values)


def filter_collected_skus(payload: Dict[str, Any]) -> int:
    """Drop page fragments that are not real 1688 SKU records.

    This is deliberately performed at the local ingest boundary too, so an
    older extension cannot save an ``unknown``/price/stock pseudo-SKU.
    """
    def keep(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        sku_id = capture_sku_id(item)
        if (not sku_id and not is_single_specification_sku(item) and not is_visible_variant_sku(item)) or has_non_sku_spec_text(item):
            return False
        item["sku_id"] = sku_id or str(item["sku_id"])
        return True

    selected = payload.get("skus") if isinstance(payload.get("skus"), list) else []
    raw_snapshot = payload.get("raw_snapshot") if isinstance(payload.get("raw_snapshot"), dict) else {}
    raw = raw_snapshot.get("all_raw_skus") if isinstance(raw_snapshot.get("all_raw_skus"), list) else selected
    filtered_selected = [item for item in selected if keep(item)]
    filtered_raw = [item for item in raw if keep(item)]
    payload["skus"] = filtered_selected
    payload["selected_sku_ids"] = [str(item["sku_id"]) for item in filtered_selected]
    raw_snapshot["all_raw_skus"] = filtered_raw
    payload["raw_snapshot"] = raw_snapshot
    return len(selected) - len(filtered_selected)


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
        re.search(r"\.(svg|woff2?|ttf|otf)(?:$|[?#])", lowered)
        or re.search(r"(icon|logo|avatar|sprite|pay|payment|wangwang|qrcode|qr|loading|blank|grey)", lowered)
        or re.search(r"(?:^|[-_/])tps-\d{1,3}-\d{1,3}(?:[-_.]|$)", lowered)
    )


def normalize_ozon_reference_image_url(url: str) -> str:
    return re.sub(r"/(?:w[hc]|c)\d+/", "/wc1000/", str(url or ""), flags=re.IGNORECASE)


def is_disallowed_ozon_reference_image_url(url: str) -> bool:
    lowered = normalize_ozon_reference_image_url(url).lower()
    if is_disallowed_image_url(lowered):
        return True
    return bool(
        re.search(r"(ozon-fonts|marketing-api|banner|/cms/|/video-)", lowered)
        or not re.search(r"\.(jpe?g|png|webp|avif)(?:$|[?#])", lowered)
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


def decode_inline_image_data(data_url: Any) -> Tuple[bytes, str]:
    text = str(data_url or "").strip()
    match = re.fullmatch(r"data:(image/[A-Za-z0-9.+-]+);base64,(.+)", text, flags=re.DOTALL)
    if not match:
        raise ValueError("inline image data is not a base64 image data URL")
    content_type = match.group(1).lower()
    content = base64.b64decode(match.group(2), validate=True)
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("inline image data exceeds 8MB")
    return content, content_type


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_product_id() -> str:
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    with ID_LOCK:
        max_id = 0
        for path in PRODUCTS_DIR.glob("P[0-9][0-9][0-9][0-9][0-9][0-9]"):
            try:
                number = int(path.name[1:])
                if number < 900000:
                    max_id = max(max_id, number)
            except ValueError:
                continue
        for number in range(max_id + 1, 900000):
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
        "output/generated-images/variant-main",
        "output/generated-images/detail",
        "output/rejected-generation",
        "output/accepted-images",
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
    step_status = "completed" if status == "COLLECTED" else "failed" if status in ATTENTION_STATES else "in_progress"
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
            "validate_source", "product_analysis", "category_match", "variant_rules", "measurements",
            "offer_exists_check", "upload_feasibility", "product_positioning", "ecommerce_design", "russian_copy",
            "field_completion", "image_plan",
            "image_generation", "image_qc", "ozon_upload"
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


FIELD_DIAGNOSTIC_STRATEGIES = {
    "structured_json",
    "script_init_data",
    "dom_semantic",
    "candidate_selector",
    "text_inference",
    "local_ingest",
}


def coerce_field_diagnostics(items: Any) -> List[Dict[str, Any]]:
    diagnostics = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        strategy = str(item.get("strategy") or "")
        diagnostics.append(build_field_diagnostic(
            unknown_text(item.get("field")),
            strategy if strategy in FIELD_DIAGNOSTIC_STRATEGIES else "local_ingest",
            bool(item.get("hit")),
            item.get("failure_reason"),
            item.get("candidate_count") if isinstance(item.get("candidate_count"), int) else 0,
        ))
    return diagnostics


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


def download_url(
    url: str,
    timeout: int = 20,
    allowed_host_suffixes: Tuple[str, ...] | None = None,
) -> Tuple[bytes, Optional[str]]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 crossborder-ai-factory-collector/0.2",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
        }
    )
    with open_source_image_url(request, timeout=timeout, allowed_host_suffixes=allowed_host_suffixes) as response:
        content, content_type = read_source_image_response(response)
        return content, content_type


def download_image_group(
    image_inputs: Iterable[Dict[str, Any]],
    output_dir: Path,
    prefix: str,
    url_cache: Dict[str, Dict[str, Any]],
    hash_cache: Dict[str, Dict[str, Any]],
    warnings: List[str],
    allowed_host_suffixes: Tuple[str, ...] | None = None,
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
            if item.get("data_url"):
                content, content_type = decode_inline_image_data(item.get("data_url"))
            else:
                last_error: Exception | None = None
                for candidate_url in source_image_candidates(url):
                    try:
                        content, content_type = download_url(candidate_url, allowed_host_suffixes=allowed_host_suffixes)
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
    captured_at = str(payload["captured_at"])
    supplied_collection_id = str(payload.get("collection_id") or "").strip()
    collection_id = supplied_collection_id if re.fullmatch(r"COL-[A-Za-z0-9._-]{8,80}", supplied_collection_id) else (
        "COL-" + hashlib.sha256(f"{product_id}|{source_url}|{captured_at}".encode("utf-8")).hexdigest()[:20]
    )
    skus = []
    for index, sku in enumerate(payload.get("skus") or []):
        image_url = normalize_url(sku.get("image_url"), source_url)
        option_values = [coerce_attribute(item) for item in (sku.get("option_values") or []) if isinstance(item, dict)]
        purchase_price = sku.get("purchase_price") if isinstance(sku.get("purchase_price"), (int, float)) else None
        price_source = sku.get("price_source") if sku.get("price_source") in {"sku_specific_price", "price_range", "unknown"} else ("sku_specific_price" if purchase_price is not None else "unknown")
        skus.append(
            {
                "sku_id": unknown_text(sku.get("sku_id") or f"sku-{index + 1}"),
                "sku_identity_type": str(sku.get("sku_identity_type") or "1688_sku"),
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
        "collection_id": collection_id,
        "source_kind": "workbench_collection",
        "source_path": f"products/{product_id}/input/source.json",
        "collected_at": captured_at,
        "source_platform": "1688",
        "source_url": source_url,
        "captured_at": captured_at,
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
        "field_diagnostics": coerce_field_diagnostics(payload.get("field_diagnostics")) or [
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
    selected_category = payload.get("ozon_category_selection") if isinstance(payload.get("ozon_category_selection"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "page_url": payload["source_url"],
        "page_title": payload.get("page_title") or "unknown",
        "structured_data_summary": raw_snapshot.get("structured_data_summary", {}),
        "candidate_selectors": raw_snapshot.get("candidate_selectors", {}),
        "field_diagnostics": coerce_field_diagnostics(payload.get("field_diagnostics")),
        "original_image_urls": original_image_urls,
        "sku_raw_data": all_raw_skus,
        "sku_debug": sku_debug,
        "sku_property_image_debug": raw_snapshot.get("sku_property_image_debug") or {},
        # Preserve the exact category object posted by the extension.  The
        # normalized selection is written separately; keeping both makes any
        # client-side ID/label mismatch diagnosable instead of invisible.
        "submitted_ozon_category_selection": selected_category,
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
    original_selected_sku_count = len(payload.get("skus") or [])
    materialize_single_specification_sku(payload)
    filtered_sku_count = filter_collected_skus(payload)
    apply_shared_price_tier(payload)

    if "1688." not in urllib.parse.urlparse(payload["source_url"]).netloc:
        raise HTTPException(status_code=422, detail={"message": "Current page is not a supported 1688 product URL"})
    selected_skus = payload.get("skus") or []
    if not selected_skus:
        if original_selected_sku_count and filtered_sku_count:
            raise HTTPException(status_code=422, detail={"message": "未解析到真实 1688 SKU，请重新采集。"})
        raise HTTPException(status_code=422, detail={"message": "请至少选择1个SKU。"})
    if len(selected_skus) > MAX_SELECTED_SKUS_PER_PRODUCT:
        raise HTTPException(
            status_code=422,
            detail={"message": f"单个商品最多选择{MAX_SELECTED_SKUS_PER_PRODUCT}个SKU，请先取消其他SKU。"},
        )
    if any(sku.get("availability") == "out_of_stock" for sku in selected_skus if isinstance(sku, dict)):
        raise HTTPException(status_code=422, detail={"message": "所选SKU包含不可购买或无库存SKU。"})
    selected_missing_images = [
        str(sku.get("sku_id") or sku.get("sku_name") or f"sku-{index + 1}")
        for index, sku in enumerate(selected_skus)
        if isinstance(sku, dict) and (
            not normalize_url(sku.get("image_url"), payload["source_url"])
            or sku.get("sku_image_missing") is True
        )
    ]
    # A real 1688 SKU can legitimately have no dedicated image. Keep the
    # missing-image fact and let the later image-source preflight request an
    # explicit human mapping; collection itself must not discard the SKU or
    # block the whole product.
    sku_image_warnings = []
    if selected_missing_images:
        sku_image_warnings.append(
            f"所选SKU有{len(selected_missing_images)}个缺少1688专属图片；已保留真实缺图标记，生图前必须人工确认参考图。"
        )
    preflight = payload.get("sku_image_preflight")
    raw_skus = ((payload.get("raw_snapshot") or {}).get("all_raw_skus") or [])
    missing_all = []
    if isinstance(preflight, dict):
        missing_all = [
            str(sku.get("sku_id") or sku.get("sku_name") or f"sku-{index + 1}")
            for index, sku in enumerate(raw_skus)
            if isinstance(sku, dict) and (
                not normalize_url(sku.get("image_url"), payload["source_url"])
                or sku.get("sku_image_missing") is True
            )
        ]
        reported_missing = max(len(missing_all), int(preflight.get("missing_count") or 0))
        if reported_missing:
            sku_image_warnings.append(
                f"1688全部SKU中有{reported_missing}个没有专属图片；缺图SKU仍可选择和采集，系统不会自动借用其他SKU图片。"
            )
    missing_real_sku_ids = [
        sku.get("sku_name") or sku.get("sku_id") or f"sku-{index + 1}"
        for index, sku in enumerate(selected_skus)
        if isinstance(sku, dict) and not capture_sku_id(sku) and not is_single_specification_sku(sku) and not is_visible_variant_sku(sku)
    ]
    if missing_real_sku_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "所选SKU缺少真实1688 sku_id，也没有可验证的规格图，禁止保存伪造SKU ID。",
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
    warnings.extend(item for item in sku_image_warnings if item not in warnings)
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

        # Preserve the submitted SKU/image evidence even when normalized source
        # validation fails, so a recoverable diagnostic issue cannot erase the
        # collection needed to repair the product.
        atomic_write_json(product_dir / "input/raw-snapshot.json", raw_snapshot)

        source_errors = validate_json(source_json, "source.schema.json")
        if source_errors:
            raise ValueError("Generated source.json failed schema validation: " + "; ".join(source_errors))

        atomic_write_json(product_dir / "input/source.json", source_json)
        atomic_write_json(product_dir / "input/category-selection.json", category_selection)
        source_manifest = write_source_manifest(product_dir)
        manifest_errors = validate_json(source_manifest, "source-manifest.schema.json")
        if manifest_errors:
            raise ValueError("Generated source-manifest.json failed schema validation: " + "; ".join(manifest_errors))
        write_asset_contract(
            product_dir,
            collection_id=source_json["collection_id"],
            manual_confirmation_required=False,
        )

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
            "NEEDS_ATTENTION",
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
        "sku-run-snapshot.json",
        "attributes.json", "ozon-category.json", "ozon-category-tree.json",
        "ozon-category-attributes.json", "ozon-attributes.json", "ozon-preflight.json",
        "variant-decision.json", "variant-grouping-result.json", "platform-grouping-result.json",
        "category-variant-rule-audit.json", "image-plan.json",
        "image-qc-report.json", "title-ru.json", "description-ru.json", "keyword-research-ru.json",
        "ozon-tags.json", "ozon-attributes-final.json", "attribute-coverage-report.json",
        "attribute-fill-input.json", "attribute-fill-input.compact.json",
        "ozon-ecommerce-design.json",
        "copy-ru.json", "keyword-research-ru.json",
        "ozon-draft.json", "ozon-upload-config.json",
        "ozon-upload-payload.json", "ozon-upload-preflight.json", "final-submission-snapshot.json",
        "rich-content.json", "generated-images",
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
    source_manifest = write_source_manifest(product_dir)
    status.update({
        "status": "COLLECTED", "current_step": "collect_source", "progress": 100,
        "task_authorized": False, "batch_id": "unknown", "completed_steps": ["collect_source"],
        "failed_step": "unknown", "error_code": "unknown", "error_message": "unknown",
        "next_action": "wait_for_run_task", "api_write_count": 0, "active_step": None,
        # Category changes start a new local run. Do not let an old failed
        # attempt keep the UI in a misleading error/locked state.
        "warnings": [], "retry_count": 0, "retry_count_by_step": {},
        "steps": [{
            "name": "collect_source", "status": "completed", "retry_count": 0,
            "retryable": True, "error": None,
        }],
    })
    # A category/source change invalidates the batch source binding.  Keeping
    # the old binding makes validate_formal_product_input reject the product
    # even after the manifest has been correctly rebuilt.
    status.pop("source_snapshot_binding", None)
    status["pending_steps"] = [
        "validate_source", "product_analysis", "category_match", "variant_rules", "measurements",
        "offer_exists_check", "upload_feasibility", "product_positioning", "ecommerce_design", "russian_copy",
        "field_completion", "image_plan",
        "image_generation", "image_qc", "ozon_upload",
    ]
    status.setdefault("history", []).append({
        "from": previous_state, "to": "COLLECTED", "at": now_iso(),
        "reason": "User changed the final Ozon category; old attributes, image strategy and payloads were invalidated.",
    })
    atomic_write_json(status_path, status)
    append_log(product_dir, "collector_category_changed", {
        "previous_category_id": previous.get("category_id"),
        "category_id": selection["category_id"], "type_id": selection["type_id"],
        "invalidated": invalidated,
        "source_manifest_sha256": sha256_file(product_dir / "input/source-manifest.json"),
        "source_manifest_record_count": len(source_manifest.get("records") or []),
        "ozon_write_api_calls": 0, "inventory_api_calls": 0,
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
    host_device = request_from_host_machine(request, client_host)
    config = load_lan_access_config()
    if not loopback:
        if not config.get("enabled"):
            return JSONResponse(
                status_code=503,
                content={"detail": {"code": "LAN_ACCESS_DISABLED", "message": "主电脑尚未启用工作室局域网访问"}},
            )
        if not client_ip_allowed(client_host, config):
            return JSONResponse(status_code=403, content={"detail": "当前设备不在允许的工作室局域网内"})
    operator: Optional[Dict[str, Any]] = None
    if request.url.path.startswith("/api/") and request.method != "OPTIONS" and request.url.path != "/health":
        if not request_origin_is_trusted(request):
            return JSONResponse(
                status_code=403,
                content={"detail": {"code": "UNTRUSTED_ORIGIN", "message": "只允许工作台页面和1688采集插件访问"}},
            )
        operator = automatic_device_operator(request, client_host, loopback=host_device)
    token = CURRENT_OPERATOR.set(operator)
    request.state.operator = operator
    try:
        response = await call_next(request)
    finally:
        CURRENT_OPERATOR.reset(token)
    record_device_activity(request, operator, response.status_code)
    return response


def secrets_compare(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return hashlib.sha256(left.encode("utf-8")).digest() == hashlib.sha256(right.encode("utf-8")).digest()


def current_operator() -> Dict[str, Any]:
    operator = CURRENT_OPERATOR.get()
    if operator:
        return operator
    fallback = dict(default_operator(ROOT))
    fallback.update({
        "device_id": "host-main", "client_device_id": "host-main", "device_name": "主电脑",
        "client_ip": "127.0.0.1", "is_host_device": True, "authentication": "automatic_device",
    })
    return fallback


def current_operator_id() -> str:
    return str(current_operator().get("id") or DEFAULT_OPERATOR_ID)


def require_owner_role() -> Dict[str, Any]:
    operator = current_operator()
    if not operator.get("is_host_device"):
        raise HTTPException(status_code=403, detail="只有主电脑可以修改店铺和系统设置")
    return operator


def product_owner(product_dir: Path) -> Dict[str, Any]:
    value = load_optional_json(product_dir / "input/owner.json")
    return {
        "owner_id": str(value.get("owner_id") or DEFAULT_OPERATOR_ID),
        "owner_name": str(value.get("owner_name") or "工作室负责人"),
    }


def product_is_owned(product_dir: Path, operator_id: Optional[str] = None) -> bool:
    # Kept under the legacy function name so old callers remain compatible.
    # Product owner data is now audit metadata; all workstations share the queue.
    return product_dir.is_dir()


def product_is_archived(product_dir: Path) -> bool:
    """Keep archived recovery data on disk but hide it from active workbench flows."""
    # Archive state is persisted in the product status file.  Do not consult
    # the SQLite publication projection here: this helper is called while
    # enumerating every product for list and host-status polling.
    status = load_optional_json(product_dir / "status.json")
    return (
        str(status.get("status") or "").upper() == "ARCHIVED"
        or str(status.get("local_lifecycle_status") or "").upper() == "ARCHIVED"
    )


def owned_product_dirs() -> List[Path]:
    result: List[Path] = []
    for path in sorted(PRODUCTS_DIR.glob(WORKBENCH_PRODUCT_GLOB), reverse=True):
        if not path.is_dir() or not product_is_owned(path) or product_is_archived(path):
            continue
        try:
            if int(path.name[1:]) >= 900000:
                continue
        except ValueError:
            continue
        source = load_optional_json(path / "input/source.json")
        # Manual conversation fixtures are never formal workbench products.
        # Legacy unmarked products remain visible for migration/audit, but the
        # production gate below will not let them run.
        if source.get("source_kind") == "manual_test":
            continue
        result.append(path)
    return result


def save_product_owner(product_dir: Path, operator: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    operator = operator or current_operator()
    value = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "owner_id": str(operator.get("id") or DEFAULT_OPERATOR_ID),
        "owner_name": str(operator.get("display_name") or "工作室负责人"),
        "device_id": str(operator.get("device_id") or "unknown"),
        "device_name": str(operator.get("device_name") or operator.get("display_name") or "unknown"),
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
        "device_id": str(operator.get("device_id") or "unknown"),
        "device_name": str(operator.get("device_name") or operator.get("display_name") or "unknown"),
        "created_at": now_iso(),
    })


def batch_is_owned(batch_id: str) -> bool:
    # Owner sidecars are audit metadata; shared LAN workstations can continue any batch.
    return bool(batch_id) and batch_path(ROOT, batch_id).is_file()


def require_owned_batch(batch_id: str) -> None:
    if not batch_is_owned(batch_id):
        raise HTTPException(status_code=404, detail="任务不存在")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "collector-local-ingest", "root": str(ROOT)}


def image_host_status() -> Dict[str, Any]:
    settings = load_optional_json(ROOT / "config/pipeline-settings.json")
    configured_codex = Path(str(settings.get("codex_command") or ""))
    codex_ready = configured_codex.is_file() or shutil.which("codex") is not None
    workers = []
    # Image slots run as isolated child workers. Include them here so the
    # workbench never reports an idle host while a real image request is live.
    worker_paths = [
        *(ROOT / "logs/product-workers").glob("P*.json"),
        *(ROOT / "logs/image-slot-workers").glob("P*.json"),
    ]
    for worker_path in worker_paths:
        worker = load_optional_json(worker_path)
        if _pid_is_alive(worker.get("pid")):
            workers.append(worker)
        else:
            worker_path.unlink(missing_ok=True)
    recovering = []
    needs_attention = []
    for product_dir in PRODUCTS_DIR.glob("P[0-9][0-9][0-9][0-9][0-9][0-9]"):
        if product_is_archived(product_dir):
            continue
        # The host monitor only reports local worker recovery and image
        # failures, all of which are persisted in status.json. It must not
        # open the publication database once per product on every poll.
        status = load_optional_json(product_dir / "status.json")
        recovery_state = str(status.get("host_recovery_state") or "normal")
        if recovery_state == "recovering" and status.get("status") in {"PROCESSING", "QUEUED"}:
            recovering.append(product_dir.name)
        if (
            str(status.get("ai_service_state") or "normal") == "waiting_for_recovery"
            and status.get("status") in {"PROCESSING", "QUEUED"}
        ):
            recovering.append(product_dir.name)
        if (
            recovery_state == "needs_attention"
            or (
                status.get("status") in ATTENTION_STATES
                and status.get("failed_step") == "image_generation"
            )
        ):
            needs_attention.append({
                "product_id": product_dir.name,
                "error_message": str(status.get("error_message") or ""),
                "failed_step": str(status.get("failed_step") or ""),
                "host_recovery_state": recovery_state,
            })
    missing_reference_attention = [
        item for item in needs_attention
        if any(token in item["error_message"].casefold() for token in (
            "missing_required_sku_reference", "image_source_preflight_blocked", "缺少真实参考图",
            "no registered sku-bound real image", "has no registered sku reference",
        ))
    ]
    if not codex_ready or needs_attention:
        state = "needs_attention"
        label = "需要你处理"
        message = (
            "Codex生图服务不可用"
            if not codex_ready
            else "有已选SKU缺少真实参考图，请确认共用图片或取消该SKU"
            if missing_reference_attention
            else "生图自动修复一次后仍未完成"
        )
    elif recovering:
        state = "recovering"
        label = "正在自动修复"
        message = "已保留完成图片，正在继续未完成槽位"
    else:
        state = "normal"
        label = "主机正常"
        message = "后台监控运行中，无需到Codex对话查询进度"
    last_progress_values = [
        str(item.get("last_progress_at"))
        for item in workers if item.get("last_progress_at")
    ]
    return {
        "state": state,
        "label": label,
        "message": message,
        "codex_ready": codex_ready,
        "active_worker_count": len(workers),
        "batch_running": running_batch_pid() is not None,
        "last_progress_at": max(last_progress_values) if last_progress_values else "unknown",
        "stall_seconds": int(settings.get("image_generation_stall_seconds", 300)),
        "image_slot_concurrency": int(settings.get("image_slot_concurrency", 3)),
        "automatic_retry_limit": int(settings.get("step_retry_limit", 1)),
    }


@app.get("/api/workbench/system-status")
def workbench_system_status() -> Dict[str, Any]:
    status = image_host_status()
    with SEERFAR_KEYWORD_JOB_LOCK:
        queue = _seerfar_keyword_jobs()
    login_jobs = [
        item for item in queue.get("jobs") or []
        if str(item.get("status") or "") == "login_required"
    ]
    latest_login_job = login_jobs[-1] if login_jobs else None
    status["seerfar_login"] = {
        "required": bool(latest_login_job),
        "job_id": str((latest_login_job or {}).get("job_id") or ""),
        "product_id": str((latest_login_job or {}).get("product_id") or ""),
        "message": "Seerfar 登录已失效，请在 Chrome 的 Seerfar 页面重新登录；登录后会自动继续关键词查询。"
        if latest_login_job else "",
    }
    status["seerfar_worker"] = _seerfar_worker_status(queue)
    return status


@app.post("/api/workbench/system/safe-exit")
async def safe_exit_workbench(request: Request) -> Dict[str, Any]:
    if current_operator().get("role") != "owner":
        raise HTTPException(status_code=403, detail="只有工作室负责人可以安全退出主机")
    batch_pid = running_batch_pid()
    if batch_pid is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "当前生产任务已经授权运行到上架，不能人工安全停止；网络或运行异常会自动记录失败原因并保留断点。", "active_tasks": True},
        )

    def stop_service() -> None:
        deadline = time.monotonic() + 30
        while batch_pid is not None and _pid_is_alive(batch_pid) and time.monotonic() < deadline:
            time.sleep(0.25)
        WORKBENCH_STOP_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        WORKBENCH_STOP_REQUEST_PATH.write_text(now_iso() + "\n", encoding="utf-8")
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=stop_service, daemon=True, name="workbench-safe-exit").start()
    return {
        "status": "stopping_safely",
        "message": "AI Factory正在安全退出；已完成的图片和任务断点都会保留。",
        "active_task_was_stopped": batch_pid is not None,
    }


def known_remote_identity(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text.upper() not in {"UNKNOWN", "NULL", "NONE"})


def snapshot_effective_aggregate_status(snapshot: Dict[str, Any]) -> str:
    """Avoid showing a product as fully submitted while a selected store is unwritten."""
    canonical = snapshot.get("product") or {}
    canonical_status = str(canonical.get("aggregate_status") or "")
    if canonical_status not in TERMINAL_PUBLICATION_STATES and canonical_status != "HANDED_OFF_TO_OZON":
        return canonical_status
    selected_stores = [
        item for item in snapshot.get("stores") or []
        if bool(item.get("selected"))
    ]
    if not selected_stores:
        return canonical_status
    sku_rows = list(snapshot.get("sku_publications") or [])
    incomplete = False
    failed = False
    for store in selected_stores:
        store_status = str(store.get("status") or "").upper()
        if store_status in {"FAILED", "QUERY_ERROR", "NEEDS_ATTENTION"}:
            failed = True
            continue
        publication_id = store.get("id")
        store_skus = [
            item for item in sku_rows
            if publication_id is not None and str(item.get("publication_id")) == str(publication_id)
        ]
        has_remote_identity = any(
            known_remote_identity(item.get("task_id"))
            or known_remote_identity(item.get("ozon_product_id"))
            for item in store_skus
        )
        has_write = int(store.get("api_write_count") or 0) > 0
        if not (store_status in TERMINAL_PUBLICATION_STATES or has_write or has_remote_identity):
            incomplete = True
    if incomplete:
        return "PARTIAL_FAILED" if failed else "PARTIAL"
    return "PENDING_REMOTE" if canonical_status == "HANDED_OFF_TO_OZON" else canonical_status


def read_product_card(product_dir: Path) -> Dict[str, Any]:
    source_path = product_dir / "input/source.json"
    status_path = product_dir / "status.json"
    source = json.loads(source_path.read_text(encoding="utf-8")) if source_path.is_file() else {}
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
    errors = []
    if status.get("error_message") not in {None, "unknown"}:
        errors.append(status["error_message"])
    errors.extend(item.get("reason", str(item)) for item in (status.get("ozon") or {}).get("errors", []))
    snapshot = product_snapshot(ROOT, product_dir.name) if cutover_active(ROOT) else {}
    canonical_status = snapshot_effective_aggregate_status(snapshot)
    if canonical_status and canonical_status.upper() != "UNKNOWN":
        status_value = "PENDING_REMOTE" if canonical_status == "HANDED_OFF_TO_OZON" else canonical_status
        canonical_progress = (
            100 if canonical_status in TERMINAL_PUBLICATION_STATES
            else 99 if canonical_status in {"PENDING_REMOTE", "HANDED_OFF_TO_OZON"}
            else min(int(status.get("progress") or 0), 95) if canonical_status == "PARTIAL"
            else int(status.get("progress") or 0)
        )
    else:
        status_value = status.get("status") or "unknown"
        canonical_progress = int(status.get("progress") or 0)
    if (
        str(status.get("ai_service_state") or "") == "waiting_for_recovery"
        and str(status_value or "").upper() in {"PROCESSING", "QUEUED", "RUNNING"}
    ):
        status_value = "WAITING_FOR_AI_SERVICE"
    return {
        "product_id": product_dir.name,
        "title_cn": source.get("title_cn") or "unknown",
        "source_url": source.get("source_url") or "unknown",
        "selected_sku_count": len(source.get("skus") or []),
        "captured_at": source.get("captured_at") or status.get("started_at") or "unknown",
        "status": status_value,
        "handoff_message": "已提交Ozon，正在等待Ozon生成商品卡；本地可执行只读状态查询。" if status_value in {"HANDED_OFF_TO_OZON", "PENDING_REMOTE"} else None,
        "current_step": status.get("current_step") or "none",
        "progress": canonical_progress,
        "warnings": status.get("warnings") or [],
        "errors": errors,
        "directory_path": str(product_dir),
        "thumbnail_url": f"/api/inbox/products/{product_dir.name}/thumbnail",
        "retryable": status_value in {"FAILED", "PARTIAL_FAILED", "WAITING_FOR_AI_SERVICE", *ATTENTION_STATES},
    }


# Moved to collector_routes.py (exec'd into this module's globals at the bottom).

@app.get("/workbench")
def workbench_redirect() -> RedirectResponse:
    return RedirectResponse(url=f"/command-center?v={COMMAND_CENTER_VERSION}", status_code=307)


def workbench_page() -> FileResponse:
    trigger_image_cleanup()
    index_path = COMMAND_CENTER_DIST_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=503, detail="Command Center frontend is not built")
    return FileResponse(
        index_path, media_type="text/html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/command-center")
def command_center_alias(request: Request):
    if request.query_params.get("v") != COMMAND_CENTER_VERSION:
        return RedirectResponse(url=f"/command-center?v={COMMAND_CENTER_VERSION}", status_code=307)
    return workbench_page()


@app.get("/1688-collection")
def command_center_1688_collection_alias(request: Request):
    product_id = str(request.query_params.get("product_id") or request.query_params.get("productId") or "").strip()
    if request.query_params.get("v") != COMMAND_CENTER_VERSION:
        suffix = f"&product_id={urllib.parse.quote(product_id)}" if product_id else ""
        return RedirectResponse(url=f"/1688-collection?v={COMMAND_CENTER_VERSION}{suffix}", status_code=307)
    return workbench_page()


@app.get("/ozon-reference")
def command_center_ozon_reference_alias(request: Request):
    task_id = str(request.query_params.get("task_id") or request.query_params.get("taskId") or "").strip()
    if task_id:
        data = load_ozon_reference_tasks()
        task = next((
            item for item in data.get("items") or []
            if isinstance(item, dict) and str(item.get("task_id") or "") == task_id
        ), None)
        product_id = str((task or {}).get("created_product_id") or "").strip()
        if product_id:
            return RedirectResponse(
                url=f"/1688-collection?v={COMMAND_CENTER_VERSION}&product_id={urllib.parse.quote(product_id)}",
                status_code=307,
            )
    if request.query_params.get("v") != COMMAND_CENTER_VERSION:
        suffix = f"&task_id={urllib.parse.quote(task_id)}" if task_id else ""
        return RedirectResponse(url=f"/ozon-reference?v={COMMAND_CENTER_VERSION}{suffix}", status_code=307)
    return workbench_page()


@app.get("/workbench-legacy")
def workbench_legacy_page() -> RedirectResponse:
    return RedirectResponse(url=f"/command-center?v={COMMAND_CENTER_VERSION}", status_code=307)


@app.get("/assets/{asset_path:path}")
def command_center_asset(asset_path: str) -> FileResponse:
    path = (COMMAND_CENTER_DIST_DIR / "assets" / asset_path).resolve()
    assets_root = (COMMAND_CENTER_DIST_DIR / "assets").resolve()
    if assets_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Command Center asset not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path, media_type=media_type,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/workbench.css")
def workbench_css() -> RedirectResponse:
    return RedirectResponse(url=f"/command-center?v={COMMAND_CENTER_VERSION}", status_code=307)


@app.get("/workbench-future.css")
def workbench_future_css() -> RedirectResponse:
    return RedirectResponse(url=f"/command-center?v={COMMAND_CENTER_VERSION}", status_code=307)


@app.get("/icons/phosphor.css")
def phosphor_icons_css() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "icons" / "phosphor.css", media_type="text/css",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/icons/Phosphor.woff2")
def phosphor_icons_font() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "icons" / "Phosphor.woff2", media_type="font/woff2",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/brand/jlc-global-logo.png")
def jlc_global_logo() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "brand" / "jlc-global-logo.png", media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/workbench.js")
def workbench_js() -> RedirectResponse:
    return RedirectResponse(url=f"/command-center?v={COMMAND_CENTER_VERSION}", status_code=307)


@app.get("/api/workbench/summary")
def workbench_summary() -> Dict[str, Any]:
    cards = cached_workbench_cards()
    counts = {name: sum(1 for card in cards if card["state"] == name) for name in ("待处理", "处理中", "完成", "失败", "需要处理")}
    risks = sum(1 for card in cards if card["risk"]["level"] == "high")
    batch = get_batch_status()
    if risks:
        focus = {"type": "risk", "title": f"{risks} 个商品需要处理", "action": "打开需要处理"}
    elif counts["待处理"] or counts["失败"] or counts["需要处理"]:
        focus = {"type": "review", "title": "继续处理商品资料", "action": "进入商品审核台"}
    elif batch.get("running"):
        focus = {"type": "batch", "title": "批次正在运行", "action": "查看批次中心"}
    else:
        focus = {"type": "inbox", "title": "采集箱等待新商品", "action": "打开采集箱"}
    return {"counts": counts, "high_risk_count": risks, "focus": focus, "batch": batch}


# Finance routes moved to finance_routes.py (exec'd into this module's globals at the bottom).
# Market-intelligence routes and helpers moved to market_routes.py
# (imported at the bottom of this module; registers its routes on this app).
@app.get("/api/workbench/settings")
def get_workbench_settings() -> Dict[str, Any]:
    operator = current_operator()
    return {**workbench_settings(), "can_manage_settings": bool(operator.get("is_host_device"))}


@app.get("/api/workbench/session")
def workbench_session() -> Dict[str, Any]:
    operator = current_operator()
    return {
        "operator": operator,
        "can_manage_settings": bool(operator.get("is_host_device")),
        "product_visibility": "shared_all",
        "workspace_mode": "shared_lan",
        "access_code_required": False,
    }


@app.get("/api/workbench/operators")
def workbench_operators() -> Dict[str, Any]:
    require_owner_role()
    return {"items": list_operators(ROOT), "product_visibility": "shared_all", "legacy_only": True}


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
    owned_count = sum(
        product_owner(path)["owner_id"] == operator_id
        and not product_is_archived(path)
        for path in PRODUCTS_DIR.glob(WORKBENCH_PRODUCT_GLOB)
    )
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
    cards = cached_workbench_cards()
    query = q.strip().lower()
    if query:
        cards = [card for card in cards if query in json.dumps(card, ensure_ascii=False).lower()]
    if state:
        state_key = state.strip()
        state_aliases = {
            "attention": {"需要处理", "失败"},
            "needs_attention": {"需要处理", "失败"},
            "pending": {"待处理"},
            "inbox": {"待处理"},
            "running": {"处理中"},
            "processing": {"处理中"},
            "ozon": {"等待Ozon处理"},
            "remote": {"等待Ozon处理"},
            "done": {"完成"},
            "completed": {"完成"},
        }
        allowed_states = state_aliases.get(state_key.lower(), {state_key})
        if state_key.lower() in {"attention", "needs_attention"}:
            cards = [
                card for card in cards
                if card["state"] in allowed_states
                or card.get("attention_required")
                or str(card.get("raw_status") or "").upper() in {"STOPPED", "NEEDS_ATTENTION", "FAILED", "PARTIAL", "PARTIAL_FAILED"}
            ]
        else:
            cards = [card for card in cards if card["state"] in allowed_states]
    start = (page - 1) * page_size
    return {
        "items": cards[start:start + page_size], "total": len(cards), "page": page, "page_size": page_size,
        "execution_plan": bounded_parallel_plan(), "queue_summary": workbench_queue_summary(),
    }


@app.get("/api/workbench/products/{product_id}")
def workbench_product(product_id: str) -> Dict[str, Any]:
    return workbench_product_detail(product_id)


@app.post("/api/workbench/products/{product_id}/refresh-ozon-status")
def workbench_product_refresh_ozon_status(product_id: str) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    result = sync_remote_ozon_status_once([product_dir.name])
    return {
        **result,
        "product_id": product_dir.name,
        "detail": workbench_product_detail(product_dir.name),
    }


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
def workbench_image(product_id: str, slot: str, download: int = 0, v: str = "") -> FileResponse:
    product_dir = workbench_product_dir(product_id)
    plan = load_optional_json(product_dir / "output/image-plan.json")
    item = next((entry for entry in image_plan_items(plan) if str(entry.get("slot")) == slot), None)
    if not item:
        raise HTTPException(status_code=404, detail="图片不存在")
    image_path = product_output_image_path(product_dir, item.get("output_path"))
    if image_path is None or not image_path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(
        image_path,
        filename=image_path.name if download else None,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/workbench/products/{product_id}/assets/{bucket}/{asset_path:path}")
def workbench_asset(product_id: str, bucket: str, asset_path: str) -> FileResponse:
    """Serve one image only when its physical directory matches the requested bucket."""
    product_dir = workbench_product_dir(product_id)
    value = urllib.parse.unquote(asset_path)
    image_path = Path(value)
    image_path = image_path.resolve() if image_path.is_absolute() else (ROOT / image_path).resolve()
    expected = {"original": "original", "candidate": "candidate", "rejected": "rejected", "accepted": "accepted"}.get(bucket)
    if not expected or classify_path(product_dir, image_path) != expected:
        raise HTTPException(status_code=403, detail="图片目录与请求的素材状态不一致")
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(image_path, media_type=mimetypes.guess_type(image_path.name)[0] or "application/octet-stream")


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
    if asset_boundaries_enabled(product_dir):
        try:
            validate_registered_input_file(product_dir, image_path)
        except ProductionInputError as exc:
            raise HTTPException(
                status_code=403,
                detail=f"这张原图不属于当前商品本次采集：{exc}",
            ) from exc
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="本地原图文件不存在")
    return FileResponse(image_path, media_type=mimetypes.guess_type(image_path.name)[0] or "application/octet-stream")


@app.patch("/api/workbench/products/{product_id}/images/{slot}")
async def update_workbench_image(product_id: str, slot: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    ensure_workbench_product_mutable(product_dir)
    if asset_boundaries_enabled(product_dir):
        try:
            validate_formal_product_input(product_dir)
        except ProductionInputError as exc:
            raise HTTPException(status_code=409, detail=f"采集版本已变化，不能确认图片：{exc}") from exc
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
    elif action in {"keep", "accept"}:
        image_path = product_output_image_path(product_dir, item.get("output_path"))
        if image_path is None:
            raise HTTPException(status_code=422, detail="候选图片路径不安全")
        item["kept_at"] = now_iso()
        item["kept_by"] = current_operator_id()
        if asset_boundaries_enabled(product_dir):
            try:
                accepted = accept_candidate(
                    product_dir,
                    image_path,
                    confirmed_by=current_operator_id(),
                    confirmed_at=item["kept_at"],
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            item["accepted_path"] = project_relative(accepted)
            item["review_status"] = "accepted"
        else:
            # Archived pre-contract products remain reviewable, but only new
            # contract products may materialize an uploadable accepted tree.
            item["review_status"] = "legacy_kept"
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
    ensure_workbench_product_mutable(product_dir)
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
    image_path = product_output_image_path(product_dir, item.get("output_path"))
    if image_path is None:
        raise HTTPException(status_code=422, detail="图片路径不安全")
    if asset_boundaries_enabled(product_dir):
        try:
            validate_generated_output(product_dir, image_path)
            invalidate_accepted_candidate(product_dir, image_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    ensure_workbench_product_mutable(product_dir)
    plan_path = product_dir / "output/image-plan.json"
    plan = load_optional_json(plan_path)
    _, item = find_image_plan_item(plan, slot)
    image_path = product_output_image_path(product_dir, item.get("output_path"))
    if image_path is None:
        raise HTTPException(status_code=422, detail="图片路径不安全")
    try:
        rejected_path = reject_candidate(product_dir, image_path, group="workbench-rejected")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_image_feedback(
        ROOT, product_dir, item, "delete", now_iso(),
        threshold=int(workbench_settings()["learning_threshold"]),
    )
    item.update({
        "status": "rejected", "deleted_at": now_iso(),
        "rejected_path": project_relative(rejected_path),
        "review_status": "rejected",
    })
    atomic_write_json(plan_path, plan)
    append_log(product_dir, "workbench_image_deleted", {"slot": slot})
    return {"deleted": True, "slot": slot, "write_api_calls": 0, "inventory_api_calls": 0}


@app.patch("/api/workbench/products/{product_id}/draft")
async def save_workbench_draft(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    ensure_workbench_product_mutable(product_dir)
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
            tag = normalize_workbench_tag(value)
            if not tag:
                continue
            if tag not in normalized:
                normalized.append(tag)
        payload["tags"] = normalized[:30]
    if "attributes" in payload:
        if not isinstance(payload["attributes"], dict):
            raise HTTPException(status_code=422, detail="属性修改格式错误")
        model_ids = system_model_attribute_ids(product_dir)
        payload["attributes"] = {
            str(attribute_id): value
            for attribute_id, value in payload["attributes"].items()
            if str(attribute_id) not in model_ids
        }
    if "sku_overrides" in payload:
        if not isinstance(payload["sku_overrides"], dict):
            raise HTTPException(status_code=422, detail="SKU修改格式错误")
        settings = workbench_settings()
        rate = float(settings["fixed_cny_to_rub"])
        rounding = max(1, int(settings["rub_rounding"]))
        normalized_overrides: Dict[str, Dict[str, Any]] = {}
        for sku_id, raw_values in payload["sku_overrides"].items():
            if not isinstance(raw_values, dict):
                raise HTTPException(status_code=422, detail="SKU修改格式错误")
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
        if field == "sku_overrides" and isinstance(value, dict):
            draft[field] = deep_merge_sku_overrides(draft.get(field) or {}, value)
        elif field in {"attributes", "image_prompts"} and isinstance(value, dict):
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
    sku_fact_changed = False
    if "sku_overrides" in payload:
        sku_fact_changed = persist_workbench_sku_overrides(
            product_dir,
            payload["sku_overrides"],
            draft["saved_at"],
        )
        if sku_fact_changed:
            invalidate_sku_fact_outputs(product_dir)
            draft["sku_fact_dirty"] = True
            draft["sku_fact_invalidation_at"] = draft["saved_at"]
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
    append_log(product_dir, "workbench_draft_saved", {
        "version": draft["version"],
        "changed_fields": changed,
        "sku_fact_changed": sku_fact_changed,
    })
    return {
        "saved": True, "version": draft["version"], "saved_at": draft["saved_at"],
        "locked_fields": draft["locked_fields"], "learning": learning,
    }


@app.post("/api/workbench/products/{product_id}/sku-image-bindings")
async def bind_workbench_sku_image(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    ensure_workbench_product_mutable(product_dir)
    payload = await request.json()
    sku_id = str(payload.get("sku_id") or "").strip()
    selected_image_path = str(payload.get("selected_image_path") or "").strip()
    if not sku_id:
        raise HTTPException(status_code=422, detail="请选择要绑定的SKU")
    if not selected_image_path:
        raise HTTPException(status_code=422, detail="请选择一张本商品已采集图片")
    try:
        binding = save_sku_image_binding(
            product_dir,
            sku_id,
            selected_image_path,
            bound_by=current_operator_id(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    status_path = product_dir / "status.json"
    status = load_optional_json(status_path)
    if str(status.get("failed_step") or status.get("current_step") or "") in {"ecommerce_design", "image_plan"}:
        status["next_action"] = "retry_failed_step"
        status["updated_at"] = now_iso()
        atomic_write_json(status_path, status)
    append_log(product_dir, "sku_reference_image_bound", {
        "sku_id": sku_id,
        "selected_image_path": binding["selected_image_path"],
        "source_type": binding["source_type"],
    })
    preflight = build_preflight(product_dir, allow_download=False)
    remaining_blocked = [
        str(value)
        for value in preflight.get("blocked_sku_ids") or []
        if str(value or "").strip()
    ]
    if not remaining_blocked:
        status = load_optional_json(status_path)
        if str(status.get("failed_step") or status.get("current_step") or "") == "image_source_preflight":
            status["status"] = "STOPPED"
            status["next_action"] = "ecommerce_design"
            status["attention_required"] = False
            status["error_message"] = "unknown"
            status["human_message"] = "SKU参考图已绑定，可继续生成。"
            status["updated_at"] = now_iso()
            atomic_write_json(status_path, status)
    source = load_optional_json(product_dir / "input/source.json")
    return {
        "saved": True,
        "binding": binding,
        "candidates": workbench_sku_image_binding_candidates(product_dir, source),
        "remaining_blocked_sku_ids": remaining_blocked,
        "message": "SKU参考图已绑定，点击继续后会从当前断点恢复。" if remaining_blocked else "SKU参考图已全部绑定，可以继续生成。",
        "ozon_write_calls": 0,
        "inventory_calls": 0,
    }


@app.put("/api/workbench/products/{product_id}/visual-preference")
async def save_visual_preference(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    ensure_workbench_product_mutable(product_dir)
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
            "ecommerce_design", "image_plan", "image_generation", "image_qc",
            "ozon_upload",
        }
        completed = list(status.get("completed_steps") or [])
        status["completed_steps"] = [step for step in completed if step not in reset_steps]
        invalidated = [step for step in completed if step in reset_steps]
        pipeline_steps = [
            "validate_source", "product_analysis", "category_match", "variant_rules", "measurements",
            "offer_exists_check", "upload_feasibility", "product_positioning", "ecommerce_design", "russian_copy",
            "field_completion", "image_plan",
            "image_generation", "image_qc", "ozon_upload",
        ]
        status["pending_steps"] = [step for step in pipeline_steps if step not in status["completed_steps"]]
        status["next_action"] = status["pending_steps"][0] if status["pending_steps"] else "complete"
        if status.get("status") not in {"COLLECTED", *ATTENTION_STATES}:
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
        # The workbench product list, detail page, and notification badge must
        # all read the same effective status.  After the SQLite cutover the
        # legacy status.json can contain an older failure even though the
        # canonical store-publication state has already advanced.
        status = effective_product_status(
            product_dir,
            load_optional_json(product_dir / "status.json"),
        )
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
        if raw_status in ATTENTION_STATES:
            error = friendly_pipeline_error(status)
            items.append({
                "id": f"failure:{product_dir.name}:{status.get('last_run_at') or status.get('completed_at') or 'current'}",
                "type": "failure", "product_id": product_dir.name,
                "title": error["title"],
                "message": error["message"],
                "action": error["action"], "tab": error["tab"],
                "product_title": source.get("title_cn") or product_dir.name,
                "created_at": status.get("last_run_at") or "unknown",
                "requires_action": True,
            })
        elif raw_status == "WAITING_MANUAL_REVIEW" and not is_auto_upload_ready_status(status):
            items.append({
                "id": f"review:{product_dir.name}:{status.get('last_run_at') or 'current'}",
                "type": "review", "product_id": product_dir.name,
                "title": "商品等待确认上传",
                "message": "商品资料和图片已经完成，请检查后确认上传",
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
    if str(status.get("status") or "").upper() in ATTENTION_STATES:
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
    ensure_workbench_product_mutable(product_dir)
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
    if (
        not definitely_retryable(record)
        and not image_repair_retryable(record)
        and not variant_repair_retryable(record)
    ):
        raise HTTPException(status_code=409, detail="该店铺状态不明确或已有远端任务，禁止重传")
    status_path = product_dir / "status.json"
    original_status = load_optional_json(status_path)
    status = dict(original_status)
    status.update({
        "status": "WAITING_MANUAL_REVIEW", "current_step": "field_completion", "progress": 95,
        "failed_step": "unknown", "error_code": "unknown", "error_message": "unknown",
        "next_action": "ozon_upload", "target_store_ids_for_run": [store_id],
        "task_authorized": True, "last_run_at": now_iso(),
    })
    status["completed_steps"] = [step for step in status.get("completed_steps") or [] if step != "ozon_upload"]
    status["pending_steps"] = list(dict.fromkeys([*(status.get("pending_steps") or []), "ozon_upload"]))
    atomic_write_json(status_path, status)
    try:
        batch = create_batch(
            ROOT, [product_id], target_store_ids=[store_id],
            auto_upload=True, allow_terminal_store_retry=True,
        )
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


@app.post("/api/workbench/products/{product_id}/stores/retry-failed")
async def retry_failed_stores(product_id: str, request: Request) -> Dict[str, Any]:
    """Retry all explicitly failed stores in one isolated batch.

    A product may already have a pending task in another store.  The retry
    allowlist is therefore validated per store, and the batch is started once
    with --only-store entries instead of racing multiple single-store runs.
    """
    product_dir = workbench_product_dir(product_id)
    payload = await request.json()
    requested = payload.get("store_ids") if isinstance(payload, dict) else None
    requested_ids = [str(value) for value in (requested or []) if str(value).strip()]
    with BATCH_QUEUE_LOCK:
        if running_batch_pid() is not None:
            raise HTTPException(status_code=409, detail="当前有任务正在运行，请完成或安全停止后再重试")
        if product_id in reserved_product_batches():
            raise HTTPException(status_code=409, detail="该商品已在任务队列中")
        publications = load_publications(product_dir)
        stores = publications.get("stores") or {}
        candidates = requested_ids or [
            store_id for store_id, record in stores.items()
            if str(record.get("status") or "") == "FAILED"
        ]
        if not candidates:
            raise HTTPException(status_code=409, detail="当前没有明确失败且可重试的店铺")
        retryable: List[str] = []
        blocked: Dict[str, str] = {}
        connected = set(connected_store_ids())
        for store_id in dict.fromkeys(candidates):
            record = stores.get(store_id) or {}
            if store_id not in connected:
                blocked[store_id] = "店铺未启用或尚未验证"
            elif str(record.get("status") or "") != "FAILED":
                blocked[store_id] = "店铺不是明确失败状态"
            elif (
                not definitely_retryable(record)
                and not image_repair_retryable(record)
                and not variant_repair_retryable(record)
            ):
                blocked[store_id] = "店铺状态不明确或已有远端任务，禁止重传"
            else:
                retryable.append(store_id)
        if not retryable:
            raise HTTPException(status_code=409, detail={"message": "没有可安全重试的店铺", "blocked": blocked})
        status_path = product_dir / "status.json"
        original_status = load_optional_json(status_path)
        status = dict(original_status)
        status.update({
            "status": "WAITING_MANUAL_REVIEW", "current_step": "field_completion", "progress": 95,
            "failed_step": "unknown", "error_code": "unknown", "error_message": "unknown",
            "next_action": "ozon_upload", "target_store_ids_for_run": retryable,
            "task_authorized": True, "last_run_at": now_iso(),
        })
        status["completed_steps"] = [step for step in status.get("completed_steps") or [] if step != "ozon_upload"]
        status["pending_steps"] = list(dict.fromkeys([*(status.get("pending_steps") or []), "ozon_upload"]))
        atomic_write_json(status_path, status)
        try:
            batch = create_batch(
                ROOT, [product_id], target_store_ids=retryable,
                auto_upload=True, allow_terminal_store_retry=True,
            )
            save_batch_owner(batch["batch_id"])
            final_snapshot(product_dir, retryable, batch["batch_id"])
            launched = launch_or_enqueue_batch(batch, "retry_failed_stores")
        except Exception:
            atomic_write_json(status_path, original_status)
            raise
    append_log(product_dir, "failed_stores_retry_started", {
        "store_ids": retryable, "blocked": blocked, "batch_id": batch["batch_id"],
    })
    return {
        **launched, "batch_id": batch["batch_id"], "product_id": product_id,
        "store_ids": retryable, "blocked": blocked,
        "write_api_calls": 0, "inventory_api_calls": 0,
    }


@app.post("/api/workbench/products/{product_id}/suggestions/{suggestion_id}")
async def handle_ai_suggestion(product_id: str, suggestion_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    ensure_workbench_product_mutable(product_dir)
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
    ensure_workbench_product_mutable(product_dir)
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
    active_path = product_output_image_path(product_dir, plan_item.get("output_path"))
    if active_path is not None:
        try:
            invalidate_accepted_candidate(product_dir, active_path)
        except ValueError:
            pass
    plan_item["review_status"] = "candidate"
    plan_item.pop("accepted_path", None)
    atomic_write_json(product_dir / "output/image-plan.json", plan)
    record_image_feedback(
        ROOT, product_dir, plan_item, "regenerate", now_iso(), prompt=prompt,
        threshold=int(workbench_settings()["learning_threshold"]),
    )
    append_log(product_dir, "single_image_regeneration_queued", {"slot": slot})
    return {"queued": True, "slot": slot, "message": "已加入单图重生成队列；不会重做其他图片"}


@app.post("/api/workbench/products/{product_id}/run")
async def run_single_workbench_product(product_id: str, request: Request) -> Dict[str, Any]:
    product_dir = workbench_product_dir(product_id)
    # Looking up an existing reservation is duplicate protection, not a product
    # load. Return it before reading product artifacts so a repeated click can
    # never create a second batch, even if an older queued product is no longer
    # a valid input for a new production run.
    with BATCH_QUEUE_LOCK:
        existing_batch_id = reserved_product_batches().get(product_id)
        if existing_batch_id:
            existing_batch = load_optional_json(batch_path(ROOT, existing_batch_id))
            if existing_batch:
                mark_products_queued_for_batch(
                    existing_batch,
                    priority_upload=existing_batch.get("execution_priority") == "manual_upload",
                )
            return {
                "status": "already_queued", "batch_id": existing_batch_id,
                "write_api_calls": 0, "inventory_api_calls": 0,
                "target_store_ids": [],
            }
    ensure_workbench_product_mutable(product_dir)
    if is_ozon_reference_draft_product(product_dir):
        result = launch_ozon_reference_image_generation(product_dir)
        return {
            **result,
            "batch_id": "ozon_reference_image_generation",
            "pid": None,
            "queue_position": 0,
            "target_store_ids": [],
            "target_store_id_source": "ozon_reference_draft",
            "resumed_from_checkpoint": True,
            "priority_upload": False,
            "write_api_calls": 0,
            "inventory_api_calls": 0,
        }
    try:
        validate_formal_product_input(product_dir)
    except ProductionInputError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"这件商品不是当前工作台本次采集的正式输入，已阻止运行：{exc}",
        ) from exc
    raw = await request.body()
    payload = json.loads(raw) if raw else {}
    requested_store_ids = payload.get("store_ids") or []
    store_id_source = "request"
    if requested_store_ids:
        selected_stores = validate_target_stores(requested_store_ids)
    else:
        fallback_store_ids = saved_target_store_candidates(product_dir)
        selected_stores = validate_target_stores(fallback_store_ids) if fallback_store_ids else []
        store_id_source = "saved_product_selection" if selected_stores else "missing"
    # Store/SKU/category selection happens before this click.  The click then
    # authorizes the full unattended path, including the selected-store upload.
    auto_upload = True
    if not selected_stores:
        raise HTTPException(
            status_code=422,
            detail="这件商品还没有保存目标店铺。请先点“上传至店铺”选择店铺，再点继续。",
        )
    original_status = load_optional_json(product_dir / "status.json")
    status = prepare_partial_upload_resume(
        product_dir,
        effective_product_status(product_dir, dict(original_status)),
    )
    # A click on Run/Continue is the explicit operator authorization for this
    # local product run.  Do not require a stale previous status flag to be
    # true, otherwise hard-blocked products can show a Continue button that
    # cannot actually resume.
    resume_authorized_failure = status.get("status") in ATTENTION_STATES
    failed_step = str(status.get("failed_step") or status.get("current_step") or "")
    resume_upload_failure = (
        resume_authorized_failure
        and failed_step in {"ozon_upload", "manual_ozon_upload"}
        and int(status.get("api_write_count") or 0) == 0
    )
    if resume_upload_failure:
        auto_upload = True
    resume_authorized_checkpoint = (
        status.get("status") in {
        "CATEGORY_MATCHED", "PRICED", "CONTENT_GENERATED", "IMAGES_GENERATED",
        "WAITING_FOR_AI_SERVICE", "STOPPED", "PARTIAL",
        }
        or str(status.get("ai_service_state") or "") == "waiting_for_recovery"
    )
    resume_authorized = resume_authorized_failure or resume_authorized_checkpoint
    if status.get("status") == "PENDING_REMOTE":
        raise HTTPException(status_code=409, detail="Ozon仍在处理，禁止重复提交")
    if status.get("status") in {"UPLOADED", "OZON_MODERATION", "ACTIVE"}:
        raise HTTPException(status_code=409, detail="商品已提交；修改后应由现有UPDATE流程处理")
    if (
        str(status.get("status") or "").upper() != "PARTIAL"
        and int(status.get("api_write_count") or 0) > 0
        and (status.get("ozon") or {}).get("upload_status") not in {"failed", "not_started"}
    ):
        raise HTTPException(status_code=409, detail="已有Ozon写入记录，当前状态不允许重试")
    confirmed_manual_upload = (
        auto_upload
        and str(status.get("status") or "").upper() == "WAITING_MANUAL_REVIEW"
        and int(status.get("api_write_count") or 0) == 0
    )
    with BATCH_QUEUE_LOCK:
        if selected_stores:
            select_stores(product_dir, selected_stores, connected_store_ids(), payload.get("overrides") or {})
        materialize_active_experience(ROOT, product_dir, now_iso())
        try:
            batch = create_batch(ROOT, [product_id], target_store_ids=selected_stores, auto_upload=auto_upload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        save_batch_owner(batch["batch_id"])
        if batch.get("product_count") != 1:
            raise HTTPException(status_code=409, detail="当前商品状态不允许进入任务")
        if auto_upload:
            final_snapshot(product_dir, selected_stores, batch["batch_id"])
        reason = (
            "manual_upload" if confirmed_manual_upload
            else "resume_failed_product" if resume_authorized_failure
            else "resume_checkpoint" if resume_authorized_checkpoint
            else "single_product"
        )
        # Clicking Run Task is the one batch authorization. Manual mode runs
        # the full generation pipeline now and stops only at image review.
        try:
            launched = launch_or_enqueue_batch(batch, reason)
        except Exception:
            if confirmed_manual_upload:
                atomic_write_json(product_dir / "status.json", original_status)
            raise
    append_log(product_dir, "workbench_product_run", {"batch_id": batch["batch_id"], "launch_status": launched["status"]})
    return {
        "status": launched["status"], "batch_id": batch["batch_id"], "pid": launched.get("pid"),
        "queue_position": launched.get("queue_position", 0), "write_api_calls": 0,
        "inventory_api_calls": 0, "target_store_ids": selected_stores,
        "target_store_id_source": store_id_source,
        "resumed_from_checkpoint": resume_authorized,
        "priority_upload": launched.get("priority_upload", False),
        "preemption_requested": launched.get("preemption_requested", False),
        "message": launched.get("message"),
    }


# Moved to batches_routes.py (exec'd into this module's globals at the bottom).

# Moved to shops_routes.py (exec'd into this module's globals at the bottom).

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
    cards = cached_workbench_cards()
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
            image_path = product_output_image_path(product_dir, item.get("output_path"))
            if image_path is not None and image_path.is_file():
                archive.write(image_path, f"{item.get('slot')}{image_path.suffix.lower()}")
    return FileResponse(path, filename=path.name)


# Market-intelligence routes/helpers live in market_routes.py.  Execute its
# code in THIS module's globals so the extracted functions resolve globals
# (and test patches via patch.object) exactly as if they were still defined
# here; their names also land directly on this module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
_local_dir = Path(__file__).resolve().parent
for _extracted in ("market_routes.py", "finance_routes.py", "reference_helpers.py", "reference_routes.py", "collector_routes.py", "batches_routes.py", "shops_routes.py"):
    _extracted_path = _local_dir / _extracted
    exec(compile(_extracted_path.read_text(encoding="utf-8"), str(_extracted_path), "exec"), globals())
