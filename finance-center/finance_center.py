"""Local-only finance center for the AI Factory workbench.

The module deliberately keeps Ozon access read-only.  Seller credentials are
resolved from the existing local shop registry and are never returned by any
public method.  Historical migrations always read the source database through
SQLite's read-only URI mode.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional
from xml.etree import ElementTree


MONEY = Decimal("0.01")
DEFAULT_RUB_PER_CNY = Decimal("12")
FINANCE_QUERY_WINDOW_DAYS = 28
SCHEDULED_MAX_ATTEMPTS = 2
SCHEDULED_RETRY_DELAY = timedelta(hours=1)
MISSING_PURCHASE_SOURCES = {"", "missing", "missing_assumed_zero", "unknown", "legacy"}
MISSING_LOGISTICS_SOURCES = {"", "missing", "unknown", "legacy", "missing_shipment_date"}
OZON_AD_OPERATION_TYPES = (
    "OperationMarketplaceCostPerClick",
    "OperationPromotionWithCostPerOrder",
)

# Advertising spend may enter an individual order only when the ad row retains
# an exact order/posting identifier. Product identifiers are corroborating
# fields, never a reason to choose one order from a campaign-level aggregate.
AD_MATCH_IS_EXACT_SQL = """
o.id IS NOT NULL
AND (trim(COALESCE(a.posting_number,'')) != '' OR trim(COALESCE(a.order_number,'')) != '')
AND (trim(COALESCE(a.posting_number,'')) = '' OR trim(a.posting_number) = trim(o.posting_number))
AND (trim(COALESCE(a.order_number,'')) = '' OR trim(a.order_number) = trim(COALESCE(o.order_number,'')))
AND (trim(COALESCE(a.sku,'')) = '' OR trim(a.sku) IN (trim(o.sku),trim(COALESCE(o.offer_id,''))))
AND (trim(COALESCE(a.offer_id,'')) = '' OR trim(a.offer_id) IN (trim(o.sku),trim(COALESCE(o.offer_id,''))))
AND (
  trim(COALESCE(a.product_id,'')) = ''
  OR trim(a.product_id) IN (trim(o.sku),trim(COALESCE(o.offer_id,'')))
  OR EXISTS (
    SELECT 1 FROM product_master pm
    WHERE pm.store_id=o.store_id AND trim(COALESCE(pm.product_id,''))=trim(a.product_id)
      AND (pm.sku=o.sku OR (trim(COALESCE(o.offer_id,''))!='' AND pm.offer_id=o.offer_id))
  )
)
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def decimal_value(value: Any) -> Decimal:
    try:
        text = str(value if value is not None else "0").strip().replace(",", "")
        return Decimal(text or "0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def money(value: Any) -> str:
    return str(decimal_value(value).quantize(MONEY, rounding=ROUND_HALF_UP))


def ratio(numerator: Decimal, denominator: Decimal) -> float:
    if denominator == 0:
        return 0.0
    return float((numerator / denominator).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def normalized_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff\u0400-\u04ff]+", "", text)


class OzonReadOnlyError(RuntimeError):
    """A sanitized Ozon read failure with enough metadata for safe backoff."""

    def __init__(
        self, message: str, *, endpoint: str, status_code: Optional[int] = None,
        retryable: bool = False, retry_after_seconds: int = 0,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = max(0, int(retry_after_seconds or 0))
        self.read_api_calls = 1


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
  id TEXT PRIMARY KEY, store_name TEXT NOT NULL, store_alias TEXT,
  client_id_reference TEXT NOT NULL DEFAULT '', seller_id TEXT,
  status TEXT NOT NULL DEFAULT 'active', sync_status TEXT NOT NULL DEFAULT 'idle',
  sync_error TEXT, last_sync_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, row_hash TEXT NOT NULL, file_hash TEXT NOT NULL,
  posting_number TEXT NOT NULL, order_number TEXT, sku TEXT NOT NULL, offer_id TEXT,
  product_name TEXT, order_date TEXT, buyer_paid_rub TEXT NOT NULL,
  buyer_paid_cny TEXT NOT NULL, status TEXT, raw_payload TEXT NOT NULL,
  created_at TEXT NOT NULL, store_id TEXT NOT NULL DEFAULT 'default_store'
);
CREATE TABLE IF NOT EXISTS finance_transactions (
  id TEXT PRIMARY KEY, row_hash TEXT NOT NULL, file_hash TEXT NOT NULL,
  matched_order_id TEXT, posting_number TEXT, order_number TEXT, sku TEXT,
  occurred_at TEXT, operation_type TEXT, service_name TEXT,
  amount_rub TEXT NOT NULL, amount_cny TEXT NOT NULL,
  platform_commission_cny TEXT NOT NULL DEFAULT '0.00',
  logistics_fee_cny TEXT NOT NULL DEFAULT '0.00', refund_cny TEXT NOT NULL DEFAULT '0.00',
  compensation_cny TEXT NOT NULL DEFAULT '0.00', acquiring_cny TEXT NOT NULL DEFAULT '0.00',
  other_fee_cny TEXT NOT NULL DEFAULT '0.00', raw_payload TEXT NOT NULL,
  created_at TEXT NOT NULL, store_id TEXT NOT NULL DEFAULT 'default_store'
);
CREATE TABLE IF NOT EXISTS ad_spend_transactions (
  id TEXT PRIMARY KEY, row_hash TEXT NOT NULL, file_hash TEXT NOT NULL,
  matched_order_id TEXT, occurred_at TEXT, campaign_id TEXT, campaign_name TEXT,
  posting_number TEXT NOT NULL DEFAULT '', order_number TEXT NOT NULL DEFAULT '',
  sku TEXT, offer_id TEXT NOT NULL DEFAULT '', product_id TEXT, spend_rub TEXT NOT NULL, spend_cny TEXT NOT NULL,
  views INTEGER NOT NULL DEFAULT 0, clicks INTEGER NOT NULL DEFAULT 0,
  orders INTEGER NOT NULL DEFAULT 0, revenue_rub TEXT NOT NULL DEFAULT '0.00',
  revenue_cny TEXT NOT NULL DEFAULT '0.00', raw_payload TEXT NOT NULL,
  created_at TEXT NOT NULL, store_id TEXT NOT NULL DEFAULT 'default_store'
);
CREATE TABLE IF NOT EXISTS product_costs (
  id TEXT PRIMARY KEY, row_hash TEXT NOT NULL, file_hash TEXT NOT NULL, sku TEXT NOT NULL,
  offer_id TEXT, product_name TEXT, purchase_cost_cny TEXT NOT NULL, effective_date TEXT,
  raw_payload TEXT NOT NULL, created_at TEXT NOT NULL,
  amount_original TEXT NOT NULL DEFAULT '0.00', currency_original TEXT NOT NULL DEFAULT 'CNY',
  currency_source TEXT NOT NULL DEFAULT 'trusted_context',
  source TEXT NOT NULL DEFAULT 'imported_purchase_cost', updated_at TEXT,
  batch_id TEXT, note TEXT, store_id TEXT NOT NULL DEFAULT 'default_store'
);
CREATE TABLE IF NOT EXISTS purchase_order_match (
  id TEXT PRIMARY KEY, store_id TEXT NOT NULL DEFAULT 'default_store', order_id TEXT NOT NULL,
  posting_number TEXT NOT NULL, sku TEXT, purchase_cost_cny TEXT NOT NULL,
  weight_g TEXT, source_file TEXT NOT NULL, source_row INTEGER NOT NULL DEFAULT 0,
  matched_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_master (
  id TEXT PRIMARY KEY, store_id TEXT NOT NULL DEFAULT 'default_store', sku TEXT NOT NULL,
  offer_id TEXT, product_id TEXT, product_name TEXT, image_url TEXT,
  unit_purchase_cost_cny TEXT, estimated_weight_g TEXT,
  weight_source TEXT NOT NULL DEFAULT 'missing', dimensions TEXT, volume_weight TEXT,
  purchase_cost_source TEXT NOT NULL DEFAULT 'missing',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(store_id, sku)
);
CREATE TABLE IF NOT EXISTS profit_snapshots (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL, posting_number TEXT NOT NULL, sku TEXT NOT NULL,
  product_name TEXT, order_date TEXT, buyer_paid_rub TEXT NOT NULL, buyer_paid_cny TEXT NOT NULL,
  ozon_original_charge_cny TEXT NOT NULL DEFAULT '0.00', finance_fee_rub TEXT NOT NULL DEFAULT '0.00',
  finance_fee_cny TEXT NOT NULL DEFAULT '0.00', platform_commission_cny TEXT NOT NULL DEFAULT '0.00',
  logistics_fee_cny TEXT NOT NULL DEFAULT '0.00', refund_cny TEXT NOT NULL DEFAULT '0.00',
  compensation_cny TEXT NOT NULL DEFAULT '0.00', ad_spend_rub TEXT NOT NULL DEFAULT '0.00',
  ad_spend_cny TEXT NOT NULL DEFAULT '0.00', purchase_cost_cny TEXT NOT NULL DEFAULT '0.00',
  final_profit_cny TEXT NOT NULL DEFAULT '0.00', profit_margin TEXT NOT NULL DEFAULT '0',
  data_sources TEXT NOT NULL DEFAULT '', is_unmatched INTEGER NOT NULL DEFAULT 0,
  unmatched_reason TEXT, created_at TEXT NOT NULL,
  revenue_calculation_version TEXT NOT NULL DEFAULT 'finance_center_v1',
  profit_snapshot_recomputed_at TEXT, revenue_source TEXT NOT NULL DEFAULT 'unknown',
  revenue_warning_reasons TEXT NOT NULL DEFAULT '', unit_purchase_cost_cny TEXT NOT NULL DEFAULT '0.00',
  purchase_cost_source TEXT NOT NULL DEFAULT 'missing', purchase_cost_update_batch_id TEXT,
  recompute_reason TEXT NOT NULL DEFAULT 'finance_center', estimated_weight_g TEXT NOT NULL DEFAULT '0',
  actual_weight_g TEXT NOT NULL DEFAULT '0', weight_used_g TEXT NOT NULL DEFAULT '0',
  weight_source TEXT NOT NULL DEFAULT 'missing', logistics_cost_source TEXT NOT NULL DEFAULT 'missing',
  logistics_recomputed_at TEXT, logistics_warning_reasons TEXT NOT NULL DEFAULT '',
  store_id TEXT NOT NULL DEFAULT 'default_store', logistics_cost_cny TEXT NOT NULL DEFAULT '0.00',
  logistics_source TEXT NOT NULL DEFAULT 'missing', logistics_rule_version TEXT NOT NULL DEFAULT 'missing',
  logistics_rule_date TEXT, logistics_rule_date_source TEXT NOT NULL DEFAULT 'missing'
);
CREATE TABLE IF NOT EXISTS import_unmatched_rows (
  id TEXT PRIMARY KEY, store_id TEXT NOT NULL DEFAULT 'default_store', file_type TEXT NOT NULL,
  file_name TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL DEFAULT '', source_row_number INTEGER NOT NULL DEFAULT 0,
  occurred_at TEXT, posting_number TEXT, order_number TEXT, sku TEXT, offer_id TEXT,
  amount_rub TEXT NOT NULL DEFAULT '0.00', amount_cny TEXT NOT NULL DEFAULT '0.00',
  reason TEXT NOT NULL, resolution_status TEXT NOT NULL DEFAULT 'open',
  raw_payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finance_center_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finance_sync_runs (
  id TEXT PRIMARY KEY, store_id TEXT, started_at TEXT NOT NULL, finished_at TEXT,
  date_from TEXT NOT NULL, date_to TEXT NOT NULL, trigger TEXT NOT NULL,
  status TEXT NOT NULL, orders_seen INTEGER NOT NULL DEFAULT 0,
  finance_seen INTEGER NOT NULL DEFAULT 0, changed_rows INTEGER NOT NULL DEFAULT 0,
  error TEXT, read_api_calls INTEGER NOT NULL DEFAULT 0,
  write_api_calls INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS finance_import_batches (
  id TEXT PRIMARY KEY, file_kind TEXT NOT NULL, file_name TEXT NOT NULL,
  status TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  applied_at TEXT, rolled_back_at TEXT, backup_path TEXT NOT NULL,
  row_count INTEGER NOT NULL DEFAULT 0, inserted_count INTEGER NOT NULL DEFAULT 0,
  updated_count INTEGER NOT NULL DEFAULT 0, mapping_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS finance_import_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, table_name TEXT NOT NULL,
  row_id TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finance_import_mappings (
  id TEXT PRIMARY KEY, file_kind TEXT NOT NULL, source_header TEXT NOT NULL,
  target_field TEXT NOT NULL, confidence REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL, UNIQUE(file_kind, source_header)
);
CREATE TABLE IF NOT EXISTS other_entries (
  id TEXT PRIMARY KEY, entry_type TEXT NOT NULL CHECK(entry_type IN ('income','expense')),
  amount_cny TEXT NOT NULL, amount_original TEXT NOT NULL, currency_original TEXT NOT NULL,
  occurred_on TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', store_id TEXT,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  import_batch_id TEXT
);
CREATE TABLE IF NOT EXISTS exchange_rates (
  rate_date TEXT NOT NULL, currency_code TEXT NOT NULL, rub_per_unit TEXT NOT NULL,
  source TEXT NOT NULL, manually_confirmed INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL, PRIMARY KEY(rate_date, currency_code)
);
CREATE TABLE IF NOT EXISTS finance_data_coverage (
  id TEXT PRIMARY KEY, store_id TEXT NOT NULL, date_from TEXT NOT NULL, date_to TEXT NOT NULL,
  orders_status TEXT NOT NULL DEFAULT 'unknown', finance_status TEXT NOT NULL DEFAULT 'unknown',
  ads_status TEXT NOT NULL DEFAULT 'unknown', source TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fc_orders_store_date ON orders(store_id, order_date);
CREATE INDEX IF NOT EXISTS idx_fc_orders_posting ON orders(store_id, posting_number, sku);
CREATE INDEX IF NOT EXISTS idx_fc_profit_store_date ON profit_snapshots(store_id, order_date);
CREATE INDEX IF NOT EXISTS idx_fc_finance_store_date ON finance_transactions(store_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_fc_purchase_order_match ON purchase_order_match(store_id, posting_number, sku);
CREATE INDEX IF NOT EXISTS idx_fc_ads_store_date ON ad_spend_transactions(store_id, occurred_at);
"""


CANONICAL_SQL = """
WITH line_keys AS (
  SELECT ps.*, COALESCE(ps.store_id, 'default_store') AS store_key,
         COALESCE(NULLIF(o.offer_id, ''), NULLIF(ps.sku, ''), ps.id) AS product_key,
         o.order_number, o.offer_id,
         pm.image_url
  FROM profit_snapshots ps
  LEFT JOIN orders o
    ON COALESCE(o.store_id, 'default_store') = COALESCE(ps.store_id, 'default_store')
   AND o.posting_number = ps.posting_number AND o.sku = ps.sku
  LEFT JOIN product_master pm
    ON COALESCE(pm.store_id, 'default_store') = COALESCE(ps.store_id, 'default_store')
   AND (pm.sku = ps.sku OR (o.offer_id IS NOT NULL AND o.offer_id != '' AND pm.offer_id = o.offer_id))
), ranked AS (
  SELECT line_keys.*,
         COUNT(*) OVER (PARTITION BY store_key, posting_number, product_key) AS duplicate_rows,
         ROW_NUMBER() OVER (
           PARTITION BY store_key, posting_number, product_key
           ORDER BY CASE WHEN revenue_source LIKE 'seller_api%' THEN 0 ELSE 1 END,
                    CAST(COALESCE(buyer_paid_cny, '0') AS REAL) DESC, id DESC
         ) AS rn
  FROM line_keys
)
SELECT * FROM ranked WHERE rn = 1
"""


IMPORT_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "purchase_cost": {
        "store_id": ("店铺", "店铺id", "store", "storeid", "shop"),
        "sku": ("sku", "商品编号", "商业编号", "ozonsku"),
        "offer_id": ("offerid", "offer_id", "货号", "商家编码", "seller sku"),
        "product_name": ("名称", "商品名称", "商品名", "产品名称", "productname", "name"),
        "order_number": ("订单编号", "订单号", "ordernumber", "order_number", "postingnumber", "posting_number"),
        "purchase_cost_cny": ("采购价", "采购成本", "采购单价", "成本价", "purchasecost", "costcny"),
        "effective_date": ("生效日期", "日期", "采购日期", "effectivedate"),
        "currency_original": ("币种", "货币", "currency"),
        "note": ("备注", "说明", "note", "memo"),
        "image_url": ("主图", "主图链接", "图片", "image", "imageurl"),
    },
    "orders": {
        "store_id": ("店铺", "店铺id", "store", "storeid", "shop"),
        "posting_number": ("postingnumber", "posting_number", "发货编号", "posting号"),
        "order_number": ("订单编号", "订单号", "ordernumber", "order_number"),
        "sku": ("sku", "商品编号", "商业编号", "ozonsku"),
        "offer_id": ("offerid", "offer_id", "货号", "商家编码"),
        "product_name": ("商品名称", "商品名", "productname", "name"),
        "order_date": ("下单日期", "订单日期", "orderdate", "date"),
        "buyer_paid_rub": ("销售额rub", "实付rub", "buyerpaidrub", "amount rub"),
        "buyer_paid_cny": ("销售额cny", "销售额人民币", "实付人民币", "buyerpaidcny"),
        "status": ("状态", "订单状态", "status"),
        "image_url": ("主图", "主图链接", "图片", "image", "imageurl"),
    },
    "finance": {
        "store_id": ("店铺", "店铺id", "store", "storeid", "shop"),
        "posting_number": ("postingnumber", "posting_number", "发货编号", "posting号"),
        "order_number": ("订单编号", "订单号", "ordernumber", "order_number"),
        "sku": ("sku", "商品编号", "商业编号", "ozonsku"),
        "occurred_at": ("发生日期", "交易日期", "日期", "occurredat", "operationdate"),
        "operation_type": ("操作类型", "交易类型", "operationtype"),
        "service_name": ("服务名称", "服务", "servicename"),
        "amount_rub": ("金额rub", "amountrub", "金额卢布"),
        "amount_cny": ("金额cny", "amountcny", "金额人民币"),
        "platform_commission_cny": ("佣金cny", "平台佣金", "commissioncny"),
        "logistics_fee_cny": ("物流费cny", "物流费", "logisticscny"),
        "refund_cny": ("退款cny", "退款", "refundcny"),
        "compensation_cny": ("赔偿cny", "补偿", "compensationcny"),
        "acquiring_cny": ("收单费cny", "支付费", "acquiringcny"),
        "other_fee_cny": ("其他费用cny", "其他费用", "otherfeecny"),
    },
    "ads": {
        "store_id": ("店铺", "店铺id", "store", "storeid", "shop"),
        "occurred_at": ("发生日期", "广告日期", "日期", "occurredat"),
        "campaign_id": ("广告活动id", "campaignid", "campaign_id"),
        "campaign_name": ("广告活动", "广告名称", "campaignname"),
        "posting_number": ("postingnumber", "posting_number", "发货编号", "posting号"),
        "order_number": ("订单编号", "订单号", "ordernumber", "order_number"),
        "sku": ("sku", "商品编号", "商业编号", "ozonsku"),
        "offer_id": ("offerid", "offer_id", "货号", "商家编码"),
        "product_id": ("productid", "product_id", "产品id"),
        "spend_rub": ("广告花费rub", "花费rub", "spendrub"),
        "spend_cny": ("广告花费cny", "花费人民币", "spendcny"),
        "views": ("曝光", "展示", "views"),
        "clicks": ("点击", "clicks"),
        "orders": ("广告订单", "订单数", "orders"),
        "revenue_rub": ("广告销售额rub", "revenuerub"),
        "revenue_cny": ("广告销售额cny", "revenuecny"),
    },
}

IMPORT_REQUIRED = {
    "purchase_cost": ({"sku", "offer_id", "order_number"}, {"purchase_cost_cny"}),
    "orders": ({"posting_number", "order_number"}, {"sku"}, {"buyer_paid_cny", "buyer_paid_rub"}),
    "finance": ({"posting_number", "order_number"}, {"amount_cny", "amount_rub"}),
    "ads": ({"occurred_at"}, {"spend_cny", "spend_rub"}),
}

MONEY_FIELDS = {
    "purchase_cost_cny", "buyer_paid_rub", "buyer_paid_cny", "amount_rub", "amount_cny",
    "platform_commission_cny", "logistics_fee_cny", "refund_cny", "compensation_cny",
    "acquiring_cny", "other_fee_cny", "spend_rub", "spend_cny", "revenue_rub", "revenue_cny",
}


class FinanceCenter:
    def __init__(self, root: Path, db_path: Optional[Path] = None) -> None:
        self.root = Path(root).resolve()
        self.db_path = Path(db_path or self.root / "runtime/finance/finance.sqlite3").resolve()
        self.backup_dir = self.db_path.parent / "backups"
        self._sync_lock = threading.Lock()
        self._initialize_lock = threading.Lock()
        self._initialized = False

    @contextmanager
    def connect(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        if readonly:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not readonly:
            conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            if not readonly:
                conn.commit()
        except Exception:
            if not readonly:
                conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            with self.connect() as conn:
                conn.executescript(SCHEMA)
                unmatched_columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(import_unmatched_rows)")
                }
                if "file_path" not in unmatched_columns:
                    conn.execute("ALTER TABLE import_unmatched_rows ADD COLUMN file_path TEXT NOT NULL DEFAULT ''")
                if "raw_payload" not in unmatched_columns:
                    conn.execute("ALTER TABLE import_unmatched_rows ADD COLUMN raw_payload TEXT NOT NULL DEFAULT '{}'")
                ad_columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(ad_spend_transactions)")
                }
                for column in ("posting_number", "order_number", "offer_id"):
                    if column not in ad_columns:
                        conn.execute(
                            f"ALTER TABLE ad_spend_transactions ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                        )
                self._set_meta(conn, "schema_version", "1.0.2")
            self.db_path.chmod(0o600)
            self._initialized = True

    @staticmethod
    def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO finance_center_meta(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, value, now_iso()),
        )

    @staticmethod
    def _meta_json(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
        row = conn.execute("SELECT value FROM finance_center_meta WHERE key=?", (key,)).fetchone()
        if not row:
            return {}
        try:
            parsed = json.loads(str(row["value"]))
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _local_naive(current: Optional[datetime] = None) -> datetime:
        current = current or datetime.now().astimezone()
        return current.astimezone().replace(tzinfo=None) if current.tzinfo else current

    def _active_read_circuit(self, current: Optional[datetime] = None) -> dict[str, Any]:
        local_current = self._local_naive(current)
        with self.connect(readonly=True) as conn:
            circuit = self._meta_json(conn, "ozon_read_circuit")
        blocked_until = str(circuit.get("blocked_until") or "")
        if not blocked_until:
            return {}
        try:
            if datetime.fromisoformat(blocked_until) > local_current:
                return circuit
        except ValueError:
            return {}
        return {}

    def _open_read_circuit(
        self, error: OzonReadOnlyError, current: Optional[datetime] = None,
    ) -> dict[str, Any]:
        local_current = self._local_naive(current)
        if error.retryable:
            delay = max(SCHEDULED_RETRY_DELAY, timedelta(seconds=error.retry_after_seconds))
            blocked_until = local_current + delay
        else:
            next_schedule_day = local_current.date() + timedelta(days=1 if local_current.hour >= 15 else 0)
            next_schedule = datetime.combine(next_schedule_day, datetime.min.time()).replace(hour=15)
            blocked_until = max(next_schedule, local_current + timedelta(hours=6))
        circuit = {
            "opened_at": local_current.isoformat(timespec="seconds"),
            "blocked_until": blocked_until.isoformat(timespec="seconds"),
            "endpoint": error.endpoint,
            "http_status": error.status_code,
            "retryable": error.retryable,
            "reason": str(error)[:240],
        }
        with self.connect() as conn:
            self._set_meta(conn, "ozon_read_circuit", safe_json(circuit))
        return circuit

    def _clear_read_circuit(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM finance_center_meta WHERE key='ozon_read_circuit'")

    def backup(self, label: str) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        cleaned = re.sub(r"[^0-9A-Za-z_-]+", "-", label).strip("-") or "backup"
        path = self.backup_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{cleaned}.sqlite3"
        with self.connect(readonly=True) as source, closing(sqlite3.connect(path)) as destination:
            source.backup(destination)
        path.chmod(0o600)
        return path

    @staticmethod
    def _invalid_ad_match_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute(
            "SELECT a.* FROM ad_spend_transactions a LEFT JOIN orders o "
            "ON o.id=a.matched_order_id AND o.store_id=a.store_id "
            f"WHERE a.matched_order_id IS NOT NULL AND NOT ({AD_MATCH_IS_EXACT_SQL}) "
            "ORDER BY a.occurred_at,a.id"
        ).fetchall()

    def invalid_ad_match_preview(self) -> dict[str, Any]:
        """Preview campaign-level ad rows that were incorrectly attached to one order."""
        self.initialize()
        with self.connect(readonly=True) as conn:
            rows = self._invalid_ad_match_rows(conn)
        spend = sum((decimal_value(row["spend_cny"]) for row in rows), Decimal("0"))
        positive_rows = [row for row in rows if decimal_value(row["spend_cny"]) != 0]
        return {
            "invalid_match_count": len(rows),
            "positive_spend_count": len(positive_rows),
            "invalid_spend_cny": money(spend),
            "affected_order_count": len({str(row["matched_order_id"]) for row in rows}),
            "items": [
                {
                    "id": str(row["id"]), "store_id": str(row["store_id"]),
                    "matched_order_id": str(row["matched_order_id"]),
                    "posting_number": str(row["posting_number"] or ""),
                    "order_number": str(row["order_number"] or ""),
                    "sku": str(row["sku"] or ""), "offer_id": str(row["offer_id"] or ""),
                    "campaign_name": str(row["campaign_name"] or ""),
                    "spend_cny": money(row["spend_cny"]),
                    "reason": "缺少可证明单笔归属的精确订单号或 Posting",
                }
                for row in rows[:100]
            ],
            "apply_available": bool(rows), "source_database_modified": False,
        }

    def repair_invalid_ad_matches(self, *, apply: bool = False, created_by: str = "system") -> dict[str, Any]:
        """Remove invalid order attribution while retaining spend at period level."""
        preview = self.invalid_ad_match_preview()
        if not apply or not preview["invalid_match_count"]:
            return {**preview, "status": "preview" if not apply else "no_changes"}

        backup = self.backup("before-invalid-ad-match-repair")
        batch_id = f"ad-match-repair-{uuid.uuid4().hex[:12]}"
        repair_time = now_iso()
        affected_orders: set[str] = set()
        unmatched_changes = 0
        with self.connect() as conn:
            rows = self._invalid_ad_match_rows(conn)
            conn.execute(
                "INSERT INTO finance_import_batches(id,file_kind,file_name,status,created_by,created_at,applied_at,"
                "backup_path,row_count,inserted_count,updated_count,mapping_json) "
                "VALUES(?,?,'系统修复：无效广告归单','applying',?,?,?,?,?,0,0,'{}')",
                (batch_id, "system_ad_match_repair", created_by, repair_time, repair_time, str(backup), len(rows)),
            )
            for row in rows:
                before = dict(row)
                order_id = str(row["matched_order_id"] or "")
                if order_id:
                    affected_orders.add(order_id)
                conn.execute("UPDATE ad_spend_transactions SET matched_order_id=NULL WHERE id=?", (row["id"],))
                after = self._table_row(conn, "ad_spend_transactions", str(row["id"])) or {}
                self._record_change(conn, batch_id, "ad_spend_transactions", str(row["id"]), "update", before, after)

                if decimal_value(row["spend_cny"]) == 0 and decimal_value(row["spend_rub"]) == 0:
                    continue
                unmatched_id = stable_id("invalid-ad-match", row["id"])
                unmatched_before = self._table_row(conn, "import_unmatched_rows", unmatched_id)
                unmatched_payload = {
                    "id": unmatched_id, "store_id": str(row["store_id"] or "default_store"),
                    "file_type": "ads", "file_name": "广告精确匹配修复", "file_path": "",
                    "source_row_number": 0, "occurred_at": row["occurred_at"],
                    "posting_number": str(row["posting_number"] or ""),
                    "order_number": str(row["order_number"] or ""), "sku": str(row["sku"] or ""),
                    "offer_id": str(row["offer_id"] or ""), "amount_rub": money(row["spend_rub"]),
                    "amount_cny": money(row["spend_cny"]),
                    "reason": "活动级广告缺少精确订单号或 Posting；已从单笔利润移除，仅保留期间汇总",
                    "resolution_status": "open",
                    "raw_payload": safe_json({"ad_transaction_id": row["id"], "repair_batch_id": batch_id}),
                    "created_at": repair_time,
                }
                columns = list(unmatched_payload)
                conn.execute(
                    f"INSERT INTO import_unmatched_rows({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                    "ON CONFLICT(id) DO UPDATE SET amount_rub=excluded.amount_rub,amount_cny=excluded.amount_cny,"
                    "reason=excluded.reason,resolution_status='open',raw_payload=excluded.raw_payload",
                    tuple(unmatched_payload[column] for column in columns),
                )
                unmatched_after = self._table_row(conn, "import_unmatched_rows", unmatched_id) or unmatched_payload
                self._record_change(
                    conn, batch_id, "import_unmatched_rows", unmatched_id,
                    "update" if unmatched_before else "insert", unmatched_before, unmatched_after,
                )
                unmatched_changes += 1
            for order_id in affected_orders:
                self._recompute_order(conn, order_id)
            conn.execute(
                "UPDATE finance_import_batches SET status='applied',inserted_count=?,updated_count=? WHERE id=?",
                (unmatched_changes, len(rows), batch_id),
            )
            self._set_meta(conn, "ad_match_contract", "exact_order_or_posting_v1")
            self._set_meta(conn, f"system_repair:{batch_id}", safe_json({
                "invalid_match_count": len(rows), "invalid_spend_cny": preview["invalid_spend_cny"],
                "affected_orders": len(affected_orders), "applied_at": repair_time,
            }))
        return {
            **preview, "status": "applied", "batch_id": batch_id, "backup_path": str(backup),
            "rollback_available": True, "unmatched_rows_added": unmatched_changes,
        }

    def repair_ozon_service_buckets(
        self, *, apply: bool = False, created_by: str = "system",
    ) -> dict[str, Any]:
        """Reclassify existing Seller Finance rows with the current operation contract."""
        self.initialize()
        changes: list[tuple[dict[str, Any], dict[str, str]]] = []
        with self.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT * FROM finance_transactions WHERE trim(COALESCE(raw_payload,'')) NOT IN ('','{}')"
            ).fetchall()
            for row in rows:
                try:
                    operation = json.loads(str(row["raw_payload"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(operation, dict) or not operation.get("operation_id"):
                    continue
                operation["operation_type"] = operation.get("operation_type") or row["operation_type"]
                operation["operation_type_name"] = operation.get("operation_type_name") or row["service_name"]
                rate = decimal_value(operation.get("rub_per_cny"))
                if rate <= 0:
                    amount_cny = abs(decimal_value(row["amount_cny"]))
                    amount_rub = abs(decimal_value(row["amount_rub"]))
                    rate = amount_rub / amount_cny if amount_cny > 0 else DEFAULT_RUB_PER_CNY
                buckets = self._service_buckets(operation, rate)
                updated = {
                    "platform_commission_cny": money(buckets["platform"]),
                    "logistics_fee_cny": money(buckets["logistics"]),
                    "refund_cny": money(buckets["refund"]),
                    "compensation_cny": money(buckets["compensation"]),
                    "acquiring_cny": money(buckets["acquiring"]),
                    "other_fee_cny": money(buckets["other"]),
                }
                if any(str(row[column]) != value for column, value in updated.items()):
                    changes.append((dict(row), updated))
        preview = {
            "status": "preview", "changed_transaction_count": len(changes),
            "affected_order_count": len({
                str(row["matched_order_id"]) for row, _updated in changes if row.get("matched_order_id")
            }),
            "logistics_before_cny": money(sum(
                (decimal_value(row["logistics_fee_cny"]) for row, _updated in changes), Decimal("0")
            )),
            "logistics_after_cny": money(sum(
                (decimal_value(updated["logistics_fee_cny"]) for _row, updated in changes), Decimal("0")
            )),
        }
        if not apply or not changes:
            return {**preview, "status": "preview" if not apply else "no_changes"}

        backup = self.backup("before-ozon-service-bucket-repair")
        batch_id = f"ozon-fee-repair-{uuid.uuid4().hex[:12]}"
        repair_time = now_iso()
        affected_orders = {
            str(row["matched_order_id"]) for row, _updated in changes if row.get("matched_order_id")
        }
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO finance_import_batches(id,file_kind,file_name,status,created_by,created_at,applied_at,"
                "backup_path,row_count,inserted_count,updated_count,mapping_json) "
                "VALUES(?,?,'系统修复：Ozon费用分类','applying',?,?,?,?,?,0,?,'{}')",
                (batch_id, "system_ozon_fee_repair", created_by, repair_time, repair_time, str(backup), len(changes), len(changes)),
            )
            snapshot_before = {
                order_id: dict(row) for order_id in affected_orders
                if (row := conn.execute(
                    "SELECT * FROM profit_snapshots WHERE order_id=? ORDER BY created_at DESC LIMIT 1", (order_id,),
                ).fetchone())
            }
            for before, updated in changes:
                conn.execute(
                    "UPDATE finance_transactions SET platform_commission_cny=?,logistics_fee_cny=?,refund_cny=?,"
                    "compensation_cny=?,acquiring_cny=?,other_fee_cny=? WHERE id=?",
                    (
                        updated["platform_commission_cny"], updated["logistics_fee_cny"], updated["refund_cny"],
                        updated["compensation_cny"], updated["acquiring_cny"], updated["other_fee_cny"], before["id"],
                    ),
                )
                after = self._table_row(conn, "finance_transactions", str(before["id"])) or {}
                self._record_change(conn, batch_id, "finance_transactions", str(before["id"]), "update", before, after)
            for order_id in affected_orders:
                self._recompute_order(conn, order_id)
                after = conn.execute(
                    "SELECT * FROM profit_snapshots WHERE order_id=? ORDER BY created_at DESC LIMIT 1", (order_id,),
                ).fetchone()
                if after:
                    self._record_change(
                        conn, batch_id, "profit_snapshots", str(after["id"]), "update",
                        snapshot_before.get(order_id), dict(after),
                    )
            conn.execute(
                "UPDATE finance_import_batches SET status='applied' WHERE id=?", (batch_id,),
            )
            self._set_meta(conn, "ozon_fee_classification_contract", "operation_level_expense_v2")
        return {
            **preview, "status": "applied", "batch_id": batch_id, "backup_path": str(backup),
            "rollback_available": True,
        }

    def migrate_legacy(self, source_path: Path) -> dict[str, Any]:
        source_path = Path(source_path).resolve()
        if source_path == self.db_path:
            raise ValueError("历史源数据库不能与财务中心运行库相同")
        source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
        source.row_factory = sqlite3.Row
        try:
            integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"历史数据库完整性检查失败：{integrity}")
            digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
            source_counts = {
                table: int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("orders", "finance_transactions", "ad_spend_transactions", "profit_snapshots")
            }
            if not self.db_path.exists():
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(self.db_path)) as target:
                    with target:
                        source.backup(target)
            else:
                self.initialize()
                current_backup = self.backup("before-legacy-migration")
                with self.connect() as target:
                    for table in (
                        "stores", "orders", "finance_transactions", "ad_spend_transactions",
                        "product_costs", "product_master", "profit_snapshots", "import_unmatched_rows",
                    ):
                        source_columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
                        target_columns = {row[1] for row in target.execute(f"PRAGMA table_info({table})")}
                        columns = [column for column in source_columns if column in target_columns]
                        placeholders = ",".join("?" for _ in columns)
                        statement = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
                        for row in source.execute(f"SELECT {','.join(columns)} FROM {table}"):
                            target.execute(statement, tuple(row))
                    self._set_meta(target, "legacy_premerge_backup", str(current_backup))
            self.db_path.chmod(0o600)
            self.initialize()
            with self.connect() as conn:
                self._set_meta(conn, "legacy_source_sha256", digest)
                self._set_meta(conn, "legacy_migrated_at", now_iso())
                self._set_meta(conn, "legacy_source_mode", "read_only_copy")
                self._set_meta(conn, "legacy_source_counts", safe_json(source_counts))
            ad_match_repair = self.repair_invalid_ad_matches(apply=True, created_by="legacy_migration")
            migrated_backup = self.backup("legacy-migrated-verified")
            return {
                "status": "migrated", "database_integrity": integrity,
                "source_sha256": digest, "counts": source_counts,
                "backup_path": str(migrated_backup), "source_was_read_only": True,
                "ad_match_repair": ad_match_repair,
            }
        finally:
            source.close()

    def store_options(self) -> list[dict[str, Any]]:
        self.initialize()
        display_names: dict[str, str] = {}
        current_registry_ids: set[str] = set()
        try:
            from workbench_stores import load_registry

            registry = load_registry(self.root)
            for shop in registry.get("shops") or []:
                current_registry_ids.add(str(shop.get("id") or ""))
                display_name = str(shop.get("display_name") or shop.get("name") or shop.get("id") or "").strip()
                if not display_name:
                    continue
                for value in (shop.get("id"), shop.get("name"), shop.get("display_name")):
                    key = normalized_key(value)
                    if key:
                        display_names[key] = display_name
                if str(shop.get("id") or "") == "volttech":
                    display_names["voltech"] = display_name
        except (ImportError, OSError, TypeError, ValueError):
            display_names = {}
        with self.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT id,store_name,store_alias,status,last_sync_at,sync_status,sync_error "
                "FROM stores ORDER BY lower(COALESCE(store_alias,store_name))"
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            current_name = str(row["store_alias"] or row["store_name"] or row["id"])
            display_name = next((
                display_names[key] for key in (
                    normalized_key(row["id"]), normalized_key(row["store_name"]), normalized_key(row["store_alias"]),
                ) if key in display_names
            ), current_name)
            result.append({
                "id": row["id"], "name": display_name,
                "status": row["status"], "sync_status": row["sync_status"],
                "last_sync_at": row["last_sync_at"], "sync_error": row["sync_error"],
            })
        name_counts: dict[str, int] = {}
        for item in result:
            name_counts[str(item["name"])] = name_counts.get(str(item["name"]), 0) + 1
        for item in result:
            if name_counts.get(str(item["name"]), 0) > 1 and str(item["id"]) not in current_registry_ids:
                item["name"] = f"{item['name']}（历史）"
        return result

    def set_sku_purchase_cost(
        self, *, sku: str, purchase_cost_cny: Any, created_by: str = "owner",
    ) -> dict[str, Any]:
        self.initialize()
        sku = str(sku or "").strip()
        cost = decimal_value(purchase_cost_cny)
        if not sku:
            raise ValueError("没有读取到 SKU，不能同步采购价")
        if cost <= 0:
            raise ValueError("采购价必须大于 0")
        batch_id = f"manual-{uuid.uuid4().hex[:16]}"
        backup = self.backup(f"before-{batch_id}")
        applied_at = now_iso()
        with self.connect() as conn:
            orders = conn.execute(
                "SELECT * FROM orders WHERE trim(COALESCE(sku,''))=? OR trim(COALESCE(offer_id,''))=? "
                "ORDER BY store_id,order_date,posting_number",
                (sku, sku),
            ).fetchall()
            masters = conn.execute(
                "SELECT * FROM product_master WHERE trim(COALESCE(sku,''))=? OR trim(COALESCE(offer_id,''))=? "
                "ORDER BY store_id,id",
                (sku, sku),
            ).fetchall()
            if not orders and not masters:
                raise ValueError("没有找到相同 SKU 的商品")
            conn.execute(
                "INSERT INTO finance_import_batches(id,file_kind,file_name,status,created_by,created_at,applied_at,"
                "backup_path,row_count,mapping_json) VALUES(?,?,'工作台单笔采购价','applying',?,?,?,?,?,'{}')",
                (batch_id, "manual_sku_cost", created_by, applied_at, applied_at, str(backup), len(orders)),
            )
            master_keys = {(str(row["store_id"]), str(row["sku"]), str(row["offer_id"] or "")) for row in masters}
            for order in orders:
                key = (str(order["store_id"]), str(order["sku"]), str(order["offer_id"] or ""))
                if key in master_keys:
                    continue
                master_id = stable_id("product", order["store_id"], order["sku"])
                payload = {
                    "id": master_id, "store_id": order["store_id"], "sku": order["sku"],
                    "offer_id": order["offer_id"], "product_id": None, "product_name": order["product_name"],
                    "image_url": None, "unit_purchase_cost_cny": money(cost), "estimated_weight_g": None,
                    "weight_source": "missing", "dimensions": None, "volume_weight": None,
                    "purchase_cost_source": "manual_sku_cost", "created_at": applied_at, "updated_at": applied_at,
                }
                conn.execute(
                    f"INSERT INTO product_master({','.join(payload)}) VALUES({','.join('?' for _ in payload)})",
                    tuple(payload.values()),
                )
                self._record_change(conn, batch_id, "product_master", master_id, "insert", None, payload)
                master_keys.add(key)
            masters = conn.execute(
                "SELECT * FROM product_master WHERE trim(COALESCE(sku,''))=? OR trim(COALESCE(offer_id,''))=? "
                "ORDER BY store_id,id",
                (sku, sku),
            ).fetchall()
            updated_masters = 0
            for master in masters:
                before = dict(master)
                conn.execute(
                    "UPDATE product_master SET unit_purchase_cost_cny=?,purchase_cost_source='manual_sku_cost',updated_at=? WHERE id=?",
                    (money(cost), applied_at, master["id"]),
                )
                after = self._table_row(conn, "product_master", str(master["id"])) or {}
                self._record_change(conn, batch_id, "product_master", str(master["id"]), "update", before, after)
                cost_id = stable_id("manual-sku-cost", batch_id, master["store_id"], master["sku"])
                cost_payload = {
                    "id": cost_id, "row_hash": stable_id(sku, money(cost)), "file_hash": stable_id(batch_id),
                    "sku": master["sku"], "offer_id": master["offer_id"], "product_name": master["product_name"],
                    "purchase_cost_cny": money(cost), "effective_date": date.today().isoformat(),
                    "raw_payload": safe_json({"source": "workbench_single_input", "input_sku": sku}),
                    "created_at": applied_at, "amount_original": money(cost), "currency_original": "CNY",
                    "currency_source": "operator_confirmed", "source": "manual_sku_cost", "updated_at": applied_at,
                    "batch_id": batch_id, "note": "工作台单笔输入后同步相同 SKU", "store_id": master["store_id"],
                }
                conn.execute(
                    f"INSERT INTO product_costs({','.join(cost_payload)}) VALUES({','.join('?' for _ in cost_payload)})",
                    tuple(cost_payload.values()),
                )
                self._record_change(conn, batch_id, "product_costs", cost_id, "insert", None, cost_payload)
                updated_masters += 1
            updated_purchase_rows = 0
            for order in orders:
                aliases = {str(order["sku"] or "").strip(), str(order["offer_id"] or "").strip()}
                aliases.discard("")
                if aliases:
                    placeholders = ",".join("?" for _ in aliases)
                    purchase_rows = conn.execute(
                        f"SELECT * FROM purchase_order_match WHERE store_id=? AND posting_number=? "
                        f"AND trim(COALESCE(sku,'')) IN ({placeholders})",
                        (order["store_id"], order["posting_number"], *sorted(aliases)),
                    ).fetchall()
                    total_cost = cost * self._order_quantity(order)
                    for purchase_row in purchase_rows:
                        before = dict(purchase_row)
                        conn.execute(
                            "UPDATE purchase_order_match SET purchase_cost_cny=?,matched_at=? WHERE id=?",
                            (money(total_cost), applied_at, purchase_row["id"]),
                        )
                        after = self._table_row(conn, "purchase_order_match", str(purchase_row["id"])) or {}
                        self._record_change(
                            conn, batch_id, "purchase_order_match", str(purchase_row["id"]), "update", before, after,
                        )
                        updated_purchase_rows += 1
                before_snapshot_row = conn.execute(
                    "SELECT * FROM profit_snapshots WHERE order_id=? ORDER BY created_at DESC LIMIT 1",
                    (order["id"],),
                ).fetchone()
                before_snapshot = dict(before_snapshot_row) if before_snapshot_row else None
                self._recompute_order(conn, str(order["id"]))
                after_snapshot_row = conn.execute(
                    "SELECT * FROM profit_snapshots WHERE order_id=? ORDER BY created_at DESC LIMIT 1",
                    (order["id"],),
                ).fetchone()
                if after_snapshot_row:
                    after_snapshot = dict(after_snapshot_row)
                    self._record_change(
                        conn, batch_id, "profit_snapshots", str(after_snapshot["id"]),
                        "update" if before_snapshot else "insert", before_snapshot, after_snapshot,
                    )
            conn.execute(
                "UPDATE finance_import_batches SET status='applied',inserted_count=?,updated_count=? WHERE id=?",
                (updated_masters, len(orders) + updated_purchase_rows, batch_id),
            )
        return {
            "status": "applied", "batch_id": batch_id, "sku": sku,
            "purchase_cost_cny": money(cost), "affected_order_count": len(orders),
            "affected_store_count": len({str(row["store_id"]) for row in orders}),
            "backup_path": str(backup), "ozon_write_api_calls": 0, "inventory_api_calls": 0,
        }

    @staticmethod
    def _period(date_from: Optional[str], date_to: Optional[str]) -> tuple[str, str]:
        today = date.today()
        start = date_from or today.replace(day=1).isoformat()
        end = date_to or today.isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
            raise ValueError("日期必须使用 YYYY-MM-DD")
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")
        return start, end

    @staticmethod
    def _matches_store(row: Mapping[str, Any], store_id: str) -> bool:
        return store_id in {"", "all"} or str(row["store_key"]) == store_id

    def _canonical_rows(self, conn: sqlite3.Connection, store_id: str, start: str, end: str) -> list[sqlite3.Row]:
        return [
            row for row in conn.execute(CANONICAL_SQL).fetchall()
            if self._matches_store(row, store_id)
            and start <= str(row["order_date"] or "")[:10] <= end
            and decimal_value(row["buyer_paid_cny"]) > 0
        ]

    def _display_rate(self, conn: sqlite3.Connection, end: str) -> tuple[Decimal, str]:
        row = conn.execute(
            "SELECT rub_per_unit,source FROM exchange_rates WHERE currency_code='CNY' AND rate_date<=? "
            "ORDER BY rate_date DESC LIMIT 1", (end,),
        ).fetchone()
        if row:
            return decimal_value(row["rub_per_unit"]) or DEFAULT_RUB_PER_CNY, str(row["source"])
        return DEFAULT_RUB_PER_CNY, "workbench_fixed_fallback"

    @staticmethod
    def _period_ad_spend(
        conn: sqlite3.Connection, store_id: str, start: str, end: str,
        order_ads_by_store: Mapping[str, Decimal],
    ) -> dict[str, Any]:
        store_filter = "" if store_id in {"", "all"} else " AND store_id = ?"
        api_params: list[Any] = [start, end, *OZON_AD_OPERATION_TYPES]
        imported_params: list[Any] = [start, end]
        if store_filter:
            api_params.append(store_id)
            imported_params.append(store_id)
        placeholders = ",".join("?" for _ in OZON_AD_OPERATION_TYPES)
        api_rows = {
            str(row["store_id"]): row for row in conn.execute(
                "SELECT store_id,COUNT(*) rows,COALESCE(SUM(CAST(amount_cny AS REAL)),0) net "
                "FROM finance_transactions WHERE substr(COALESCE(occurred_at,''),1,10) BETWEEN ? AND ? "
                f"AND operation_type IN ({placeholders})" + store_filter + " GROUP BY store_id",
                api_params,
            ).fetchall()
        }
        imported_rows = {
            str(row["store_id"]): row for row in conn.execute(
                "SELECT store_id,COUNT(*) rows,COALESCE(SUM(CAST(spend_cny AS REAL)),0) total "
                "FROM ad_spend_transactions WHERE substr(COALESCE(occurred_at,''),1,10) BETWEEN ? AND ?"
                + store_filter + " GROUP BY store_id",
                imported_params,
            ).fetchall()
        }
        total = Decimal("0")
        unallocated = Decimal("0")
        api_record_count = 0
        imported_record_count = 0
        by_store: dict[str, dict[str, Any]] = {}
        for current_store in sorted(set(api_rows) | set(imported_rows)):
            api_row = api_rows.get(current_store)
            if api_row and int(api_row["rows"] or 0) > 0:
                spend = max(-decimal_value(api_row["net"]), Decimal("0"))
                total += spend
                unallocated += spend
                api_record_count += int(api_row["rows"] or 0)
                by_store[current_store] = {
                    "total": spend, "unallocated": spend, "source": "ozon_finance",
                }
                continue
            imported_row = imported_rows.get(current_store)
            if imported_row and int(imported_row["rows"] or 0) > 0:
                spend = max(decimal_value(imported_row["total"]), Decimal("0"))
                store_unallocated = max(
                    spend - order_ads_by_store.get(current_store, Decimal("0")), Decimal("0")
                )
                total += spend
                unallocated += store_unallocated
                imported_record_count += int(imported_row["rows"] or 0)
                by_store[current_store] = {
                    "total": spend, "unallocated": store_unallocated, "source": "imported_ads",
                }
        if api_record_count and imported_record_count:
            source = "ozon_finance_and_imported_fallback"
            source_label = "Ozon Finance + 导入广告表"
        elif api_record_count:
            source = "ozon_finance"
            source_label = "Ozon Finance"
        elif imported_record_count:
            source = "imported_ads"
            source_label = "导入广告表"
        else:
            source = "missing"
            source_label = "未读取"
        return {
            "total": total,
            "unallocated": unallocated,
            "available": bool(api_record_count or imported_record_count),
            "source": source,
            "source_label": source_label,
            "api_record_count": api_record_count,
            "imported_record_count": imported_record_count,
            "by_store": by_store,
        }

    @staticmethod
    def _cost_rate(
        product_rates: Mapping[tuple[str, str], tuple[Decimal, Decimal]],
        store_rates: Mapping[str, tuple[Decimal, Decimal]],
        global_rate: tuple[Decimal, Decimal],
        store_key: str, product_key: str,
    ) -> tuple[Optional[Decimal], str]:
        for bucket, source in (
            (product_rates.get((store_key, product_key)), "same_sku_history"),
            (store_rates.get(store_key), "store_history"),
            (global_rate, "all_store_history"),
        ):
            if bucket and bucket[1] > 0:
                return bucket[0] / bucket[1], source
        return None, "missing"

    def _costed_rows(
        self, conn: sqlite3.Connection, rows: list[sqlite3.Row], advertising: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Apply actual costs first, then explicit estimates for unsettled orders."""
        history = conn.execute(CANONICAL_SQL).fetchall()

        def benchmarks(field: str) -> tuple[
            dict[tuple[str, str], tuple[Decimal, Decimal]],
            dict[str, tuple[Decimal, Decimal]],
            tuple[Decimal, Decimal],
        ]:
            product: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
            store: dict[str, tuple[Decimal, Decimal]] = {}
            global_cost = Decimal("0")
            global_sales = Decimal("0")
            for item in history:
                cost = decimal_value(item[field])
                sales = decimal_value(item["buyer_paid_cny"])
                if cost <= 0 or sales <= 0:
                    continue
                store_key = str(item["store_key"] or item["store_id"] or "default_store")
                product_key = str(item["product_key"] or item["sku"] or "")
                old_cost, old_sales = product.get((store_key, product_key), (Decimal("0"), Decimal("0")))
                product[(store_key, product_key)] = (old_cost + cost, old_sales + sales)
                old_cost, old_sales = store.get(store_key, (Decimal("0"), Decimal("0")))
                store[store_key] = (old_cost + cost, old_sales + sales)
                global_cost += cost
                global_sales += sales
            return product, store, (global_cost, global_sales)

        finance_product, finance_store, finance_global = benchmarks("finance_fee_cny")
        logistics_product, logistics_store, logistics_global = benchmarks("logistics_cost_cny")
        selected_sales_by_store: dict[str, Decimal] = {}
        for row in rows:
            current_store = str(row["store_key"] or row["store_id"] or "default_store")
            selected_sales_by_store[current_store] = (
                selected_sales_by_store.get(current_store, Decimal("0"))
                + decimal_value(row["buyer_paid_cny"])
            )

        costed: list[dict[str, Any]] = []
        ad_by_store = dict(advertising.get("by_store") or {})
        for row in rows:
            current_store = str(row["store_key"] or row["store_id"] or "default_store")
            product_key = str(row["product_key"] or row["sku"] or "")
            sales = decimal_value(row["buyer_paid_cny"])
            purchase = decimal_value(row["purchase_cost_cny"])

            actual_finance = decimal_value(row["finance_fee_cny"])
            if actual_finance != 0:
                finance_cost = actual_finance
                finance_source = "actual_finance"
            else:
                finance_rate, finance_source = self._cost_rate(
                    finance_product, finance_store, finance_global, current_store, product_key,
                )
                finance_cost = sales * finance_rate if finance_rate is not None else Decimal("0")

            actual_logistics = decimal_value(row["logistics_cost_cny"] or row["logistics_fee_cny"])
            if actual_logistics > 0:
                logistics_cost = actual_logistics
                logistics_source = "actual_finance"
            else:
                logistics_rate, logistics_source = self._cost_rate(
                    logistics_product, logistics_store, logistics_global, current_store, product_key,
                )
                logistics_cost = sales * logistics_rate if logistics_rate is not None else Decimal("0")

            exact_ads = decimal_value(row["ad_spend_cny"])
            store_ad = dict(ad_by_store.get(current_store) or {})
            store_sales = selected_sales_by_store.get(current_store, Decimal("0"))
            ad_share = Decimal("0")
            if store_sales > 0:
                ad_share = decimal_value(store_ad.get("unallocated")) * sales / store_sales
            ad_cost = exact_ads + ad_share
            if exact_ads > 0 and ad_share > 0:
                ad_source = "actual_and_period_allocation"
            elif exact_ads > 0:
                ad_source = "actual_order"
            elif ad_share > 0:
                ad_source = "period_sales_allocation"
            else:
                ad_source = "missing"

            profit = sales - purchase - finance_cost - logistics_cost - ad_cost
            costed.append({
                "row": row,
                "finance_cost": finance_cost,
                "finance_source": finance_source,
                "logistics_cost": logistics_cost,
                "logistics_source": logistics_source,
                "ad_cost": ad_cost,
                "ad_source": ad_source,
                "profit": profit,
                "profit_margin": Decimal("0") if sales == 0 else profit / sales,
            })
        return costed

    @staticmethod
    def _convert(value: Decimal, currency: str, rate: Decimal) -> Decimal:
        return value * rate if currency == "RUB" else value

    def overview(
        self, *, store_id: str = "all", date_from: Optional[str] = None,
        date_to: Optional[str] = None, currency: str = "CNY",
    ) -> dict[str, Any]:
        self.initialize()
        start, end = self._period(date_from, date_to)
        currency = currency.upper()
        if currency not in {"CNY", "RUB"}:
            raise ValueError("当前只支持 CNY 或 RUB")
        with self.connect(readonly=True) as conn:
            rows = self._canonical_rows(conn, store_id, start, end)
            display_rate, exchange_source = self._display_rate(conn, end)
            sales = sum((decimal_value(row["buyer_paid_cny"]) for row in rows), Decimal("0"))
            trial_profit = sum((decimal_value(row["final_profit_cny"]) for row in rows), Decimal("0"))
            purchase = sum((decimal_value(row["purchase_cost_cny"]) for row in rows), Decimal("0"))
            finance = sum((decimal_value(row["finance_fee_cny"]) for row in rows), Decimal("0"))
            logistics = sum(
                (decimal_value(row["logistics_cost_cny"] or row["logistics_fee_cny"]) for row in rows),
                Decimal("0"),
            )
            order_ads = sum((decimal_value(row["ad_spend_cny"]) for row in rows), Decimal("0"))
            order_ads_by_store: dict[str, Decimal] = {}
            for row in rows:
                current_store = str(row["store_id"] or "")
                order_ads_by_store[current_store] = (
                    order_ads_by_store.get(current_store, Decimal("0"))
                    + decimal_value(row["ad_spend_cny"])
                )
            advertising = self._period_ad_spend(conn, store_id, start, end, order_ads_by_store)
            costed_rows = self._costed_rows(conn, rows, advertising)
            purchase_rows = [row for row in rows if str(row["purchase_cost_source"] or "").lower() not in MISSING_PURCHASE_SOURCES]
            finance_rows = [row for row in rows if decimal_value(row["finance_fee_cny"]) != 0]
            logistics_rows = [row for row in rows if decimal_value(row["logistics_cost_cny"] or row["logistics_fee_cny"]) > 0]
            attributed_ad_rows = [row for row in rows if decimal_value(row["ad_spend_cny"]) > 0]
            purchase_sales = sum((decimal_value(row["buyer_paid_cny"]) for row in purchase_rows), Decimal("0"))
            finance_sales = sum((decimal_value(row["buyer_paid_cny"]) for row in finance_rows), Decimal("0"))
            logistics_sales = sum((decimal_value(row["buyer_paid_cny"]) for row in logistics_rows), Decimal("0"))
            complete_rows = [
                row for row in rows
                if row in purchase_rows and row in finance_rows and row in logistics_rows and row in attributed_ad_rows
            ]
            confirmed_profit = sum((decimal_value(row["final_profit_cny"]) for row in complete_rows), Decimal("0"))
            total_ads = decimal_value(advertising["total"])
            period_unallocated_ads = decimal_value(advertising["unallocated"])
            other_params: list[Any] = [start, end]
            other_filter = ""
            if store_id not in {"", "all"}:
                other_filter = " AND (store_id IS NULL OR store_id = ?)"
                other_params.append(store_id)
            other_rows = conn.execute(
                "SELECT entry_type,amount_cny FROM other_entries WHERE occurred_on BETWEEN ? AND ?" + other_filter,
                other_params,
            ).fetchall()
            other_income = sum((decimal_value(row["amount_cny"]) for row in other_rows if row["entry_type"] == "income"), Decimal("0"))
            other_expense = sum((decimal_value(row["amount_cny"]) for row in other_rows if row["entry_type"] == "expense"), Decimal("0"))
            purchase_estimate_available = purchase_sales > 0
            finance_estimate_available = bool(costed_rows) and all(
                item["finance_source"] != "missing" for item in costed_rows
            )
            logistics_estimate_available = bool(costed_rows) and all(
                item["logistics_source"] != "missing" for item in costed_rows
            )
            ads_estimate_available = bool(advertising["available"])
            missing_purchase_estimate = (
                (sales - purchase_sales) * purchase / purchase_sales
                if purchase_estimate_available else None
            )
            effective_finance = sum((item["finance_cost"] for item in costed_rows), Decimal("0"))
            effective_logistics = sum((item["logistics_cost"] for item in costed_rows), Decimal("0"))
            missing_finance_estimate = (
                sum(
                    (item["finance_cost"] for item in costed_rows if item["finance_source"] != "actual_finance"),
                    Decimal("0"),
                )
                if finance_estimate_available else None
            )
            missing_logistics_estimate = (
                sum(
                    (item["logistics_cost"] for item in costed_rows if item["logistics_source"] != "actual_finance"),
                    Decimal("0"),
                )
                if logistics_estimate_available else None
            )
            expected_profit_available = all((
                purchase_estimate_available,
                finance_estimate_available,
                logistics_estimate_available,
                ads_estimate_available,
            ))
            expected_profit = None
            if expected_profit_available:
                expected_profit = (
                    sales - purchase - missing_purchase_estimate - effective_finance
                    - effective_logistics - total_ads + other_income - other_expense
                )
            missing_sources = [
                source for source, available in (
                    ("purchase", purchase_estimate_available),
                    ("finance", finance_estimate_available),
                    ("logistics", logistics_estimate_available),
                    ("ads", ads_estimate_available),
                ) if not available
            ]
            unmatched_finance = conn.execute(
                "SELECT COUNT(*) FROM import_unmatched_rows WHERE file_type='finance' AND resolution_status='open'"
                + (" AND store_id=?" if store_id not in {"", "all"} else ""),
                (() if store_id in {"", "all"} else (store_id,)),
            ).fetchone()[0]
            unmatched_ads = conn.execute(
                "SELECT COUNT(*) FROM import_unmatched_rows WHERE file_type='ads' AND resolution_status='open'"
                + (" AND store_id=?" if store_id not in {"", "all"} else ""),
                (() if store_id in {"", "all"} else (store_id,)),
            ).fetchone()[0]

        def converted(value: Decimal) -> str:
            return money(self._convert(value, currency, display_rate))

        def converted_optional(value: Optional[Decimal]) -> Optional[str]:
            return converted(value) if value is not None else None

        return {
            "period": {"date_from": start, "date_to": end},
            "store_id": store_id or "all", "stores": self.store_options(),
            "currency": currency, "rub_per_cny": money(display_rate),
            "exchange_rate_source": exchange_source,
            "summary": {
                "sales": converted(sales),
                "confirmed_profit": converted(confirmed_profit),
                "confirmed_margin": ratio(confirmed_profit, sales),
                "expected_profit": converted_optional(expected_profit),
                "expected_margin": ratio(expected_profit, sales) if expected_profit is not None else None,
                "expected_profit_available": expected_profit_available,
                "expected_profit_missing_sources": missing_sources,
                "trial_profit_before_gap_estimates": converted(trial_profit - period_unallocated_ads),
                "ad_spend": converted(total_ads),
                "ozon_fees": converted(effective_finance),
                "logistics": converted(effective_logistics),
                "other_income": converted(other_income), "other_expense": converted(other_expense),
                "effective_order_lines": len(rows), "fully_covered_order_lines": len(complete_rows),
            },
            "coverage": {
                "purchase": ratio(purchase_sales, sales),
                "finance": ratio(finance_sales, sales),
                "logistics": ratio(logistics_sales, sales),
                "ads": 1.0 if ads_estimate_available else 0.0,
            },
            "advertising": {
                "total": converted(total_ads),
                "source": advertising["source"],
                "source_label": advertising["source_label"],
                "api_record_count": advertising["api_record_count"],
                "imported_record_count": advertising["imported_record_count"],
                "attributed_to_orders": False,
            },
            "gap_estimates": {
                "missing_purchase": converted_optional(missing_purchase_estimate),
                "missing_finance": converted_optional(missing_finance_estimate),
                "missing_logistics": converted_optional(missing_logistics_estimate),
                "period_level_unallocated_ads": converted(period_unallocated_ads) if ads_estimate_available else None,
                "method": (
                    "按同周期已覆盖订单的成本率外推；未使用未匹配 Finance 金额"
                    if expected_profit_available
                    else "关键成本没有可用覆盖样本，暂不计算预计利润；未匹配金额仍不计入成本或收入"
                ),
            },
            "reconciliation": {
                "unmatched_finance_rows": int(unmatched_finance),
                "unmatched_ads_rows": int(unmatched_ads),
                "excluded_from_order_profit": True,
            },
            "warnings": [
                (
                    "已结算订单使用真实费用；配送中订单按同店同 SKU 历史费用估算。"
                    if expected_profit_available
                    else "采购、Finance、物流或广告缺少可用样本，本期预计利润暂不可计算。"
                ),
                (
                    "广告费用来自 Ozon Finance；没有订单号的费用按同店当期销售额分摊并标记为估算。"
                    if advertising["source"] in {"ozon_finance", "ozon_finance_and_imported_fallback"}
                    else "广告费用来自导入记录；没有订单号的费用按同店当期销售额分摊并标记为估算。"
                ),
            ],
            "profit_margin_contract": "0_to_1_decimal",
            "ozon_write_api_calls": 0, "inventory_api_calls": 0,
        }

    def orders(
        self, *, store_id: str = "all", date_from: Optional[str] = None,
        date_to: Optional[str] = None, query: str = "", limit: int = 200,
    ) -> dict[str, Any]:
        self.initialize()
        start, end = self._period(date_from, date_to)
        query_key = normalized_key(query)
        with self.connect(readonly=True) as conn:
            rows = self._canonical_rows(conn, store_id, start, end)
            order_ads_by_store: dict[str, Decimal] = {}
            for row in rows:
                current_store = str(row["store_id"] or "")
                order_ads_by_store[current_store] = (
                    order_ads_by_store.get(current_store, Decimal("0"))
                    + decimal_value(row["ad_spend_cny"])
                )
            advertising = self._period_ad_spend(conn, store_id, start, end, order_ads_by_store)
            costed_rows = self._costed_rows(conn, rows, advertising)
        items = []
        for costed in costed_rows:
            row = costed["row"]
            fields = [row["posting_number"], row["order_number"], row["sku"], row["offer_id"], row["product_name"]]
            if query_key and not any(query_key in normalized_key(value) for value in fields):
                continue
            purchase_ok = str(row["purchase_cost_source"] or "").lower() not in MISSING_PURCHASE_SOURCES
            finance_ok = decimal_value(row["finance_fee_cny"]) != 0
            logistics_ok = decimal_value(row["logistics_cost_cny"] or row["logistics_fee_cny"]) > 0
            ads_ok = decimal_value(row["ad_spend_cny"]) > 0
            items.append({
                "id": row["id"], "store_id": row["store_key"], "order_number": row["order_number"],
                "posting_number": row["posting_number"], "sku": row["sku"], "offer_id": row["offer_id"],
                "product_name": row["product_name"], "image_url": row["image_url"], "order_date": row["order_date"],
                "buyer_paid_cny": money(row["buyer_paid_cny"]), "buyer_paid_rub": money(row["buyer_paid_rub"]),
                "purchase_cost_cny": money(row["purchase_cost_cny"]),
                "finance_fee_cny": money(costed["finance_cost"]),
                "logistics_cny": money(costed["logistics_cost"]),
                "ad_spend_cny": money(costed["ad_cost"]), "profit_cny": money(costed["profit"]),
                "profit_margin": float(costed["profit_margin"]),
                "cost_sources": {
                    "finance": costed["finance_source"],
                    "logistics": costed["logistics_source"],
                    "ads": costed["ad_source"],
                },
                "has_estimates": any((
                    costed["finance_source"] not in {"actual_finance", "missing"},
                    costed["logistics_source"] not in {"actual_finance", "missing"},
                    costed["ad_source"] in {"period_sales_allocation", "actual_and_period_allocation"},
                )),
                "coverage": {"purchase": purchase_ok, "finance": finance_ok, "logistics": logistics_ok, "ads": ads_ok},
                "fully_covered": purchase_ok and finance_ok and logistics_ok and ads_ok,
            })
        items.sort(key=lambda item: (item["order_date"] or "", item["posting_number"] or ""), reverse=True)
        return {"items": items[: max(1, min(limit, 1000))], "total": len(items), "period": {"date_from": start, "date_to": end}}

    def products(
        self, *, store_id: str = "all", date_from: Optional[str] = None,
        date_to: Optional[str] = None, query: str = "", limit: int = 200,
    ) -> dict[str, Any]:
        order_items = self.orders(store_id=store_id, date_from=date_from, date_to=date_to, query=query, limit=5000)["items"]
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in order_items:
            key = (str(item["store_id"]), str(item["offer_id"] or item["sku"]))
            target = grouped.setdefault(key, {
                "store_id": item["store_id"], "sku": item["sku"], "offer_id": item["offer_id"],
                "product_name": item["product_name"], "image_url": item["image_url"],
                "sales_cny": Decimal("0"), "profit_cny": Decimal("0"), "order_lines": 0,
                "missing_purchase_lines": 0,
            })
            target["sales_cny"] += decimal_value(item["buyer_paid_cny"])
            target["profit_cny"] += decimal_value(item["profit_cny"])
            target["order_lines"] += 1
            if not item["coverage"]["purchase"]:
                target["missing_purchase_lines"] += 1
        items = []
        for target in grouped.values():
            sales = target.pop("sales_cny")
            profit = target.pop("profit_cny")
            items.append({**target, "sales_cny": money(sales), "profit_cny": money(profit), "profit_margin": ratio(profit, sales)})
        items.sort(key=lambda item: decimal_value(item["sales_cny"]), reverse=True)
        return {"items": items[: max(1, min(limit, 1000))], "total": len(items)}

    def reconciliation(self, *, store_id: str = "all", limit: int = 200) -> dict[str, Any]:
        self.initialize()
        with self.connect(readonly=True) as conn:
            params: tuple[Any, ...] = () if store_id in {"", "all"} else (store_id,)
            store_filter = "" if not params else " AND store_id=?"
            rows = conn.execute(
                "SELECT id,file_type,file_name,source_row_number,occurred_at,posting_number,order_number,sku,offer_id,"
                "amount_cny,reason,resolution_status,store_id FROM import_unmatched_rows "
                "WHERE resolution_status='open'" + store_filter + " ORDER BY occurred_at DESC LIMIT ?",
                (*params, max(1, min(limit, 1000))),
            ).fetchall()
            counts = conn.execute(
                "SELECT file_type,COUNT(*) count FROM import_unmatched_rows WHERE resolution_status='open'"
                + store_filter + " GROUP BY file_type", params,
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "counts": {str(row["file_type"]): int(row["count"]) for row in counts},
            "notice": "未匹配金额仅进入待核对清单，不自动计入成本或收入。",
        }

    @staticmethod
    def _decode_import_file(file_name: str, content_base64: str) -> tuple[list[str], list[dict[str, Any]]]:
        try:
            raw = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise ValueError("文件内容不是有效的 Base64") from exc
        if len(raw) > 15 * 1024 * 1024:
            raise ValueError("单个导入文件不能超过 15MB")
        suffix = Path(file_name).suffix.lower()
        if suffix in {".csv", ".txt", ".tsv"}:
            text = None
            for encoding in ("utf-8-sig", "gb18030", "utf-16"):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                raise ValueError("无法识别 CSV 文件编码")
            dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
            sample = text[:4096]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                pass
            reader = csv.reader(io.StringIO(text), dialect=dialect)
            matrix = [[str(cell).strip() for cell in row] for row in reader]
        elif suffix == ".xlsx":
            matrix = FinanceCenter._read_xlsx(raw)
        else:
            raise ValueError("当前支持 .xlsx、.csv、.tsv 文件")
        matrix = [row for row in matrix if any(str(cell).strip() for cell in row)]
        if not matrix:
            raise ValueError("文件没有可读取的数据")
        headers = [str(value).strip() or f"未命名列{index + 1}" for index, value in enumerate(matrix[0])]
        rows = []
        for values in matrix[1:5001]:
            padded = values + [""] * (len(headers) - len(values))
            rows.append({headers[index]: padded[index] for index in range(len(headers))})
        return headers, rows

    @staticmethod
    def _read_xlsx(raw: bytes) -> list[list[str]]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise ValueError("Excel 文件损坏或不是标准 .xlsx") from exc
        with archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in root.iter():
                    if item.tag.endswith("}si"):
                        shared.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
            sheet_names = sorted(
                name for name in archive.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            if not sheet_names:
                raise ValueError("Excel 文件中没有工作表")
            candidates: list[tuple[int, list[list[str]]]] = []
            alias_keys = {
                normalized_key(alias)
                for fields in IMPORT_FIELDS.values()
                for aliases in fields.values()
                for alias in aliases
            }
            for sheet_name in sheet_names:
                root = ElementTree.fromstring(archive.read(sheet_name))
                matrix: list[list[str]] = []
                for row_node in (node for node in root.iter() if node.tag.endswith("}row")):
                    cells: dict[int, str] = {}
                    for cell in (node for node in row_node if node.tag.endswith("}c")):
                        reference = cell.attrib.get("r", "A1")
                        letters = re.match(r"[A-Z]+", reference)
                        column = 0
                        for letter in (letters.group(0) if letters else "A"):
                            column = column * 26 + ord(letter) - 64
                        column -= 1
                        kind = cell.attrib.get("t", "")
                        value_node = next((node for node in cell if node.tag.endswith("}v")), None)
                        inline = next((node for node in cell if node.tag.endswith("}is")), None)
                        if inline is not None:
                            value = "".join(node.text or "" for node in inline.iter() if node.tag.endswith("}t"))
                        else:
                            value = value_node.text if value_node is not None and value_node.text is not None else ""
                            if kind == "s" and value.isdigit() and int(value) < len(shared):
                                value = shared[int(value)]
                            elif kind == "b":
                                value = "是" if value == "1" else "否"
                        cells[column] = str(value).strip()
                    width = max(cells, default=-1) + 1
                    matrix.append([cells.get(index, "") for index in range(width)])
                best_score = -1
                best_header = 0
                for row_index, row in enumerate(matrix[:25]):
                    keys = {normalized_key(cell) for cell in row if str(cell).strip()}
                    score = len(keys.intersection(alias_keys))
                    if normalized_key("订单号") in keys:
                        score += 5
                    if normalized_key("采购成本") in keys:
                        score += 5
                    if normalized_key("店铺") in keys:
                        score += 2
                    if score > best_score:
                        best_score = score
                        best_header = row_index
                candidates.append((best_score, matrix[best_header:]))
            return max(candidates, key=lambda item: item[0])[1]

    @staticmethod
    def _field_score(source_header: str, target_field: str, aliases: Iterable[str]) -> float:
        source = normalized_key(source_header)
        candidates = [normalized_key(target_field), *(normalized_key(alias) for alias in aliases)]
        if source in candidates:
            return 1.0
        if not source:
            return 0.0
        scores = []
        for candidate in candidates:
            if not candidate:
                continue
            if source in candidate or candidate in source:
                scores.append(0.88)
            scores.append(SequenceMatcher(None, source, candidate).ratio() * 0.82)
        return max(scores, default=0.0)

    def _mapping_candidates(self, headers: list[str], file_kind: str) -> list[dict[str, Any]]:
        fields = IMPORT_FIELDS[file_kind]
        results = []
        for header in headers:
            scored = sorted(
                ((self._field_score(header, field, aliases), field) for field, aliases in fields.items()),
                reverse=True,
            )
            confidence, target = scored[0]
            results.append({
                "source_header": header,
                "target_field": target if confidence >= 0.55 else None,
                "confidence": round(confidence, 4),
                "auto_selected": confidence >= 0.9 and target not in MONEY_FIELDS,
                "requires_manual_confirmation": target in MONEY_FIELDS or confidence < 0.9,
                "alternatives": [field for score, field in scored[1:4] if score >= 0.45],
            })
        return results

    def _infer_file_kind(self, headers: list[str]) -> str:
        scores: list[tuple[float, str]] = []
        for file_kind in IMPORT_FIELDS:
            candidates = self._mapping_candidates(headers, file_kind)
            score = sum(item["confidence"] for item in candidates if item["confidence"] >= 0.7)
            scores.append((score, file_kind))
        return max(scores)[1]

    @staticmethod
    def _validate_mapping(file_kind: str, mapping: Mapping[str, str]) -> None:
        targets = {str(value) for value in mapping.values() if value}
        groups = IMPORT_REQUIRED[file_kind]
        for group in groups:
            if not targets.intersection(group):
                labels = " / ".join(sorted(group))
                raise ValueError(f"{file_kind} 导入缺少必需字段：{labels}")
        if len(targets) != len([value for value in mapping.values() if value]):
            raise ValueError("同一个目标字段不能映射多次")

    def preview_import(self, *, file_name: str, content_base64: str, file_kind: Optional[str] = None) -> dict[str, Any]:
        headers, rows = self._decode_import_file(file_name, content_base64)
        kind = file_kind if file_kind in IMPORT_FIELDS else self._infer_file_kind(headers)
        candidates = self._mapping_candidates(headers, kind)
        detected_mapping = {
            item["source_header"]: item["target_field"]
            for item in candidates if item["auto_selected"] and item["target_field"]
        }
        return {
            "file_name": Path(file_name).name, "file_kind": kind,
            "headers": headers, "row_count": len(rows), "mapping_candidates": candidates,
            "detected_mapping": detected_mapping, "sample_rows": rows[:8],
            "requires_confirmation": True,
            "notice": "金额列和低置信度列必须由负责人确认后才会写入。",
        }

    @staticmethod
    def _excel_date(value: Any) -> str:
        text = str(value or "").strip()
        if re.fullmatch(r"\d+(?:\.0+)?", text):
            serial = int(Decimal(text))
            if 1 <= serial <= 80000:
                return (date(1899, 12, 30) + timedelta(days=serial)).isoformat()
        return text[:10] if len(text) >= 10 else text

    @staticmethod
    def _mapped_row(row: Mapping[str, Any], mapping: Mapping[str, str]) -> dict[str, Any]:
        result = {target: row.get(source, "") for source, target in mapping.items() if target}
        for field in ("effective_date", "order_date", "occurred_at"):
            if field in result:
                result[field] = FinanceCenter._excel_date(result[field])
        return result

    @staticmethod
    def _store_value(value: Any) -> str:
        return str(value or "default_store").strip() or "default_store"

    @staticmethod
    def _table_row(conn: sqlite3.Connection, table: str, row_id: str) -> Optional[dict[str, Any]]:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _record_change(
        conn: sqlite3.Connection, batch_id: str, table: str, row_id: str,
        action: str, before: Optional[dict[str, Any]], after: dict[str, Any],
    ) -> None:
        conn.execute(
            "INSERT INTO finance_import_changes(batch_id,table_name,row_id,action,before_json,after_json) VALUES(?,?,?,?,?,?)",
            (batch_id, table, row_id, action, safe_json(before) if before else None, safe_json(after)),
        )

    def commit_import(
        self, *, file_name: str, content_base64: str, file_kind: str,
        mapping: Mapping[str, str], created_by: str,
    ) -> dict[str, Any]:
        if file_kind not in IMPORT_FIELDS:
            raise ValueError("未知导入类型")
        self._validate_mapping(file_kind, mapping)
        headers, rows = self._decode_import_file(file_name, content_base64)
        if not rows:
            raise ValueError("没有可导入的数据行")
        if any(source not in headers for source in mapping):
            raise ValueError("字段映射与当前文件不一致")
        self.initialize()
        batch_id = f"imp-{uuid.uuid4().hex[:16]}"
        backup = self.backup(f"before-{batch_id}")
        inserted = 0
        updated = 0
        unmatched = 0
        processed_rows = len(rows)
        matched_source_rows = 0
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO finance_import_batches(id,file_kind,file_name,status,created_by,created_at,backup_path,row_count,mapping_json) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (batch_id, file_kind, Path(file_name).name, "applying", created_by, now_iso(), str(backup), len(rows), safe_json(mapping)),
            )
            if file_kind == "purchase_cost" and "order_number" in mapping.values():
                purchase_result = self._import_purchase_order_rows(
                    conn, batch_id, Path(file_name).name, rows, mapping,
                )
                inserted = purchase_result["inserted_count"]
                updated = purchase_result["updated_count"]
                unmatched = purchase_result["unmatched_count"]
                processed_rows = purchase_result["source_row_count"]
                matched_source_rows = purchase_result["matched_source_row_count"]
            else:
                for index, source_row in enumerate(rows, start=2):
                    value = self._mapped_row(source_row, mapping)
                    if not any(str(item).strip() for item in value.values()):
                        continue
                    if file_kind == "purchase_cost":
                        result = self._import_purchase_cost(conn, batch_id, index, value, source_row)
                    elif file_kind == "orders":
                        result = self._import_order(conn, batch_id, index, value, source_row)
                    elif file_kind == "finance":
                        result = self._import_finance(conn, batch_id, index, value, source_row)
                    else:
                        result = self._import_ads(conn, batch_id, index, value, source_row)
                    inserted += result == "inserted"
                    updated += result == "updated"
            conn.execute(
                "UPDATE finance_import_batches SET status='applied',applied_at=?,inserted_count=?,updated_count=? WHERE id=?",
                (now_iso(), inserted, updated, batch_id),
            )
            conn.execute("UPDATE finance_import_batches SET row_count=? WHERE id=?", (processed_rows, batch_id))
            for source, target in mapping.items():
                mapping_id = stable_id(file_kind, normalized_key(source))
                conn.execute(
                    "INSERT INTO finance_import_mappings(id,file_kind,source_header,target_field,confidence,confirmed,updated_at) "
                    "VALUES(?,?,?,?,1,1,?) ON CONFLICT(file_kind,source_header) DO UPDATE SET "
                    "target_field=excluded.target_field,confidence=1,confirmed=1,updated_at=excluded.updated_at",
                    (mapping_id, file_kind, source, target, now_iso()),
                )
        return {
            "batch_id": batch_id, "status": "applied", "row_count": processed_rows,
            "inserted_count": inserted, "updated_count": updated,
            "matched_count": matched_source_rows or inserted + updated, "unmatched_count": unmatched,
            "backup_path": str(backup), "rollback_available": True,
        }

    @staticmethod
    def _purchase_candidate_key(order: Mapping[str, Any]) -> str:
        return str(order.get("offer_id") or order.get("sku") or "").strip()

    @staticmethod
    def _purchase_candidate_aliases(orders: Iterable[Mapping[str, Any]]) -> set[str]:
        aliases: set[str] = set()
        for order in orders:
            aliases.update(str(order.get(field) or "").strip() for field in ("sku", "offer_id"))
        return {value for value in aliases if value}

    def _purchase_store_ids(self, conn: sqlite3.Connection, value: Any) -> list[str]:
        raw = self._store_value(value)
        key = normalized_key(raw)
        rows = conn.execute("SELECT id,store_name,store_alias FROM stores").fetchall()
        matched = [
            str(row["id"]) for row in rows
            if key in {
                normalized_key(row["id"]), normalized_key(row["store_name"]),
                normalized_key(row["store_alias"]),
            }
        ]
        return list(dict.fromkeys(matched or [raw]))

    def _purchase_order_groups(
        self, conn: sqlite3.Connection, store_ids: list[str], order_number: str,
    ) -> dict[str, list[dict[str, Any]]]:
        placeholders = ",".join("?" for _ in store_ids)
        rows = conn.execute(
            f"SELECT * FROM orders WHERE store_id IN ({placeholders}) "
            "AND (trim(posting_number)=? OR trim(COALESCE(order_number,''))=?)",
            (*store_ids, order_number, order_number),
        ).fetchall()
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            key = self._purchase_candidate_key(item)
            if key:
                groups.setdefault(key, []).append(item)
        return groups

    def _import_purchase_order_rows(
        self, conn: sqlite3.Connection, batch_id: str, file_name: str,
        rows: list[dict[str, Any]], mapping: Mapping[str, str],
    ) -> dict[str, int]:
        entries: list[dict[str, Any]] = []
        evidence: dict[tuple[str, str], dict[str, int]] = {}
        for row_number, raw in enumerate(rows, start=2):
            value = self._mapped_row(raw, mapping)
            if not any(str(item).strip() for item in value.values()):
                continue
            order_number = str(value.get("order_number") or "").strip()
            source_name = str(value.get("product_name") or value.get("sku") or value.get("offer_id") or "").strip()
            store_token = self._store_value(value.get("store_id"))
            cost_text = str(value.get("purchase_cost_cny") if value.get("purchase_cost_cny") is not None else "").strip()
            if not order_number and not source_name:
                continue
            if not cost_text:
                continue
            entry = {
                "row_number": row_number, "raw": raw, "value": value,
                "order_number": order_number, "source_name": source_name,
                "store_token": store_token, "cost_text": cost_text,
                "store_ids": self._purchase_store_ids(conn, store_token),
                "groups": {}, "resolved_key": None,
            }
            if order_number:
                entry["groups"] = self._purchase_order_groups(conn, entry["store_ids"], order_number)
            groups = entry["groups"]
            if len(groups) == 1:
                entry["resolved_key"] = next(iter(groups))
                evidence_key = (normalized_key(store_token), normalized_key(source_name))
                scores = evidence.setdefault(evidence_key, {})
                scores[entry["resolved_key"]] = scores.get(entry["resolved_key"], 0) + 1
            entries.append(entry)

        for entry in entries:
            groups = entry["groups"]
            if entry["resolved_key"] or len(groups) < 2:
                continue
            wanted = {
                str(entry["value"].get(field) or "").strip()
                for field in ("sku", "offer_id")
                if str(entry["value"].get(field) or "").strip()
            }
            evidence_key = (normalized_key(entry["store_token"]), normalized_key(entry["source_name"]))
            known = evidence.get(evidence_key, {})
            scored: list[tuple[int, str]] = []
            for key, candidates in groups.items():
                aliases = self._purchase_candidate_aliases(candidates) | {key}
                score = sum(known.get(alias, 0) for alias in aliases)
                if wanted.intersection(aliases):
                    score += 1000
                if score:
                    scored.append((score, key))
            scored.sort(reverse=True)
            if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                entry["resolved_key"] = scored[0][1]

        # A multi-product order often has one purchase row per SKU. Resolve the
        # final row from the only candidate not already assigned in that order.
        changed = True
        while changed:
            changed = False
            order_entries: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for entry in entries:
                if entry["groups"]:
                    order_entries.setdefault(
                        (normalized_key(entry["store_token"]), entry["order_number"]), []
                    ).append(entry)
            for related in order_entries.values():
                assigned = {entry["resolved_key"] for entry in related if entry["resolved_key"]}
                for entry in related:
                    if entry["resolved_key"] or len(entry["groups"]) < 2:
                        continue
                    remaining = set(entry["groups"]).difference(assigned)
                    if len(remaining) == 1:
                        entry["resolved_key"] = remaining.pop()
                        assigned.add(entry["resolved_key"])
                        changed = True

        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        unmatched = 0
        for entry in entries:
            if not entry["order_number"] or not entry["resolved_key"]:
                reason = "订单尚未从 Ozon 读取" if not entry["groups"] else "同一订单包含多个 SKU，无法唯一确认采购价归属"
                self._unmatched_import(
                    conn, batch_id, "purchase_cost", entry["row_number"], entry["value"], entry["raw"], reason,
                )
                unmatched += 1
                continue
            group_key = (normalized_key(entry["store_token"]), entry["order_number"], entry["resolved_key"])
            group = grouped.setdefault(group_key, {
                "entries": [], "cost": Decimal("0"), "orders": entry["groups"][entry["resolved_key"]],
                "store_token": entry["store_token"], "order_number": entry["order_number"],
                "sku": entry["resolved_key"], "source_name": entry["source_name"],
            })
            group["entries"].append(entry)
            group["cost"] += decimal_value(entry["cost_text"])

        inserted = updated = 0
        affected_orders: set[str] = set()
        for group in sorted(grouped.values(), key=lambda item: max(entry["row_number"] for entry in item["entries"])):
            source_row = min(entry["row_number"] for entry in group["entries"])
            total_cost = group["cost"]
            quantities = [self._order_quantity(order) for order in group["orders"]]
            quantity = max((value for value in quantities if value > 0), default=Decimal("1"))
            unit_cost = total_cost / quantity
            order_stores = sorted({str(order["store_id"]) for order in group["orders"]})
            for order in group["orders"]:
                store_id = str(order["store_id"])
                order_id = str(order["id"])
                match_id = stable_id("purchase-order-import", order_id, group["sku"], batch_id)
                payload = {
                    "id": match_id, "store_id": store_id, "order_id": order_id,
                    "posting_number": group["order_number"], "sku": group["sku"],
                    "purchase_cost_cny": money(total_cost), "weight_g": None,
                    "source_file": file_name, "source_row": source_row,
                    "matched_at": now_iso(), "created_at": now_iso(),
                }
                conn.execute(
                    f"INSERT INTO purchase_order_match({','.join(payload)}) VALUES({','.join('?' for _ in payload)})",
                    tuple(payload.values()),
                )
                self._record_change(conn, batch_id, "purchase_order_match", match_id, "insert", None, payload)
                inserted += 1

            product_cost_id = stable_id("purchase-order-cost", batch_id, group["order_number"], group["sku"])
            cost_payload = {
                "id": product_cost_id, "row_hash": stable_id(group["order_number"], group["sku"], money(total_cost)),
                "file_hash": stable_id(batch_id), "sku": group["sku"], "offer_id": group["sku"],
                "product_name": group["source_name"], "purchase_cost_cny": money(unit_cost),
                "effective_date": None, "raw_payload": safe_json([entry["raw"] for entry in group["entries"]]),
                "created_at": now_iso(), "amount_original": money(unit_cost), "currency_original": "CNY",
                "currency_source": "confirmed_import", "source": "finance_center_order_import",
                "updated_at": now_iso(), "batch_id": batch_id,
                "note": f"订单 {group['order_number']}，合计 {money(total_cost)} 元",
                "store_id": order_stores[0],
            }
            conn.execute(
                f"INSERT INTO product_costs({','.join(cost_payload)}) VALUES({','.join('?' for _ in cost_payload)})",
                tuple(cost_payload.values()),
            )
            self._record_change(conn, batch_id, "product_costs", product_cost_id, "insert", None, cost_payload)

            product_keys: set[tuple[str, str, str]] = set()
            for order in group["orders"]:
                product_keys.add((str(order["store_id"]), str(order["sku"]), str(order.get("offer_id") or "")))
                affected_orders.add(str(order["id"]))
            for store_id, sku, offer_id in product_keys:
                master = conn.execute(
                    "SELECT * FROM product_master WHERE store_id=? AND (sku=? OR (?!='' AND offer_id=?)) "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (store_id, sku, offer_id, offer_id),
                ).fetchone()
                if not master:
                    continue
                before = dict(master)
                conn.execute(
                    "UPDATE product_master SET unit_purchase_cost_cny=?,purchase_cost_source='purchase_table',updated_at=? WHERE id=?",
                    (money(unit_cost), now_iso(), master["id"]),
                )
                after = self._table_row(conn, "product_master", str(master["id"])) or {}
                self._record_change(conn, batch_id, "product_master", str(master["id"]), "update", before, after)
                updated += 1

        for order_id in affected_orders:
            before_snapshot_row = conn.execute(
                "SELECT * FROM profit_snapshots WHERE order_id=? ORDER BY created_at DESC LIMIT 1",
                (order_id,),
            ).fetchone()
            before_snapshot = dict(before_snapshot_row) if before_snapshot_row else None
            self._recompute_order(conn, order_id)
            conn.execute(
                "UPDATE profit_snapshots SET purchase_cost_update_batch_id=?,recompute_reason='purchase_order_import' WHERE order_id=?",
                (batch_id, order_id),
            )
            after_snapshot_row = conn.execute(
                "SELECT * FROM profit_snapshots WHERE order_id=? ORDER BY created_at DESC LIMIT 1",
                (order_id,),
            ).fetchone()
            if after_snapshot_row:
                after_snapshot = dict(after_snapshot_row)
                self._record_change(
                    conn, batch_id, "profit_snapshots", str(after_snapshot["id"]),
                    "update" if before_snapshot else "insert", before_snapshot, after_snapshot,
                )
        return {
            "inserted_count": inserted,
            "updated_count": updated,
            "matched_source_row_count": sum(len(group["entries"]) for group in grouped.values()),
            "unmatched_count": unmatched,
            "source_row_count": len(entries),
        }

    def _import_purchase_cost(
        self, conn: sqlite3.Connection, batch_id: str, row_number: int,
        value: dict[str, Any], raw: Mapping[str, Any],
    ) -> str:
        store_id = self._store_value(value.get("store_id"))
        sku = str(value.get("sku") or value.get("offer_id") or "").strip()
        offer_id = str(value.get("offer_id") or "").strip()
        if not sku:
            raise ValueError(f"第 {row_number} 行缺少 SKU 或商家编码")
        cost = decimal_value(value.get("purchase_cost_cny"))
        if cost < 0:
            raise ValueError(f"第 {row_number} 行采购价不能小于 0")
        row_id = stable_id("purchase", batch_id, row_number, store_id, sku, offer_id)
        payload = {
            "id": row_id, "row_hash": stable_id(raw), "file_hash": stable_id(batch_id),
            "sku": sku, "offer_id": offer_id, "product_name": str(value.get("product_name") or ""),
            "purchase_cost_cny": money(cost), "effective_date": value.get("effective_date") or None,
            "raw_payload": safe_json(raw), "created_at": now_iso(), "amount_original": money(cost),
            "currency_original": str(value.get("currency_original") or "CNY").upper(),
            "currency_source": "confirmed_import", "source": "finance_center_import",
            "updated_at": now_iso(), "batch_id": batch_id, "note": str(value.get("note") or ""),
            "store_id": store_id,
        }
        columns = ",".join(payload)
        conn.execute(f"INSERT INTO product_costs({columns}) VALUES({','.join('?' for _ in payload)})", tuple(payload.values()))
        self._record_change(conn, batch_id, "product_costs", row_id, "insert", None, payload)
        master = conn.execute(
            "SELECT * FROM product_master WHERE store_id=? AND (sku=? OR (?!='' AND offer_id=?)) ORDER BY updated_at DESC LIMIT 1",
            (store_id, sku, offer_id, offer_id),
        ).fetchone()
        if master:
            before = dict(master)
            conn.execute(
                "UPDATE product_master SET unit_purchase_cost_cny=?,purchase_cost_source='finance_center_import',"
                "image_url=COALESCE(NULLIF(?,''),image_url),updated_at=? WHERE id=?",
                (money(cost), str(value.get("image_url") or ""), now_iso(), master["id"]),
            )
            after = self._table_row(conn, "product_master", master["id"])
            self._record_change(conn, batch_id, "product_master", master["id"], "update", before, after or {})
            self._recompute_product_orders(conn, store_id, sku, offer_id)
            return "updated"
        master_id = stable_id("product", store_id, sku)
        master_payload = {
            "id": master_id, "store_id": store_id, "sku": sku, "offer_id": offer_id,
            "product_id": None, "product_name": str(value.get("product_name") or ""),
            "image_url": str(value.get("image_url") or "") or None,
            "unit_purchase_cost_cny": money(cost), "estimated_weight_g": None,
            "weight_source": "missing", "dimensions": None, "volume_weight": None,
            "purchase_cost_source": "finance_center_import", "created_at": now_iso(), "updated_at": now_iso(),
        }
        conn.execute(
            f"INSERT INTO product_master({','.join(master_payload)}) VALUES({','.join('?' for _ in master_payload)})",
            tuple(master_payload.values()),
        )
        self._record_change(conn, batch_id, "product_master", master_id, "insert", None, master_payload)
        self._recompute_product_orders(conn, store_id, sku, offer_id)
        return "inserted"

    def _import_order(
        self, conn: sqlite3.Connection, batch_id: str, row_number: int,
        value: dict[str, Any], raw: Mapping[str, Any],
    ) -> str:
        store_id = self._store_value(value.get("store_id"))
        posting = str(value.get("posting_number") or value.get("order_number") or "").strip()
        order_number = str(value.get("order_number") or posting).strip()
        sku = str(value.get("sku") or value.get("offer_id") or "").strip()
        if not posting or not sku:
            raise ValueError(f"第 {row_number} 行缺少订单/Posting 编号或 SKU")
        matched_existing = self._exact_order_match(conn, {**value, "store_id": store_id, "posting_number": posting, "sku": sku})
        row_id = matched_existing or stable_id("order-import", store_id, posting, sku)
        existing = self._table_row(conn, "orders", row_id)
        rate = self._rate_for_date(conn, value.get("order_date") or date.today().isoformat())
        rub = decimal_value(value.get("buyer_paid_rub"))
        cny = decimal_value(value.get("buyer_paid_cny"))
        if cny == 0 and rub != 0:
            cny = rub / rate
        if rub == 0 and cny != 0:
            rub = cny * rate
        payload = {
            "id": row_id, "row_hash": stable_id(raw), "file_hash": stable_id(batch_id),
            "posting_number": posting, "order_number": order_number, "sku": sku,
            "offer_id": str(value.get("offer_id") or ""), "product_name": str(value.get("product_name") or ""),
            "order_date": value.get("order_date") or None, "buyer_paid_rub": money(rub),
            "buyer_paid_cny": money(cny), "status": str(value.get("status") or ""),
            "raw_payload": safe_json(raw), "created_at": now_iso(), "store_id": store_id,
        }
        conn.execute(
            f"INSERT INTO orders({','.join(payload)}) VALUES({','.join('?' for _ in payload)}) "
            "ON CONFLICT(id) DO UPDATE SET row_hash=excluded.row_hash,file_hash=excluded.file_hash,"
            "order_number=excluded.order_number,offer_id=excluded.offer_id,product_name=excluded.product_name,"
            "order_date=excluded.order_date,buyer_paid_rub=excluded.buyer_paid_rub,buyer_paid_cny=excluded.buyer_paid_cny,"
            "status=excluded.status,raw_payload=excluded.raw_payload,store_id=excluded.store_id",
            tuple(payload.values()),
        )
        after = self._table_row(conn, "orders", row_id) or payload
        self._record_change(conn, batch_id, "orders", row_id, "update" if existing else "insert", existing, after)
        image = str(value.get("image_url") or "")
        if image:
            conn.execute(
                "INSERT INTO product_master(id,store_id,sku,offer_id,product_name,image_url,purchase_cost_source,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'missing',?,?) ON CONFLICT(store_id,sku) DO UPDATE SET "
                "image_url=COALESCE(NULLIF(excluded.image_url,''),product_master.image_url),updated_at=excluded.updated_at",
                (stable_id("product", store_id, sku), store_id, sku, payload["offer_id"], payload["product_name"], image, now_iso(), now_iso()),
            )
        self._recompute_order(conn, row_id)
        return "updated" if existing else "inserted"

    def _exact_order_match(self, conn: sqlite3.Connection, value: Mapping[str, Any]) -> Optional[str]:
        store_id = self._store_value(value.get("store_id"))
        posting = str(value.get("posting_number") or "").strip()
        order_number = str(value.get("order_number") or "").strip()
        sku = str(value.get("sku") or "").strip()
        clauses = ["store_id=?"]
        params: list[Any] = [store_id]
        if posting:
            clauses.append("posting_number=?")
            params.append(posting)
        elif order_number:
            clauses.append("order_number=?")
            params.append(order_number)
        else:
            return None
        if sku:
            clauses.append("sku=?")
            params.append(sku)
        rows = conn.execute("SELECT id FROM orders WHERE " + " AND ".join(clauses), params).fetchall()
        return str(rows[0]["id"]) if len(rows) == 1 else None

    def _unmatched_import(
        self, conn: sqlite3.Connection, batch_id: str, file_type: str, row_number: int,
        value: Mapping[str, Any], raw: Mapping[str, Any], reason: str,
    ) -> None:
        row_id = stable_id("unmatched", batch_id, file_type, row_number)
        amount_rub = value.get("amount_rub") or value.get("spend_rub") or "0"
        amount_cny = value.get("amount_cny") or value.get("spend_cny") or value.get("purchase_cost_cny") or "0"
        payload = {
            "id": row_id, "store_id": self._store_value(value.get("store_id")), "file_type": file_type,
            "file_name": batch_id, "file_path": "", "source_row_number": row_number,
            "occurred_at": value.get("occurred_at") or None, "posting_number": value.get("posting_number") or None,
            "order_number": value.get("order_number") or None, "sku": value.get("sku") or None,
            "offer_id": value.get("offer_id") or None, "amount_rub": money(amount_rub),
            "amount_cny": money(amount_cny), "reason": reason, "resolution_status": "open",
            "raw_payload": safe_json(raw), "created_at": now_iso(),
        }
        conn.execute(
            f"INSERT OR IGNORE INTO import_unmatched_rows({','.join(payload)}) VALUES({','.join('?' for _ in payload)})",
            tuple(payload.values()),
        )
        self._record_change(conn, batch_id, "import_unmatched_rows", row_id, "insert", None, payload)

    def _import_finance(
        self, conn: sqlite3.Connection, batch_id: str, row_number: int,
        value: dict[str, Any], raw: Mapping[str, Any],
    ) -> str:
        store_id = self._store_value(value.get("store_id"))
        rate = self._rate_for_date(conn, value.get("occurred_at") or date.today().isoformat())
        rub = decimal_value(value.get("amount_rub"))
        cny = decimal_value(value.get("amount_cny"))
        if cny == 0 and rub != 0:
            cny = rub / rate
        if rub == 0 and cny != 0:
            rub = cny * rate
        matched = self._exact_order_match(conn, value)
        row_id = stable_id("finance-import", batch_id, row_number, store_id)
        payload = {
            "id": row_id, "row_hash": stable_id(raw), "file_hash": stable_id(batch_id),
            "matched_order_id": matched, "posting_number": str(value.get("posting_number") or ""),
            "order_number": str(value.get("order_number") or ""), "sku": str(value.get("sku") or ""),
            "occurred_at": value.get("occurred_at") or None, "operation_type": str(value.get("operation_type") or ""),
            "service_name": str(value.get("service_name") or ""), "amount_rub": money(rub), "amount_cny": money(cny),
            "platform_commission_cny": money(value.get("platform_commission_cny")),
            "logistics_fee_cny": money(value.get("logistics_fee_cny")), "refund_cny": money(value.get("refund_cny")),
            "compensation_cny": money(value.get("compensation_cny")), "acquiring_cny": money(value.get("acquiring_cny")),
            "other_fee_cny": money(value.get("other_fee_cny")), "raw_payload": safe_json(raw),
            "created_at": now_iso(), "store_id": store_id,
        }
        conn.execute(
            f"INSERT INTO finance_transactions({','.join(payload)}) VALUES({','.join('?' for _ in payload)})",
            tuple(payload.values()),
        )
        self._record_change(conn, batch_id, "finance_transactions", row_id, "insert", None, payload)
        if matched:
            self._recompute_order(conn, matched)
        else:
            self._unmatched_import(conn, batch_id, "finance", row_number, value, raw, "没有唯一精确匹配的订单")
        return "inserted"

    def _import_ads(
        self, conn: sqlite3.Connection, batch_id: str, row_number: int,
        value: dict[str, Any], raw: Mapping[str, Any],
    ) -> str:
        store_id = self._store_value(value.get("store_id"))
        rate = self._rate_for_date(conn, value.get("occurred_at") or date.today().isoformat())
        rub = decimal_value(value.get("spend_rub"))
        cny = decimal_value(value.get("spend_cny"))
        if cny == 0 and rub != 0:
            cny = rub / rate
        if rub == 0 and cny != 0:
            rub = cny * rate
        matched = self._exact_order_match(conn, value)
        row_id = stable_id("ads-import", batch_id, row_number, store_id)
        payload = {
            "id": row_id, "row_hash": stable_id(raw), "file_hash": stable_id(batch_id),
            "matched_order_id": matched, "occurred_at": value.get("occurred_at") or None,
            "campaign_id": str(value.get("campaign_id") or ""), "campaign_name": str(value.get("campaign_name") or ""),
            "posting_number": str(value.get("posting_number") or ""),
            "order_number": str(value.get("order_number") or ""), "sku": str(value.get("sku") or ""),
            "offer_id": str(value.get("offer_id") or ""), "product_id": str(value.get("product_id") or ""),
            "spend_rub": money(rub), "spend_cny": money(cny), "views": int(decimal_value(value.get("views"))),
            "clicks": int(decimal_value(value.get("clicks"))), "orders": int(decimal_value(value.get("orders"))),
            "revenue_rub": money(value.get("revenue_rub")), "revenue_cny": money(value.get("revenue_cny")),
            "raw_payload": safe_json(raw), "created_at": now_iso(), "store_id": store_id,
        }
        conn.execute(
            f"INSERT INTO ad_spend_transactions({','.join(payload)}) VALUES({','.join('?' for _ in payload)})",
            tuple(payload.values()),
        )
        self._record_change(conn, batch_id, "ad_spend_transactions", row_id, "insert", None, payload)
        if matched:
            self._recompute_order(conn, matched)
        else:
            self._unmatched_import(conn, batch_id, "ads", row_number, value, raw, "没有唯一精确匹配的订单")
        return "inserted"

    def rollback_import(self, batch_id: str, *, rolled_back_by: str) -> dict[str, Any]:
        self.initialize()
        backup = self.backup(f"before-rollback-{batch_id}")
        with self.connect() as conn:
            batch = conn.execute("SELECT * FROM finance_import_batches WHERE id=?", (batch_id,)).fetchone()
            if not batch:
                raise KeyError(batch_id)
            if batch["status"] != "applied":
                raise ValueError("该导入批次当前不能回滚")
            if str(batch["file_kind"]).startswith("system_"):
                raise ValueError("系统一致性修复请使用该记录对应的数据库恢复点整体回滚")
            changes = conn.execute(
                "SELECT * FROM finance_import_changes WHERE batch_id=? ORDER BY id DESC", (batch_id,),
            ).fetchall()
            recompute_order_ids: set[str] = set()
            recompute_products: set[tuple[str, str, str]] = set()
            for change in changes:
                table = str(change["table_name"])
                if table not in {
                    "orders", "finance_transactions", "ad_spend_transactions", "product_costs",
                    "product_master", "purchase_order_match", "profit_snapshots", "import_unmatched_rows",
                }:
                    raise ValueError("回滚记录包含不允许的表")
                before_payload = json.loads(change["before_json"] or "{}")
                after_payload = json.loads(change["after_json"] or "{}")
                if table == "orders":
                    recompute_order_ids.add(str(change["row_id"]))
                elif table in {"finance_transactions", "ad_spend_transactions"}:
                    matched = before_payload.get("matched_order_id") or after_payload.get("matched_order_id")
                    if matched:
                        recompute_order_ids.add(str(matched))
                elif table == "product_master":
                    value = before_payload or after_payload
                    recompute_products.add((str(value.get("store_id") or "default_store"), str(value.get("sku") or ""), str(value.get("offer_id") or "")))
                elif table == "purchase_order_match":
                    value = before_payload or after_payload
                    if value.get("order_id"):
                        recompute_order_ids.add(str(value["order_id"]))
                    posting = str(value.get("posting_number") or "")
                    store_value = str(value.get("store_id") or "default_store")
                    for row in conn.execute(
                        "SELECT id FROM orders WHERE store_id=? AND posting_number=?",
                        (store_value, posting),
                    ):
                        recompute_order_ids.add(str(row["id"]))
                if change["action"] == "insert":
                    conn.execute(f"DELETE FROM {table} WHERE id=?", (change["row_id"],))
                else:
                    before = before_payload
                    columns = [column for column in before if column != "id"]
                    conn.execute(
                        f"UPDATE {table} SET {','.join(f'{column}=?' for column in columns)} WHERE id=?",
                        (*[before[column] for column in columns], change["row_id"]),
                    )
            conn.execute(
                "UPDATE finance_import_batches SET status='rolled_back',rolled_back_at=? WHERE id=?",
                (now_iso(), batch_id),
            )
            self._set_meta(conn, f"rollback:{batch_id}", safe_json({"by": rolled_back_by, "at": now_iso()}))
            for store_value, sku, offer_id in recompute_products:
                self._recompute_product_orders(conn, store_value, sku, offer_id)
            for order_id in recompute_order_ids:
                self._recompute_order(conn, order_id)
        return {"batch_id": batch_id, "status": "rolled_back", "backup_path": str(backup), "changes_reversed": len(changes)}

    def import_batches(self, limit: int = 100) -> list[dict[str, Any]]:
        self.initialize()
        with self.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT id,file_kind,file_name,status,created_by,created_at,applied_at,rolled_back_at,row_count,"
                "inserted_count,updated_count FROM finance_import_batches ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["rollback_allowed"] = (
                item["status"] == "applied" and not str(item["file_kind"]).startswith("system_")
            )
        return items

    def other_entries(self, *, store_id: str = "all", date_from: Optional[str] = None, date_to: Optional[str] = None) -> list[dict[str, Any]]:
        self.initialize()
        start, end = self._period(date_from, date_to)
        with self.connect(readonly=True) as conn:
            params: list[Any] = [start, end]
            clause = ""
            if store_id not in {"", "all"}:
                clause = " AND (store_id IS NULL OR store_id=?)"
                params.append(store_id)
            rows = conn.execute(
                "SELECT * FROM other_entries WHERE occurred_on BETWEEN ? AND ?" + clause + " ORDER BY occurred_on DESC,created_at DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def save_other_entry(self, payload: Mapping[str, Any], *, created_by: str, entry_id: Optional[str] = None) -> dict[str, Any]:
        self.initialize()
        entry_type = str(payload.get("entry_type") or "")
        if entry_type not in {"income", "expense"}:
            raise ValueError("其他收支类型必须是收入或支出")
        amount = decimal_value(payload.get("amount"))
        if amount <= 0:
            raise ValueError("其他收支金额必须大于 0")
        occurred_on = self._excel_date(payload.get("occurred_on"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", occurred_on):
            raise ValueError("其他收支日期必须使用 YYYY-MM-DD")
        currency = str(payload.get("currency") or "CNY").upper()
        with self.connect() as conn:
            rate = self._rate_for_date(conn, occurred_on)
            amount_cny = amount / rate if currency == "RUB" else amount
            row_id = entry_id or f"other-{uuid.uuid4().hex[:16]}"
            existing = conn.execute("SELECT id,created_at FROM other_entries WHERE id=?", (row_id,)).fetchone()
            conn.execute(
                "INSERT INTO other_entries(id,entry_type,amount_cny,amount_original,currency_original,occurred_on,note,store_id,"
                "created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "entry_type=excluded.entry_type,amount_cny=excluded.amount_cny,amount_original=excluded.amount_original,"
                "currency_original=excluded.currency_original,occurred_on=excluded.occurred_on,note=excluded.note,"
                "store_id=excluded.store_id,updated_at=excluded.updated_at",
                (row_id, entry_type, money(amount_cny), money(amount), currency, occurred_on,
                 str(payload.get("note") or ""), payload.get("store_id") or None, created_by,
                 existing["created_at"] if existing else now_iso(), now_iso()),
            )
            row = conn.execute("SELECT * FROM other_entries WHERE id=?", (row_id,)).fetchone()
        return dict(row)

    def delete_other_entry(self, entry_id: str) -> None:
        self.initialize()
        with self.connect() as conn:
            changed = conn.execute("DELETE FROM other_entries WHERE id=?", (entry_id,)).rowcount
            if not changed:
                raise KeyError(entry_id)

    def _rate_for_date(self, conn: sqlite3.Connection, value: Any) -> Decimal:
        day = self._excel_date(value) or date.today().isoformat()
        row = conn.execute(
            "SELECT rub_per_unit FROM exchange_rates WHERE currency_code='CNY' AND rate_date<=? ORDER BY rate_date DESC LIMIT 1",
            (day,),
        ).fetchone()
        return decimal_value(row["rub_per_unit"]) if row and decimal_value(row["rub_per_unit"]) > 0 else DEFAULT_RUB_PER_CNY

    @staticmethod
    def _order_quantity(order: sqlite3.Row) -> Decimal:
        try:
            payload = json.loads(order["raw_payload"] or "{}")
            product = payload.get("product") if isinstance(payload, dict) else None
            return decimal_value((product or {}).get("quantity")) or Decimal("1")
        except (json.JSONDecodeError, TypeError):
            return Decimal("1")

    def _recompute_order(self, conn: sqlite3.Connection, order_id: str) -> None:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            return
        status = str(order["status"] or "").lower()
        if status in {"cancelled", "canceled", "已取消", "取消", "отменен", "отменён"}:
            conn.execute("DELETE FROM profit_snapshots WHERE order_id=?", (order_id,))
            return
        existing_snapshot = conn.execute(
            "SELECT * FROM profit_snapshots WHERE order_id=? ORDER BY "
            "CASE WHEN lower(COALESCE(purchase_cost_source,'')) NOT IN ('','missing','unknown','legacy','missing_assumed_zero') "
            "THEN 0 ELSE 1 END, ABS(CAST(COALESCE(finance_fee_cny,'0') AS REAL)) DESC, created_at DESC LIMIT 1",
            (order_id,),
        ).fetchone()

        def previous(column: str, default: Any) -> Any:
            if existing_snapshot and column in existing_snapshot.keys() and existing_snapshot[column] is not None:
                return existing_snapshot[column]
            return default
        master = conn.execute(
            "SELECT * FROM product_master WHERE store_id=? AND (sku=? OR (?!='' AND offer_id=?)) ORDER BY updated_at DESC LIMIT 1",
            (order["store_id"], order["sku"], order["offer_id"] or "", order["offer_id"] or ""),
        ).fetchone()
        quantity = self._order_quantity(order)
        purchase_record = conn.execute(
            "SELECT * FROM purchase_order_match WHERE store_id=? AND posting_number=? "
            "AND (order_id=? OR trim(COALESCE(sku,'')) IN (trim(?),trim(COALESCE(?,'')))) "
            "ORDER BY CASE WHEN order_id=? THEN 0 ELSE 1 END,matched_at DESC,created_at DESC LIMIT 1",
            (
                order["store_id"], order["posting_number"], order["id"], order["sku"],
                order["offer_id"] or "", order["id"],
            ),
        ).fetchone()
        if purchase_record:
            purchase_source = "order_purchase_record"
            purchase = decimal_value(purchase_record["purchase_cost_cny"])
            unit_cost = purchase / quantity if quantity > 0 else purchase
        else:
            purchase_source = str(master["purchase_cost_source"] if master else "missing")
            unit_cost = decimal_value(master["unit_purchase_cost_cny"] if master else 0)
            purchase = unit_cost * quantity if purchase_source.lower() not in MISSING_PURCHASE_SOURCES else Decimal("0")
        if (
            purchase_source.lower() in MISSING_PURCHASE_SOURCES and existing_snapshot
            and str(existing_snapshot["purchase_cost_source"] or "").lower() not in MISSING_PURCHASE_SOURCES
        ):
            purchase_source = str(existing_snapshot["purchase_cost_source"])
            purchase = decimal_value(existing_snapshot["purchase_cost_cny"])
            unit_cost = decimal_value(existing_snapshot["unit_purchase_cost_cny"])
            if unit_cost == 0 and quantity > 0:
                unit_cost = purchase / quantity
        finance_row = conn.execute(
            "SELECT COALESCE(SUM(CAST(platform_commission_cny AS REAL)),0) platform,"
            "COALESCE(SUM(CAST(logistics_fee_cny AS REAL)),0) logistics,"
            "COALESCE(SUM(CAST(refund_cny AS REAL)),0) refund,"
            "COALESCE(SUM(CAST(compensation_cny AS REAL)),0) compensation,"
            "COALESCE(SUM(CAST(acquiring_cny AS REAL)),0) acquiring,"
            "COALESCE(SUM(CAST(other_fee_cny AS REAL)),0) other FROM finance_transactions WHERE matched_order_id=?",
            (order_id,),
        ).fetchone()
        finance = {key: decimal_value(finance_row[key]) for key in finance_row.keys()}
        if not any(value != 0 for value in finance.values()) and existing_snapshot:
            existing_logistics = decimal_value(existing_snapshot["logistics_cost_cny"])
            if existing_logistics == 0:
                existing_logistics = decimal_value(existing_snapshot["logistics_fee_cny"])
            finance = {
                "platform": decimal_value(existing_snapshot["platform_commission_cny"]),
                "logistics": existing_logistics,
                "refund": decimal_value(existing_snapshot["refund_cny"]),
                "compensation": decimal_value(existing_snapshot["compensation_cny"]),
                "acquiring": Decimal("0"), "other": Decimal("0"),
            }
            finance_fee = decimal_value(existing_snapshot["finance_fee_cny"])
        else:
            finance_fee = (
                finance["platform"] + finance["refund"] + finance["acquiring"]
                + finance["other"] - finance["compensation"]
            )
        ads_row = conn.execute(
            "SELECT COALESCE(SUM(CAST(a.spend_cny AS REAL)),0) cny,"
            "COALESCE(SUM(CAST(a.spend_rub AS REAL)),0) rub FROM ad_spend_transactions a "
            "LEFT JOIN orders o ON o.id=a.matched_order_id AND o.store_id=a.store_id "
            f"WHERE a.matched_order_id=? AND ({AD_MATCH_IS_EXACT_SQL})",
            (order_id,),
        ).fetchone()
        ads = decimal_value(ads_row["cny"])
        ads_rub = decimal_value(ads_row["rub"])
        revenue = decimal_value(order["buyer_paid_cny"])
        profit = revenue - purchase - finance_fee - finance["logistics"] - ads
        margin_value = Decimal("0") if revenue == 0 else profit / revenue
        snapshot_id = str(existing_snapshot["id"]) if existing_snapshot else stable_id(
            "snapshot", order["store_id"], order_id
        )
        payload = {
            "id": snapshot_id, "order_id": order_id, "posting_number": order["posting_number"], "sku": order["sku"],
            "product_name": order["product_name"], "order_date": order["order_date"],
            "buyer_paid_rub": money(order["buyer_paid_rub"]), "buyer_paid_cny": money(revenue),
            "ozon_original_charge_cny": money(previous("ozon_original_charge_cny", 0)),
            "finance_fee_rub": money(previous("finance_fee_rub", 0)), "finance_fee_cny": money(finance_fee),
            "platform_commission_cny": money(finance["platform"]), "logistics_fee_cny": money(finance["logistics"]),
            "refund_cny": money(finance["refund"]), "compensation_cny": money(finance["compensation"]),
            "ad_spend_rub": money(ads_rub), "ad_spend_cny": money(ads), "purchase_cost_cny": money(purchase),
            "final_profit_cny": money(profit), "profit_margin": str(margin_value.quantize(Decimal("0.000001"))),
            "data_sources": str(previous("data_sources", "Seller API / Finance API / Purchase / Ads when configured")),
            "is_unmatched": 0, "unmatched_reason": None, "created_at": str(previous("created_at", now_iso())),
            "revenue_calculation_version": str(previous("revenue_calculation_version", "finance_center_v1")),
            "profit_snapshot_recomputed_at": now_iso(),
            "revenue_source": str(previous("revenue_source", "finance_center_import_or_seller_api")),
            "revenue_warning_reasons": str(previous("revenue_warning_reasons", "")),
            "unit_purchase_cost_cny": money(unit_cost), "purchase_cost_source": purchase_source,
            "purchase_cost_update_batch_id": previous("purchase_cost_update_batch_id", None),
            "recompute_reason": "ad_exact_match_repair",
            "estimated_weight_g": str(previous("estimated_weight_g", "0")),
            "actual_weight_g": str(previous("actual_weight_g", "0")),
            "weight_used_g": str(previous("weight_used_g", "0")),
            "weight_source": str(previous("weight_source", master["weight_source"] if master else "missing")),
            "logistics_cost_source": "actual_finance" if decimal_value(finance["logistics"]) > 0 else "missing",
            "logistics_recomputed_at": now_iso(),
            "logistics_warning_reasons": str(previous("logistics_warning_reasons", "")), "store_id": order["store_id"],
            "logistics_cost_cny": money(finance["logistics"]),
            "logistics_source": str(previous(
                "logistics_source", "actual_finance" if decimal_value(finance["logistics"]) > 0 else "missing"
            )),
            "logistics_rule_version": str(previous("logistics_rule_version", "finance_transaction")),
            "logistics_rule_date": previous("logistics_rule_date", order["order_date"]),
            "logistics_rule_date_source": str(previous("logistics_rule_date_source", "order_date")),
        }
        conn.execute(
            f"INSERT INTO profit_snapshots({','.join(payload)}) VALUES({','.join('?' for _ in payload)}) "
            "ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{column}=excluded.{column}" for column in payload if column != "id"),
            tuple(payload.values()),
        )
        conn.execute("DELETE FROM profit_snapshots WHERE order_id=? AND id<>?", (order_id, snapshot_id))

    def _recompute_all(self, conn: sqlite3.Connection) -> None:
        for row in conn.execute("SELECT id FROM orders"):
            self._recompute_order(conn, str(row["id"]))

    def _recompute_product_orders(self, conn: sqlite3.Connection, store_id: str, sku: str, offer_id: str) -> None:
        rows = conn.execute(
            "SELECT id FROM orders WHERE store_id=? AND (sku=? OR (? != '' AND offer_id=?))",
            (store_id, sku, offer_id, offer_id),
        ).fetchall()
        for row in rows:
            self._recompute_order(conn, str(row["id"]))

    def _configured_shops(self, requested_store_id: str = "all") -> list[tuple[dict[str, Any], str, dict[str, str]]]:
        from workbench_stores import load_registry, read_secret  # Imported lazily to keep this module reusable.

        registry = load_registry(self.root)
        configured: list[tuple[dict[str, Any], str, dict[str, str]]] = []
        with self.connect() as conn:
            database_stores = conn.execute("SELECT id,store_name,store_alias FROM stores").fetchall()
            for shop in registry.get("shops") or []:
                if not shop.get("enabled", True):
                    continue
                shop_id = str(shop["id"])
                if requested_store_id not in {"", "all", shop_id}:
                    # A finance database can retain historical IDs.  Match those below before skipping.
                    names = {normalized_key(shop_id), normalized_key(shop.get("display_name")), normalized_key(shop.get("name"))}
                    historical_match = next(
                        (row for row in database_stores if requested_store_id == row["id"] and (
                            normalized_key(row["store_name"]) in names or normalized_key(row["store_alias"]) in names
                        )), None,
                    )
                    if historical_match is None:
                        continue
                names = {normalized_key(shop_id), normalized_key(shop.get("display_name")), normalized_key(shop.get("name"))}
                matched = next(
                    (row for row in database_stores if row["id"] == shop_id or normalized_key(row["store_name"]) in names or normalized_key(row["store_alias"]) in names),
                    None,
                )
                finance_store_id = str(matched["id"]) if matched else shop_id
                if not matched:
                    timestamp = now_iso()
                    conn.execute(
                        "INSERT INTO stores(id,store_name,store_alias,client_id_reference,status,sync_status,created_at,updated_at) "
                        "VALUES(?,?,?,?, 'active','idle',?,?)",
                        (finance_store_id, str(shop.get("display_name") or shop_id), str(shop.get("display_name") or shop_id),
                         str(shop.get("client_id_env") or ""), timestamp, timestamp),
                    )
                secret = read_secret(self.root, shop)
                client_id = secret.get(str(shop.get("client_id_env") or ""), "")
                api_key = secret.get(str(shop.get("api_key_env") or ""), "")
                if client_id and api_key:
                    configured.append((dict(shop), finance_store_id, {"client_id": client_id, "api_key": api_key}))
        return configured

    @staticmethod
    def _safe_http_error_detail(raw: bytes) -> str:
        text = raw.decode("utf-8", errors="replace")[:2000].strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                values = [parsed.get(key) for key in ("message", "reason", "details", "code")]
                text = " | ".join(str(value) for value in values if value not in (None, "")) or text
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        text = re.sub(
            r"(?i)(api[-_ ]?key|client[-_ ]?id|authorization)\s*[:=]\s*[^\s,;]+",
            r"\1=[redacted]",
            text,
        )
        return re.sub(r"\s+", " ", text).strip()[:240]

    @staticmethod
    def _seller_post(credentials: Mapping[str, str], endpoint: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"https://api-seller.ozon.ru{endpoint}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Client-Id": str(credentials["client_id"]), "Api-Key": str(credentials["api_key"]),
                "Content-Type": "application/json", "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = FinanceCenter._safe_http_error_detail(exc.read(2000))
            retry_after = 0
            try:
                retry_after = int(exc.headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                retry_after = 0
            retryable = int(exc.code) in {408, 409, 425, 429, 500, 502, 503, 504}
            message = f"Ozon 只读接口 {endpoint} 返回 HTTP {exc.code}"
            if detail:
                message += f"：{detail}"
            raise OzonReadOnlyError(
                message, endpoint=endpoint, status_code=int(exc.code), retryable=retryable,
                retry_after_seconds=retry_after,
            ) from exc
        except urllib.error.URLError as exc:
            raise OzonReadOnlyError(
                f"无法连接 Ozon 只读接口 {endpoint}", endpoint=endpoint, retryable=True,
            ) from exc

    def _exchange_rate(self, conn: sqlite3.Connection, day: str) -> tuple[Decimal, str]:
        day = self._excel_date(day) or date.today().isoformat()
        cached = conn.execute(
            "SELECT rub_per_unit,source FROM exchange_rates WHERE rate_date=? AND currency_code='CNY'", (day,),
        ).fetchone()
        if cached:
            return decimal_value(cached["rub_per_unit"]), str(cached["source"])
        try:
            parsed = datetime.strptime(day, "%Y-%m-%d")
            url = "https://www.cbr.ru/scripts/XML_daily.asp?" + urllib.parse.urlencode({"date_req": parsed.strftime("%d/%m/%Y")})
            request = urllib.request.Request(url, headers={"User-Agent": "AI-Factory-Finance-Center/1.0"})
            with urllib.request.urlopen(request, timeout=15) as response:
                root = ElementTree.fromstring(response.read())
            rate = None
            for valute in root.findall("Valute"):
                if (valute.findtext("CharCode") or "").strip() == "CNY":
                    nominal = decimal_value(valute.findtext("Nominal") or "1")
                    value = decimal_value((valute.findtext("Value") or "0").replace(",", "."))
                    if nominal > 0 and value > 0:
                        rate = value / nominal
                        break
            if rate is None:
                raise ValueError("官方汇率中没有 CNY")
            source = "cbr_transaction_date"
        except Exception:
            rate = self._rate_for_date(conn, day)
            source = "workbench_fixed_fallback"
        conn.execute(
            "INSERT INTO exchange_rates(rate_date,currency_code,rub_per_unit,source,updated_at) VALUES(?,'CNY',?,?,?) "
            "ON CONFLICT(rate_date,currency_code) DO UPDATE SET rub_per_unit=excluded.rub_per_unit,source=excluded.source,updated_at=excluded.updated_at",
            (day, str(rate.quantize(Decimal("0.000001"))), source, now_iso()),
        )
        return rate, source

    @staticmethod
    def _posting_products(posting: Mapping[str, Any]) -> list[dict[str, Any]]:
        products = posting.get("products") or posting.get("items") or []
        return [dict(item) for item in products if isinstance(item, dict)]

    def _upsert_api_order(
        self, conn: sqlite3.Connection, store_id: str, posting: Mapping[str, Any],
        product: Mapping[str, Any], source_kind: str, line_index: int,
    ) -> tuple[str, bool]:
        posting_number = str(posting.get("posting_number") or "").strip()
        sku = str(product.get("sku") or product.get("offer_id") or "").strip()
        if not posting_number or not sku:
            return "", False
        offer_id = str(product.get("offer_id") or "").strip()
        order_number = str(posting.get("order_id") or posting.get("order_number") or posting_number).strip()
        order_date = str(posting.get("in_process_at") or posting.get("created_at") or posting.get("shipment_date") or "")[:10]
        rate, rate_source = self._exchange_rate(conn, order_date)
        quantity = decimal_value(product.get("quantity")) or Decimal("1")
        unit_price = decimal_value(product.get("price") or product.get("old_price"))
        total = unit_price * quantity
        currency = str(product.get("currency_code") or product.get("currency") or "RUB").upper()
        if currency == "CNY":
            cny, rub = total, total * rate
        else:
            rub, cny = total, (total / rate if rate > 0 else Decimal("0"))
        matched = self._exact_order_match(conn, {
            "store_id": store_id, "posting_number": posting_number, "sku": sku,
        })
        row_id = matched or stable_id("seller-order", store_id, source_kind, posting_number, sku, offer_id, line_index)
        safe_payload = {
            "source_kind": source_kind, "posting_number": posting_number, "order_number": order_number,
            "created_at": posting.get("created_at"), "in_process_at": posting.get("in_process_at"),
            "shipment_date": posting.get("shipment_date"), "status": posting.get("status"),
            "exchange_rate_source": rate_source, "rub_per_cny": str(rate),
            "product": {
                "sku": product.get("sku"), "offer_id": offer_id, "product_id": product.get("product_id"),
                "name": product.get("name"), "price": product.get("price"), "quantity": product.get("quantity"),
                "currency_code": currency, "dimensions": product.get("dimensions"), "weight": product.get("weight"),
            },
        }
        row_hash = stable_id(safe_json(safe_payload))
        existing = conn.execute("SELECT row_hash FROM orders WHERE id=?", (row_id,)).fetchone()
        payload = {
            "id": row_id, "row_hash": row_hash, "file_hash": stable_id("seller-api", store_id, order_date),
            "posting_number": posting_number, "order_number": order_number, "sku": sku, "offer_id": offer_id,
            "product_name": str(product.get("name") or ""), "order_date": order_date,
            "buyer_paid_rub": money(rub), "buyer_paid_cny": money(cny), "status": str(posting.get("status") or ""),
            "raw_payload": safe_json(safe_payload), "created_at": now_iso(), "store_id": store_id,
        }
        conn.execute(
            f"INSERT INTO orders({','.join(payload)}) VALUES({','.join('?' for _ in payload)}) ON CONFLICT(id) DO UPDATE SET "
            + ",".join(f"{column}=excluded.{column}" for column in payload if column not in {"id", "created_at"}),
            tuple(payload.values()),
        )
        conn.execute(
            "INSERT INTO product_master(id,store_id,sku,offer_id,product_id,product_name,purchase_cost_source,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,'missing',?,?) ON CONFLICT(store_id,sku) DO UPDATE SET "
            "offer_id=COALESCE(NULLIF(excluded.offer_id,''),product_master.offer_id),"
            "product_id=COALESCE(NULLIF(excluded.product_id,''),product_master.product_id),"
            "product_name=COALESCE(NULLIF(excluded.product_name,''),product_master.product_name),updated_at=excluded.updated_at",
            (stable_id("product", store_id, sku), store_id, sku, offer_id, str(product.get("product_id") or ""),
             str(product.get("name") or ""), now_iso(), now_iso()),
        )
        self._recompute_order(conn, row_id)
        return row_id, not existing or str(existing["row_hash"]) != row_hash

    def _sync_orders(
        self, conn: sqlite3.Connection, store_id: str, credentials: Mapping[str, str],
        date_from: str, date_to: str,
    ) -> tuple[int, int, int]:
        seen = changed = calls = 0
        since = f"{date_from}T00:00:00.000Z"
        until = f"{date_to}T23:59:59.999Z"
        for source_kind, endpoint in (("fbo", "/v2/posting/fbo/list"), ("fbs", "/v3/posting/fbs/list")):
            offset = 0
            while True:
                payload = {
                    "dir": "ASC", "filter": {"since": since, "to": until, "status": ""},
                    "limit": 1000, "offset": offset, "with": {"analytics_data": True, "financial_data": True},
                }
                try:
                    response = self._seller_post(credentials, endpoint, payload)
                except OzonReadOnlyError as exc:
                    exc.read_api_calls += calls
                    raise
                calls += 1
                result = response.get("result") or []
                postings = result if isinstance(result, list) else (result.get("postings") or [])
                if not postings:
                    break
                for posting in postings:
                    if not isinstance(posting, dict):
                        continue
                    for line_index, product in enumerate(self._posting_products(posting)):
                        order_id, was_changed = self._upsert_api_order(conn, store_id, posting, product, source_kind, line_index)
                        if order_id:
                            seen += 1
                            changed += int(was_changed)
                if len(postings) < 1000:
                    break
                offset += len(postings)
        return seen, changed, calls

    @staticmethod
    def _finance_date_chunks(date_from: str, date_to: str) -> list[tuple[str, str]]:
        cursor = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
        chunks: list[tuple[str, str]] = []
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=FINANCE_QUERY_WINDOW_DAYS - 1), end)
            chunks.append((cursor.isoformat(), chunk_end.isoformat()))
            cursor = chunk_end + timedelta(days=1)
        return chunks

    @staticmethod
    def _service_buckets(operation: Mapping[str, Any], rate: Decimal) -> dict[str, Decimal]:
        buckets = {key: Decimal("0") for key in ("platform", "logistics", "refund", "compensation", "acquiring", "other")}
        commission = abs(decimal_value(operation.get("sale_commission")))
        delivery = abs(decimal_value(operation.get("delivery_charge"))) + abs(decimal_value(operation.get("return_delivery_charge")))
        buckets["platform"] = commission / rate if rate > 0 else Decimal("0")
        buckets["logistics"] = delivery / rate if rate > 0 else Decimal("0")
        for service in operation.get("services") or operation.get("item_services") or []:
            if not isinstance(service, dict):
                continue
            name = str(service.get("name") or service.get("service_name") or "").lower()
            amount = abs(decimal_value(service.get("price") or service.get("amount"))) / rate if rate > 0 else Decimal("0")
            if amount == 0:
                continue
            if any(token in name for token in ("комисс", "commission")):
                if buckets["platform"] == 0:
                    buckets["platform"] += amount
            elif any(token in name for token in ("логист", "достав", "delivery", "logistic", "last mile")):
                if buckets["logistics"] == 0:
                    buckets["logistics"] += amount
            elif any(token in name for token in ("эквай", "acquiring", "payment processing")):
                buckets["acquiring"] += amount
            elif any(token in name for token in ("возврат", "refund", "return")):
                buckets["refund"] += amount
            elif any(token in name for token in ("компенсац", "compensation")):
                buckets["compensation"] += amount
            else:
                buckets["other"] += amount

        # Several Ozon Global charges are returned as standalone negative
        # operations with an empty services array. Classify the operation-level
        # amount so delivery and acquiring costs are not silently treated as 0.
        operation_type = str(operation.get("operation_type") or "").lower()
        operation_name = str(operation.get("operation_type_name") or operation.get("service_name") or "").lower()
        operation_label = f"{operation_type} {operation_name}"
        operation_expense = max(-decimal_value(operation.get("amount")), Decimal("0"))
        operation_expense = operation_expense / rate if rate > 0 else Decimal("0")
        if operation_expense > 0 and operation_type not in {value.lower() for value in OZON_AD_OPERATION_TYPES}:
            if any(token in operation_label for token in (
                "deliveryservices", "достав", "логист", "transport", "транспорт",
            )):
                if buckets["logistics"] == 0:
                    buckets["logistics"] = operation_expense
            elif any(token in operation_label for token in ("эквай", "acquiring", "payment processing")):
                if buckets["acquiring"] == 0:
                    buckets["acquiring"] = operation_expense
            elif any(token in operation_label for token in ("комисс", "commission")):
                if buckets["platform"] == 0:
                    buckets["platform"] = operation_expense
        return buckets

    def _sync_finance(
        self, conn: sqlite3.Connection, store_id: str, credentials: Mapping[str, str],
        date_from: str, date_to: str,
    ) -> tuple[int, int, int]:
        page = 1
        seen = changed = calls = 0
        while True:
            payload = {
                "filter": {
                    "date": {"from": f"{date_from}T00:00:00.000Z", "to": f"{date_to}T23:59:59.999Z"},
                    "operation_type": [], "posting_number": "", "transaction_type": "all",
                },
                "page": page, "page_size": 1000,
            }
            try:
                response = self._seller_post(credentials, "/v3/finance/transaction/list", payload)
            except OzonReadOnlyError as exc:
                exc.read_api_calls += calls
                raise
            calls += 1
            result = response.get("result") or {}
            operations = result.get("operations") or []
            if not operations:
                break
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                seen += 1
                operation_id = str(operation.get("operation_id") or operation.get("id") or stable_id(operation))
                occurred_at = str(operation.get("operation_date") or operation.get("created_at") or "")
                rate, rate_source = self._exchange_rate(conn, occurred_at[:10])
                posting_value = operation.get("posting") or {}
                posting = str(posting_value.get("posting_number") or operation.get("posting_number") or "")
                items = [item for item in operation.get("items") or [] if isinstance(item, dict)]
                sku = str(items[0].get("sku") or "") if len(items) == 1 else ""
                matched = self._exact_order_match(conn, {"store_id": store_id, "posting_number": posting, "sku": sku})
                amount_rub = decimal_value(operation.get("amount"))
                amount_cny = amount_rub / rate if rate > 0 else Decimal("0")
                buckets = self._service_buckets(operation, rate)
                safe_payload = {
                    "operation_id": operation_id, "operation_date": occurred_at,
                    "operation_type": operation.get("operation_type"), "operation_type_name": operation.get("operation_type_name"),
                    "posting_number": posting, "items": items, "services": operation.get("services") or [],
                    "amount": operation.get("amount"), "sale_commission": operation.get("sale_commission"),
                    "delivery_charge": operation.get("delivery_charge"),
                    "return_delivery_charge": operation.get("return_delivery_charge"),
                    "exchange_rate_source": rate_source, "rub_per_cny": str(rate),
                }
                row_id = stable_id("seller-finance", store_id, operation_id)
                row_hash = stable_id(safe_json(safe_payload))
                previous = conn.execute("SELECT row_hash FROM finance_transactions WHERE id=?", (row_id,)).fetchone()
                row = {
                    "id": row_id, "row_hash": row_hash, "file_hash": stable_id("seller-finance", store_id, date_from, date_to),
                    "matched_order_id": matched, "posting_number": posting, "order_number": "", "sku": sku,
                    "occurred_at": occurred_at, "operation_type": str(operation.get("operation_type") or ""),
                    "service_name": str(operation.get("operation_type_name") or ""), "amount_rub": money(amount_rub),
                    "amount_cny": money(amount_cny), "platform_commission_cny": money(buckets["platform"]),
                    "logistics_fee_cny": money(buckets["logistics"]), "refund_cny": money(buckets["refund"]),
                    "compensation_cny": money(buckets["compensation"]), "acquiring_cny": money(buckets["acquiring"]),
                    "other_fee_cny": money(buckets["other"]), "raw_payload": safe_json(safe_payload),
                    "created_at": now_iso(), "store_id": store_id,
                }
                conn.execute(
                    f"INSERT INTO finance_transactions({','.join(row)}) VALUES({','.join('?' for _ in row)}) ON CONFLICT(id) DO UPDATE SET "
                    + ",".join(f"{column}=excluded.{column}" for column in row if column not in {"id", "created_at"}),
                    tuple(row.values()),
                )
                changed += int(not previous or str(previous["row_hash"]) != row_hash)
                if matched:
                    self._recompute_order(conn, matched)
                    conn.execute("DELETE FROM import_unmatched_rows WHERE id=?", (stable_id("seller-finance-unmatched", store_id, operation_id),))
                else:
                    unmatched_id = stable_id("seller-finance-unmatched", store_id, operation_id)
                    conn.execute(
                        "INSERT INTO import_unmatched_rows(id,store_id,file_type,file_name,file_path,source_row_number,occurred_at,posting_number,"
                        "order_number,sku,offer_id,amount_rub,amount_cny,reason,resolution_status,raw_payload,created_at) "
                        "VALUES(?,?,'finance','Seller API','',0,?,?, '',?, '',?,?,?,'open',?,?) ON CONFLICT(id) DO UPDATE SET "
                        "occurred_at=excluded.occurred_at,posting_number=excluded.posting_number,sku=excluded.sku,"
                        "amount_rub=excluded.amount_rub,amount_cny=excluded.amount_cny,reason=excluded.reason,raw_payload=excluded.raw_payload",
                        (unmatched_id, store_id, occurred_at, posting, sku, money(amount_rub), money(amount_cny),
                         "没有唯一精确匹配的订单；金额未进入订单利润", safe_json(safe_payload), now_iso()),
                    )
            page_count = int(result.get("page_count") or page)
            if page >= page_count or len(operations) < 1000:
                break
            page += 1
        return seen, changed, calls

    def _sync_product_images(
        self, conn: sqlite3.Connection, store_id: str, credentials: Mapping[str, str],
    ) -> tuple[int, int]:
        offers = [
            str(row["offer_id"]) for row in conn.execute(
                "SELECT DISTINCT offer_id FROM product_master WHERE store_id=? AND trim(COALESCE(offer_id,''))!=''",
                (store_id,),
            ) if row["offer_id"]
        ]
        changed = calls = 0
        for start in range(0, len(offers), 1000):
            batch = offers[start:start + 1000]
            try:
                response = self._seller_post(credentials, "/v3/product/info/list", {"offer_id": batch, "product_id": [], "sku": []})
            except RuntimeError:
                # Images are useful but must never make financial synchronization fail.
                calls += 1
                continue
            calls += 1
            for item in (response.get("items") or (response.get("result") or {}).get("items") or []):
                if not isinstance(item, dict):
                    continue
                images = item.get("images") or []
                image_url = str((images[0] if images else item.get("primary_image") or ""))
                offer_id = str(item.get("offer_id") or "")
                if offer_id and image_url:
                    changed += conn.execute(
                        "UPDATE product_master SET image_url=?,updated_at=? WHERE store_id=? AND offer_id=? AND COALESCE(image_url,'')!=?",
                        (image_url, now_iso(), store_id, offer_id, image_url),
                    ).rowcount
        return changed, calls

    def sync(
        self, *, store_id: str = "all", date_from: Optional[str] = None,
        date_to: Optional[str] = None, trigger: str = "manual",
    ) -> dict[str, Any]:
        self.initialize()
        end = date_to or date.today().isoformat()
        start = date_from or (date.fromisoformat(end) - timedelta(days=89)).isoformat()
        start, end = self._period(start, end)
        circuit = self._active_read_circuit()
        if circuit:
            raise RuntimeError(
                f"Ozon 只读同步已自动暂停至 {circuit['blocked_until']}，避免重复请求；"
                "请等待保护期结束后再试"
            )
        if not self._sync_lock.acquire(blocking=False):
            raise RuntimeError("财务同步正在进行，请稍后查看")
        try:
            shops = self._configured_shops(store_id)
            if not shops:
                raise ValueError("没有找到已启用且凭据完整的 Ozon 店铺")
            results = []
            for shop_index, (shop, finance_store_id, credentials) in enumerate(shops):
                run_id = f"sync-{uuid.uuid4().hex[:16]}"
                started = now_iso()
                with self.connect() as conn:
                    conn.execute(
                        "INSERT INTO finance_sync_runs(id,store_id,started_at,date_from,date_to,trigger,status) VALUES(?,?,?,?,?,?, 'running')",
                        (run_id, finance_store_id, started, start, end, trigger),
                    )
                    conn.execute("UPDATE stores SET sync_status='running',sync_error=NULL,updated_at=? WHERE id=?", (started, finance_store_id))
                orders_seen = order_changes = order_calls = 0
                finance_seen = finance_changes = finance_calls = 0
                image_changes = image_calls = 0
                try:
                    with self.connect() as conn:
                        orders_seen, order_changes, order_calls = self._sync_orders(conn, finance_store_id, credentials, start, end)
                        for chunk_start, chunk_end in self._finance_date_chunks(start, end):
                            chunk_seen, chunk_changes, chunk_calls = self._sync_finance(
                                conn, finance_store_id, credentials, chunk_start, chunk_end,
                            )
                            finance_seen += chunk_seen
                            finance_changes += chunk_changes
                            finance_calls += chunk_calls
                        image_changes, image_calls = self._sync_product_images(conn, finance_store_id, credentials)
                        finished = now_iso()
                        coverage_id = stable_id("coverage", finance_store_id, start, end, run_id)
                        conn.execute(
                            "INSERT INTO finance_data_coverage(id,store_id,date_from,date_to,orders_status,finance_status,ads_status,source,updated_at) "
                            "VALUES(?,?,?,?,'complete','complete','complete_from_finance','ozon_read_only_sync',?)",
                            (coverage_id, finance_store_id, start, end, finished),
                        )
                        conn.execute(
                            "UPDATE finance_sync_runs SET finished_at=?,status='success',orders_seen=?,finance_seen=?,changed_rows=?,"
                            "read_api_calls=?,write_api_calls=0 WHERE id=?",
                            (finished, orders_seen, finance_seen, order_changes + finance_changes + image_changes,
                             order_calls + finance_calls + image_calls, run_id),
                        )
                        conn.execute(
                            "UPDATE stores SET sync_status='success',sync_error=NULL,last_sync_at=?,updated_at=? WHERE id=?",
                            (finished, finished, finance_store_id),
                        )
                    results.append({
                        "store_id": finance_store_id, "store_name": shop.get("display_name") or shop.get("id"),
                        "status": "success", "orders_seen": orders_seen, "finance_seen": finance_seen,
                        "changed_rows": order_changes + finance_changes + image_changes,
                        "read_api_calls": order_calls + finance_calls + image_calls,
                        "ads_status": "complete_from_finance", "write_api_calls": 0,
                    })
                except Exception as exc:
                    message = str(exc)[:240]
                    failed_read_calls = (
                        order_calls + finance_calls + image_calls
                        + int(getattr(exc, "read_api_calls", 0) or 0)
                    )
                    with self.connect() as conn:
                        conn.execute(
                            "UPDATE finance_sync_runs SET finished_at=?,status='failed',error=?,read_api_calls=?,write_api_calls=0 WHERE id=?",
                            (now_iso(), message, failed_read_calls, run_id),
                        )
                        conn.execute(
                            "UPDATE stores SET sync_status='failed',sync_error=?,updated_at=? WHERE id=?",
                            (message, now_iso(), finance_store_id),
                        )
                    failed_item = {
                        "store_id": finance_store_id, "store_name": shop.get("display_name") or shop.get("id"),
                        "status": "failed", "error": message, "read_api_calls": failed_read_calls,
                        "retryable": bool(getattr(exc, "retryable", False)),
                        "http_status": getattr(exc, "status_code", None),
                        "endpoint": getattr(exc, "endpoint", None),
                        "retry_after_seconds": int(getattr(exc, "retry_after_seconds", 0) or 0),
                        "write_api_calls": 0,
                    }
                    results.append(failed_item)
                    if isinstance(exc, OzonReadOnlyError):
                        circuit = self._open_read_circuit(exc)
                        failed_item["circuit_blocked_until"] = circuit["blocked_until"]
                        for skipped_shop, skipped_store_id, _credentials in shops[shop_index + 1:]:
                            results.append({
                                "store_id": skipped_store_id,
                                "store_name": skipped_shop.get("display_name") or skipped_shop.get("id"),
                                "status": "skipped",
                                "error": "前一店铺触发 Ozon 只读接口保护，本批剩余店铺未再请求",
                                "read_api_calls": 0, "write_api_calls": 0,
                            })
                        break
            complete = bool(results) and all(item["status"] == "success" for item in results)
            any_success = any(item["status"] == "success" for item in results)
            if complete:
                completed_at = now_iso()
                with self.connect() as conn:
                    self._set_meta(conn, "last_successful_sync_date", date.today().isoformat())
                    self._set_meta(conn, "last_successful_sync_at", completed_at)
                    self._set_meta(conn, "scheduled_sync_state", safe_json({
                        "due_day": date.today().isoformat(), "status": "completed",
                        "completed_at": completed_at, "attempt_count": 0,
                    }))
                self._clear_read_circuit()
            return {
                "date_from": start, "date_to": end, "trigger": trigger, "stores": results,
                "success": complete, "complete": complete,
                "partial_success": any_success and not complete,
                "ozon_write_api_calls": 0, "inventory_api_calls": 0,
            }
        finally:
            self._sync_lock.release()

    def sync_status(self, limit: int = 50) -> dict[str, Any]:
        self.initialize()
        with self.connect(readonly=True) as conn:
            runs = conn.execute(
                "SELECT id,store_id,started_at,finished_at,date_from,date_to,trigger,status,orders_seen,finance_seen,"
                "changed_rows,error,read_api_calls,write_api_calls FROM finance_sync_runs ORDER BY started_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
            last = conn.execute("SELECT value FROM finance_center_meta WHERE key='last_successful_sync_date'").fetchone()
            last_at = conn.execute("SELECT value FROM finance_center_meta WHERE key='last_successful_sync_at'").fetchone()
            if not last_at:
                last_at = conn.execute(
                    "SELECT MAX(finished_at) value FROM finance_sync_runs WHERE status='success' AND finished_at IS NOT NULL"
                ).fetchone()
            scheduler_state = self._meta_json(conn, "scheduled_sync_state")
            circuit = self._meta_json(conn, "ozon_read_circuit")
        circuit_active = False
        if circuit.get("blocked_until"):
            try:
                circuit_active = datetime.fromisoformat(str(circuit["blocked_until"])) > self._local_naive()
            except ValueError:
                circuit_active = False
        return {
            "schedule": "每天 15:00（北京时间）；关机错过后下次启动补跑；失败时持久化退避，禁止每分钟重跑",
            "rescan_days": 90, "last_successful_sync_date": last["value"] if last else None,
            "last_successful_sync_at": last_at["value"] if last_at and last_at["value"] else None,
            "scheduler_state": scheduler_state,
            "circuit_breaker": {**circuit, "active": circuit_active} if circuit else {"active": False},
            "automatic_retry_policy": {
                "non_retryable_http_4xx": "当天停止自动重试",
                "retryable_network_or_429_5xx": "至少退避1小时，最多自动尝试2次",
                "poll_interval_seconds": 300,
            },
            "runs": [dict(row) for row in runs], "stores": self.store_options(),
            "ozon_write_api_calls": 0, "inventory_api_calls": 0,
        }

    def recover_interrupted_syncs(self) -> int:
        """Close sync ledger rows left running by an earlier local process exit."""
        self.initialize()
        finished = now_iso()
        message = "本地服务上次退出，未完成的只读同步已中断"
        with self.connect() as conn:
            count = conn.execute(
                "UPDATE finance_sync_runs SET status='interrupted',finished_at=?,error=?,write_api_calls=0 "
                "WHERE status='running'",
                (finished, message),
            ).rowcount
            if count:
                conn.execute(
                    "UPDATE stores SET sync_status='idle',sync_error=?,updated_at=? WHERE sync_status='running'",
                    (message, finished),
                )
        return int(count)

    def scheduler_tick(self, current: Optional[datetime] = None) -> dict[str, Any]:
        local_current = self._local_naive(current)
        due_day = local_current.date() if local_current.hour >= 15 else local_current.date() - timedelta(days=1)
        due_at = datetime.combine(due_day, datetime.min.time()).replace(hour=15)
        status = self.sync_status(limit=1)
        last_at_text = status.get("last_successful_sync_at")
        if last_at_text:
            last_at = datetime.fromisoformat(last_at_text)
            if last_at.tzinfo:
                last_at = last_at.astimezone().replace(tzinfo=None)
            if last_at >= due_at:
                return {"status": "not_due", "due_day": due_day.isoformat(), "due_at": due_at.isoformat()}
        elif status.get("last_successful_sync_date") and status["last_successful_sync_date"] > due_day.isoformat():
            return {"status": "not_due", "due_day": due_day.isoformat(), "due_at": due_at.isoformat()}

        state = dict(status.get("scheduler_state") or {})
        if state.get("due_day") != due_day.isoformat():
            with self.connect() as conn:
                legacy_failure = conn.execute(
                    "SELECT started_at,error FROM finance_sync_runs "
                    "WHERE trigger='scheduled_catch_up' AND substr(started_at,1,10)=? "
                    "ORDER BY started_at DESC LIMIT 1",
                    (due_day.isoformat(),),
                ).fetchone()
                if legacy_failure:
                    next_due = datetime.combine(due_day + timedelta(days=1), datetime.min.time()).replace(hour=15)
                    error_text = str(legacy_failure["error"] or "历史计划同步已失败")[:240]
                    state = {
                        "due_day": due_day.isoformat(), "attempt_count": 1,
                        "last_attempt_at": str(legacy_failure["started_at"]),
                        "status": "blocked_for_day", "last_error": error_text,
                        "retry_not_before": next_due.isoformat(timespec="seconds"),
                    }
                    self._set_meta(conn, "scheduled_sync_state", safe_json(state))
                    http_status = 400 if "HTTP 400" in error_text else None
                    circuit = {
                        "opened_at": local_current.isoformat(timespec="seconds"),
                        "blocked_until": next_due.isoformat(timespec="seconds"),
                        "endpoint": "/v3/finance/transaction/list" if "finance/transaction/list" in error_text else "unknown",
                        "http_status": http_status, "retryable": False, "reason": error_text,
                    }
                    self._set_meta(conn, "ozon_read_circuit", safe_json(circuit))
                    return {
                        "status": "blocked_for_day", "due_day": due_day.isoformat(),
                        "due_at": due_at.isoformat(), "retry_not_before": state["retry_not_before"],
                        "reason": "已识别今天的历史失败记录，未再次请求 Ozon",
                    }
            state = {"due_day": due_day.isoformat(), "attempt_count": 0, "status": "pending"}

        if state.get("status") in {"success", "blocked_for_day"}:
            return {
                "status": "not_due" if state["status"] == "success" else "blocked_for_day",
                "due_day": due_day.isoformat(), "due_at": due_at.isoformat(),
                "retry_not_before": state.get("retry_not_before"),
            }
        if state.get("status") == "running":
            with self.connect(readonly=True) as conn:
                running_count = int(conn.execute(
                    "SELECT COUNT(*) FROM finance_sync_runs WHERE trigger='scheduled_catch_up' AND status='running'"
                ).fetchone()[0])
            if running_count:
                return {
                    "status": "in_progress", "due_day": due_day.isoformat(),
                    "due_at": due_at.isoformat(), "reason": "同一批计划同步仍在运行",
                }
            next_due = datetime.combine(due_day + timedelta(days=1), datetime.min.time()).replace(hour=15)
            state.update({
                "status": "blocked_for_day", "last_error": "上次计划同步被服务退出中断，未自动重放",
                "retry_not_before": next_due.isoformat(timespec="seconds"),
            })
            with self.connect() as conn:
                self._set_meta(conn, "scheduled_sync_state", safe_json(state))
            return {
                "status": "blocked_for_day", "due_day": due_day.isoformat(),
                "due_at": due_at.isoformat(), "retry_not_before": state["retry_not_before"],
                "reason": state["last_error"],
            }
        retry_not_before = str(state.get("retry_not_before") or "")
        if retry_not_before:
            try:
                if datetime.fromisoformat(retry_not_before) > local_current:
                    return {
                        "status": "backoff", "due_day": due_day.isoformat(),
                        "due_at": due_at.isoformat(), "retry_not_before": retry_not_before,
                    }
            except ValueError:
                pass
        if int(state.get("attempt_count") or 0) >= SCHEDULED_MAX_ATTEMPTS:
            state["status"] = "blocked_for_day"
            with self.connect() as conn:
                self._set_meta(conn, "scheduled_sync_state", safe_json(state))
            return {"status": "blocked_for_day", "due_day": due_day.isoformat(), "due_at": due_at.isoformat()}

        circuit = self._active_read_circuit(local_current)
        if circuit:
            return {
                "status": "circuit_open", "due_day": due_day.isoformat(),
                "due_at": due_at.isoformat(), "retry_not_before": circuit["blocked_until"],
            }

        attempt_count = int(state.get("attempt_count") or 0) + 1
        state.update({
            "due_day": due_day.isoformat(), "attempt_count": attempt_count,
            "last_attempt_at": local_current.isoformat(timespec="seconds"),
            "status": "running", "retry_not_before": None,
        })
        with self.connect() as conn:
            self._set_meta(conn, "scheduled_sync_state", safe_json(state))
        try:
            result = self.sync(
                date_from=(local_current.date() - timedelta(days=89)).isoformat(),
                date_to=local_current.date().isoformat(), trigger="scheduled_catch_up",
            )
        except RuntimeError as exc:
            message = str(exc)[:240]
            if "正在进行" in message or "自动暂停" in message:
                state.update({
                    "attempt_count": max(0, attempt_count - 1), "status": "backoff",
                    "last_error": message,
                    "retry_not_before": (local_current + timedelta(minutes=5)).isoformat(timespec="seconds"),
                })
            else:
                state.update({"status": "blocked_for_day", "last_error": message})
            with self.connect() as conn:
                self._set_meta(conn, "scheduled_sync_state", safe_json(state))
            return {
                "status": state["status"], "due_day": due_day.isoformat(),
                "due_at": due_at.isoformat(), "retry_not_before": state.get("retry_not_before"),
                "reason": message,
            }

        if bool(result.get("complete", result.get("success", False))):
            state.update({"status": "success", "last_error": None, "retry_not_before": None})
            tick_status = "completed"
        else:
            failed = next((item for item in result.get("stores", []) if item.get("status") == "failed"), {})
            retryable = bool(failed.get("retryable"))
            if retryable and attempt_count < SCHEDULED_MAX_ATTEMPTS:
                delay = max(
                    SCHEDULED_RETRY_DELAY,
                    timedelta(seconds=int(failed.get("retry_after_seconds") or 0)),
                )
                retry_at = str(failed.get("circuit_blocked_until") or (local_current + delay).isoformat(timespec="seconds"))
                state.update({
                    "status": "backoff", "last_error": str(failed.get("error") or "")[:240],
                    "retry_not_before": retry_at,
                })
                tick_status = "backoff"
            else:
                next_due = datetime.combine(due_day + timedelta(days=1), datetime.min.time()).replace(hour=15)
                state.update({
                    "status": "blocked_for_day", "last_error": str(failed.get("error") or "")[:240],
                    "retry_not_before": next_due.isoformat(timespec="seconds"),
                })
                tick_status = "blocked_for_day"
        with self.connect() as conn:
            self._set_meta(conn, "scheduled_sync_state", safe_json(state))
        return {
            "status": tick_status, "due_day": due_day.isoformat(), "due_at": due_at.isoformat(),
            "retry_not_before": state.get("retry_not_before"), "result": result,
        }
