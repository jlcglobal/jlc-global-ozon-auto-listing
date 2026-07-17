"""SQLite task-state projection and idempotent JSON migration.

The product JSON files remain the source of truth during the migration window.
This module is deliberately stdlib-only and never opens an Ozon client.  It
records local task state, per-store publications, and per-SKU remote IDs so a
read-only recovery worker can operate without touching the web process.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


SCHEMA_VERSION = "003"
DEFAULT_DB_RELATIVE_PATH = Path("runtime/task-db.sqlite3")
CUTOVER_MARKER = Path("runtime/task-db-cutover.json")
UNKNOWN = {None, "", "unknown", "UNKNOWN", "null", "None"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def unknown(value: Any) -> bool:
    return value in UNKNOWN or str(value or "").strip().lower() in {"unknown", "null", "none"}


def _aggregate_states(states: Iterable[str]) -> str:
    values = [str(value) for value in states if value not in {"NOT_SELECTED", "SELECTED"}]
    if not values:
        return "UNKNOWN"
    created = values.count("CREATED")
    pending = values.count("PENDING_REMOTE") + values.count("SUBMITTED")
    handoff = values.count("HANDED_OFF_TO_OZON")
    failed = values.count("FAILED")
    if created == len(values):
        return "CREATED"
    if handoff == len(values) or (handoff and not failed):
        return "HANDED_OFF_TO_OZON"
    if pending == len(values):
        return "PENDING_REMOTE"
    if failed == len(values):
        return "FAILED"
    if failed:
        return "PARTIAL_FAILED"
    return "PARTIAL"


def _migrate_task_ids_to_local_handoff(db: sqlite3.Connection) -> None:
    """Make a returned Ozon task id the local terminal without remote polling.

    Older P0 projections stored these rows as PENDING_REMOTE and left the UI at
    99% forever even though the production contract is to hand the item off to
    the Ozon product-card backend as soon as a task id is recorded.
    """
    applied = db.execute(
        "SELECT 1 FROM schema_migrations WHERE version='003'"
    ).fetchone()
    if applied:
        return
    changed_at = now()
    known = "task_id IS NOT NULL AND TRIM(task_id) NOT IN ('', 'unknown', 'UNKNOWN', 'null', 'None')"
    db.execute(
        f"UPDATE store_sku_publications SET status='HANDED_OFF_TO_OZON', updated_at=? "
        f"WHERE {known} AND status IN ('SUBMITTED','PENDING_REMOTE','UPLOADING','QUEUED','OZON_MODERATION')",
        (changed_at,),
    )
    db.execute(
        f"UPDATE tasks SET status='HANDED_OFF_TO_OZON', next_check_at=NULL, "
        f"terminal_reason='task_id_local_handoff', updated_at=? "
        f"WHERE {known} AND status IN ('SUBMITTED','PENDING_REMOTE','UPLOADING','QUEUED','OZON_MODERATION')",
        (changed_at,),
    )
    publication_rows = db.execute(
        "SELECT id, product_id FROM store_publications WHERE selected=1"
    ).fetchall()
    for publication in publication_rows:
        sku_states = [
            str(row[0])
            for row in db.execute(
                "SELECT status FROM store_sku_publications WHERE publication_id=?",
                (publication["id"],),
            ).fetchall()
        ]
        if sku_states and all(state in {"CREATED", "HANDED_OFF_TO_OZON"} for state in sku_states):
            store_status = "CREATED" if all(state == "CREATED" for state in sku_states) else "HANDED_OFF_TO_OZON"
            db.execute(
                "UPDATE store_publications SET status=?, updated_at=? WHERE id=?",
                (store_status, changed_at, publication["id"]),
            )
    product_ids = [str(row[0]) for row in db.execute("SELECT product_id FROM products").fetchall()]
    for product_id in product_ids:
        states = [
            str(row[0])
            for row in db.execute(
                "SELECT status FROM store_publications WHERE product_id=? AND selected=1",
                (product_id,),
            ).fetchall()
        ]
        db.execute(
            "UPDATE products SET aggregate_status=?, created_store_count=?, pending_store_count=?, "
            "failed_store_count=?, updated_at=? WHERE product_id=?",
            (
                _aggregate_states(states),
                sum(state == "CREATED" for state in states),
                sum(state in {"SUBMITTED", "PENDING_REMOTE"} for state in states),
                sum(state == "FAILED" for state in states),
                changed_at,
                product_id,
            ),
        )
    db.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES ('003', ?)",
        (changed_at,),
    )


def database_path(root: Path) -> Path:
    return root / DEFAULT_DB_RELATIVE_PATH


def _ensure_private_database(root: Path) -> None:
    """Keep the task database and SQLite sidecars readable only by the owner."""
    for path in (
        database_path(root),
        database_path(root).with_name(database_path(root).name + "-wal"),
        database_path(root).with_name(database_path(root).name + "-shm"),
    ):
        if path.exists():
            try:
                path.chmod(0o600)
            except OSError:
                pass


def _after_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc).astimezone() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def remote_backoff_seconds(read_query_count: int) -> int:
    """Return the next read-only poll delay: 1m, 5m, 15m, 30m, then 1h."""
    return (60, 300, 900, 1800, 3600)[min(max(int(read_query_count), 0), 4)]


def cutover_active(root: Path) -> bool:
    marker = root / CUTOVER_MARKER
    if not marker.is_file():
        return False
    try:
        return json.loads(marker.read_text(encoding="utf-8")).get("state") == "sqlite_authoritative"
    except (OSError, json.JSONDecodeError):
        return False


def connect(root: Path) -> sqlite3.Connection:
    path = database_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA journal_mode=WAL")
    _ensure_private_database(root)
    return connection


@contextmanager
def database(root: Path):
    connection = connect(root)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()
        # SQLite may create/recreate WAL sidecars during commit.  Reapply the
        # private mode after every transaction, including recovery writes.
        _ensure_private_database(root)


def initialize(root: Path) -> Path:
    """Create the local task database without contacting any external service."""
    with database(root) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY,
                owner_id TEXT,
                category_id INTEGER,
                type_id INTEGER,
                aggregate_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                target_store_count INTEGER NOT NULL DEFAULT 0,
                created_store_count INTEGER NOT NULL DEFAULT 0,
                pending_store_count INTEGER NOT NULL DEFAULT 0,
                failed_store_count INTEGER NOT NULL DEFAULT 0,
                status_json_hash TEXT,
                source_json_hash TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS store_publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                store_id TEXT NOT NULL,
                selected INTEGER NOT NULL DEFAULT 0,
                action TEXT NOT NULL DEFAULT 'UNKNOWN',
                status TEXT NOT NULL DEFAULT 'NOT_SELECTED',
                payload_hash TEXT,
                api_write_count INTEGER NOT NULL DEFAULT 0,
                write_retry_count INTEGER NOT NULL DEFAULT 0,
                submission_version INTEGER NOT NULL DEFAULT 0,
                last_submitted_at TEXT,
                last_checked_at TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(product_id, store_id)
            );
            CREATE TABLE IF NOT EXISTS store_sku_publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                publication_id INTEGER NOT NULL REFERENCES store_publications(id) ON DELETE CASCADE,
                sku_id TEXT NOT NULL,
                offer_id TEXT,
                task_id TEXT,
                ozon_product_id TEXT,
                status TEXT NOT NULL DEFAULT 'NOT_SUBMITTED',
                error_code TEXT,
                error_message TEXT,
                payload_hash TEXT,
                last_checked_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(publication_id, sku_id)
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                store_id TEXT NOT NULL,
                publication_id INTEGER REFERENCES store_publications(id) ON DELETE CASCADE,
                task_type TEXT NOT NULL DEFAULT 'CREATE',
                status TEXT NOT NULL,
                write_attempt_count INTEGER NOT NULL DEFAULT 0,
                read_query_count INTEGER NOT NULL DEFAULT 0,
                payload_hash TEXT,
                submitted_at TEXT,
                next_check_at TEXT,
                last_checked_at TEXT,
                terminal_reason TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(store_id, task_id, task_type)
            );
            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                product_id TEXT NOT NULL,
                store_id TEXT,
                from_status TEXT,
                to_status TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                store_id TEXT,
                sku_id TEXT,
                artifact_type TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(product_id, store_id, sku_id, artifact_type, path)
            );
            CREATE TABLE IF NOT EXISTS archive_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                archived_at TEXT NOT NULL,
                cleanup_after TEXT NOT NULL,
                reason TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                summary_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(entity_type, entity_id)
            );
            CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                local_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                archived_at TEXT,
                cleanup_after TEXT,
                archive_reason TEXT,
                summary_json TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES ('002', ?)",
            (now(),),
        )
        _migrate_task_ids_to_local_handoff(db)
        # Older P0 databases predate next_check_at.  Backfill the schedule
        # without contacting Ozon; existing read counts determine the next
        # read-only delay and no CREATE/UPDATE can be triggered here.
        for row in db.execute(
            "SELECT id, read_query_count FROM tasks "
            "WHERE status IN ('SUBMITTED','PENDING_REMOTE') AND next_check_at IS NULL"
        ).fetchall():
            db.execute(
                "UPDATE tasks SET next_check_at=?, updated_at=? WHERE id=?",
                (_after_seconds(remote_backoff_seconds(int(row["read_query_count"] or 0))), now(), row["id"]),
            )
    _ensure_private_database(root)
    return database_path(root)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _hash_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sku_status(sku: Mapping[str, Any], publication_status: str) -> str:
    product_id = sku.get("ozon_product_id")
    task_id = sku.get("task_id")
    if not unknown(product_id):
        return "CREATED"
    if publication_status in {"FAILED", "QUERY_ERROR", "FAILED_HARD_BLOCKER"}:
        return "FAILED"
    if not unknown(task_id):
        return "HANDED_OFF_TO_OZON"
    if publication_status in {"SUBMITTED", "PENDING_REMOTE", "UPLOADING", "QUEUED", "OZON_MODERATION", "HANDED_OFF_TO_OZON"}:
        return "HANDED_OFF_TO_OZON" if publication_status == "HANDED_OFF_TO_OZON" else ("PENDING_REMOTE" if publication_status != "SUBMITTED" else "SUBMITTED")
    return "NOT_SUBMITTED"


def _publication_status(record: Mapping[str, Any]) -> str:
    raw = str(record.get("status") or "NOT_SELECTED").upper()
    skus = list(record.get("sku_publications") or [])
    if raw in {"FAILED", "QUERY_ERROR", "FAILED_HARD_BLOCKER"}:
        return "FAILED"
    if skus and all(not unknown(item.get("ozon_product_id")) for item in skus):
        return "CREATED"
    if skus and any(not unknown(item.get("task_id")) for item in skus) and all(
        not unknown(item.get("task_id")) or not unknown(item.get("ozon_product_id")) for item in skus
    ):
        return "HANDED_OFF_TO_OZON"
    if raw in {"SUCCESS", "IMPORTED", "ACTIVE", "UPLOADED"}:
        return "PENDING_REMOTE"
    if raw in {"PENDING_REMOTE", "OZON_MODERATION"}:
        return "PENDING_REMOTE"
    if raw in {"SUBMITTED", "UPLOADING", "QUEUED"}:
        return "SUBMITTED"
    if raw == "HANDED_OFF_TO_OZON":
        return "HANDED_OFF_TO_OZON"
    return "NOT_SELECTED" if raw == "NOT_SELECTED" else "SELECTED"


def _aggregate(states: Iterable[str]) -> str:
    return _aggregate_states(states)


def _upsert_product(db: sqlite3.Connection, product_dir: Path, publications: Mapping[str, Any], status: Mapping[str, Any]) -> None:
    product_id = product_dir.name
    source = _read_json(product_dir / "input/source.json", {})
    category = _read_json(product_dir / "output/ozon-category.json", {})
    category_id = category.get("category_id") or (status.get("category") or {}).get("category_id")
    type_id = category.get("type_id") or (status.get("category") or {}).get("type_id")
    selected = [record for record in (publications.get("stores") or {}).values() if record.get("selected")]
    mapped = [(record, _publication_status(record)) for record in selected]
    states = [state for _, state in mapped]
    db.execute(
        """INSERT INTO products(product_id, owner_id, category_id, type_id, aggregate_status,
           target_store_count, created_store_count, pending_store_count, failed_store_count,
           status_json_hash, source_json_hash, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(product_id) DO UPDATE SET owner_id=excluded.owner_id,
           category_id=excluded.category_id, type_id=excluded.type_id,
           aggregate_status=excluded.aggregate_status, target_store_count=excluded.target_store_count,
           created_store_count=excluded.created_store_count, pending_store_count=excluded.pending_store_count,
           failed_store_count=excluded.failed_store_count, status_json_hash=excluded.status_json_hash,
           source_json_hash=excluded.source_json_hash, updated_at=excluded.updated_at""",
        (
            product_id, source.get("owner_id"), category_id, type_id, _aggregate(states),
            len(mapped), sum(state == "CREATED" for state in states),
            sum(state in {"SUBMITTED", "PENDING_REMOTE"} for state in states),
            sum(state == "FAILED" for state in states),
            _hash_file(product_dir / "status.json"), _hash_file(product_dir / "input/source.json"), now(),
        ),
    )


def sync_publications_json(root: Path, product_dir: Path, publications: Optional[Mapping[str, Any]] = None) -> None:
    """Idempotently project one product's current JSON state into SQLite."""
    initialize(root)
    status = _read_json(product_dir / "status.json", {})
    data = dict(publications or _read_json(product_dir / "output/store-publications.json", {"stores": {}}))
    with database(root) as db:
        _upsert_product(db, product_dir, data, status)
        for store_id, record in (data.get("stores") or {}).items():
            store_status = _publication_status(record)
            selected = bool(record.get("selected"))
            db.execute(
                """INSERT INTO store_publications(product_id, store_id, selected, action, status,
                   payload_hash, api_write_count, write_retry_count, submission_version,
                   last_submitted_at, last_checked_at, last_error, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(product_id, store_id) DO UPDATE SET selected=excluded.selected,
                   action=excluded.action, status=excluded.status, payload_hash=excluded.payload_hash,
                   api_write_count=excluded.api_write_count, submission_version=excluded.submission_version,
                   last_submitted_at=excluded.last_submitted_at, last_checked_at=excluded.last_checked_at,
                   last_error=excluded.last_error, updated_at=excluded.updated_at""",
                (
                    product_dir.name, str(store_id), int(selected),
                    str((record.get("sku_publications") or [{}])[0].get("action") or "UNKNOWN").upper(),
                    store_status, record.get("payload_hash"), int(record.get("api_write_count") or 0),
                    int(record.get("write_retry_count") or 0), int(record.get("submission_version") or 0),
                    record.get("last_submitted_at"), record.get("last_checked_at"), record.get("last_error"), now(),
                ),
            )
            cursor = db.execute(
                "SELECT id FROM store_publications WHERE product_id=? AND store_id=?",
                (product_dir.name, str(store_id)),
            )
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                continue
            publication_id = row[0]
            for sku in record.get("sku_publications") or []:
                sku_id = str(sku.get("sku_id") or "unknown")
                sku_status = _sku_status(sku, store_status)
                db.execute(
                    """INSERT INTO store_sku_publications(publication_id, sku_id, offer_id, task_id,
                       ozon_product_id, status, error_code, error_message, payload_hash, last_checked_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(publication_id, sku_id) DO UPDATE SET offer_id=excluded.offer_id,
                       task_id=excluded.task_id, ozon_product_id=excluded.ozon_product_id,
                       status=excluded.status, error_code=excluded.error_code, error_message=excluded.error_message,
                       payload_hash=excluded.payload_hash, last_checked_at=excluded.last_checked_at,
                       updated_at=excluded.updated_at""",
                    (
                        publication_id, sku_id, sku.get("offer_id"), sku.get("task_id"), sku.get("ozon_product_id"),
                        sku_status, None, "; ".join(str(item) for item in sku.get("errors") or []) or None,
                        sku.get("payload_hash"), record.get("last_checked_at"), now(),
                    ),
                )
                task_id = sku.get("task_id")
                if not unknown(task_id):
                    task_status = sku_status
                    db.execute(
                        """INSERT INTO tasks(task_id, product_id, store_id, publication_id, task_type, status,
                           write_attempt_count, read_query_count, payload_hash, submitted_at, next_check_at,
                           last_checked_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(store_id, task_id, task_type) DO UPDATE SET status=excluded.status,
                           write_attempt_count=MAX(tasks.write_attempt_count, excluded.write_attempt_count),
                           payload_hash=COALESCE(excluded.payload_hash, tasks.payload_hash),
                           next_check_at=COALESCE(tasks.next_check_at, excluded.next_check_at),
                           last_checked_at=excluded.last_checked_at, updated_at=excluded.updated_at""",
                        (
                            str(task_id), product_dir.name, str(store_id), publication_id,
                            str(sku.get("action") or "CREATE").upper(), task_status,
                            int(record.get("api_write_count") or 0), 0, sku.get("payload_hash"),
                            record.get("last_submitted_at"),
                            None if task_status == "HANDED_OFF_TO_OZON" else record.get("next_check_at") or _after_seconds(60),
                            record.get("last_checked_at"), now(),
                        ),
                    )


def migrate_all(root: Path) -> Dict[str, Any]:
    """Idempotently import all current product JSON records."""
    initialize(root)
    migrated: List[str] = []
    errors: List[Dict[str, str]] = []
    products_dir = root / "products"
    for product_dir in sorted(products_dir.glob("P[0-9]*")):
        if not product_dir.is_dir():
            continue
        try:
            sync_publications_json(root, product_dir)
            migrated.append(product_dir.name)
        except Exception as exc:  # keep one malformed legacy product from blocking others
            errors.append({"product_id": product_dir.name, "error": str(exc)})
    report = {"schema_version": SCHEMA_VERSION, "migrated": migrated, "errors": errors, "pending_write_protection": True, "created_at": now()}
    report_path = root / "runtime/task-db-migration-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=report_path.parent, delete=False) as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(report_path)
    return report


def product_snapshot(root: Path, product_id: str) -> Dict[str, Any]:
    initialize(root)
    with database(root) as db:
        product = db.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
        stores = db.execute("SELECT * FROM store_publications WHERE product_id=? ORDER BY store_id", (product_id,)).fetchall()
        skus = db.execute(
            """SELECT s.* FROM store_sku_publications s JOIN store_publications p ON p.id=s.publication_id
               WHERE p.product_id=? ORDER BY p.store_id, s.sku_id""", (product_id,)
        ).fetchall()
    return {
        "product": dict(product) if product else None,
        "stores": [dict(item) for item in stores],
        "sku_publications": [dict(item) for item in skus],
    }


def due_pending_store_ids(root: Path, product_id: str) -> List[str]:
    """Return only pending stores whose next_check_at has arrived."""
    initialize(root)
    current = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with database(root) as db:
        rows = db.execute(
            """SELECT store_id, MIN(next_check_at) AS next_check_at, COUNT(*) AS task_count
               FROM tasks WHERE product_id=? AND status IN ('SUBMITTED','PENDING_REMOTE')
               GROUP BY store_id""",
            (product_id,),
        ).fetchall()
    # A pending publication without a task row is safe to inspect immediately;
    # it can never be used to issue a CREATE from this read-only worker.
    return [str(row["store_id"]) for row in rows if not row["next_check_at"] or str(row["next_check_at"]) <= current]


def record_remote_check(root: Path, product_id: str, store_ids: Optional[Iterable[str]] = None) -> None:
    """Record a read-only remote check and schedule the next backoff interval."""
    checked = now()
    allowed = {str(item) for item in store_ids} if store_ids is not None else None
    if allowed is not None and not allowed:
        return
    initialize(root)
    with database(root) as db:
        rows = db.execute(
            "SELECT id, store_id, read_query_count FROM tasks "
            "WHERE product_id=? AND status IN ('SUBMITTED','PENDING_REMOTE')",
            (product_id,),
        ).fetchall()
        for row in rows:
            if allowed is not None and str(row["store_id"]) not in allowed:
                continue
            new_count = int(row["read_query_count"] or 0) + 1
            db.execute(
                "UPDATE tasks SET read_query_count=?, last_checked_at=?, next_check_at=?, updated_at=? WHERE id=?",
                (new_count, checked, _after_seconds(remote_backoff_seconds(new_count)), checked, row["id"]),
            )
        if allowed is None:
            db.execute(
                "UPDATE store_publications SET last_checked_at=?, updated_at=? "
                "WHERE product_id=? AND status IN ('SUBMITTED','PENDING_REMOTE')",
                (checked, checked, product_id),
            )
        else:
            placeholders = ",".join("?" for _ in allowed) or "?"
            params = [checked, checked, product_id, *sorted(allowed)]
            db.execute(
                f"UPDATE store_publications SET last_checked_at=?, updated_at=? "
                f"WHERE product_id=? AND status IN ('SUBMITTED','PENDING_REMOTE') AND store_id IN ({placeholders})",
                params,
            )
    _ensure_private_database(root)


def publications_from_db(root: Path, product_dir: Path, store_ids: Iterable[str] = ()) -> Optional[Dict[str, Any]]:
    """Rebuild the publication view used by legacy callers from SQLite state."""
    if not database_path(root).is_file():
        return None
    initialize(root)
    legacy_path = product_dir / "output/store-publications.json"
    legacy = _read_json(legacy_path, {"schema_version": "1.0.0", "product_id": product_dir.name, "stores": {}})
    stores: Dict[str, Any] = dict(legacy.get("stores") or {})
    with database(root) as db:
        product = db.execute("SELECT * FROM products WHERE product_id=?", (product_dir.name,)).fetchone()
        if product is None:
            return None
        rows = db.execute("SELECT * FROM store_publications WHERE product_id=? ORDER BY store_id", (product_dir.name,)).fetchall()
        for row in rows:
            store_id = str(row["store_id"])
            record = dict(stores.get(store_id) or {"product_internal_id": product_dir.name, "store_id": store_id, "selected": False, "sku_publications": []})
            record.update({
                "selected": bool(row["selected"]),
                "status": {"CREATED": "SUCCESS", "SUBMITTED": "PENDING_REMOTE", "PENDING_REMOTE": "PENDING_REMOTE", "FAILED": "FAILED"}.get(row["status"], row["status"]),
                "api_write_count": row["api_write_count"],
                "submission_version": row["submission_version"],
                "last_submitted_at": row["last_submitted_at"],
                "last_checked_at": row["last_checked_at"],
                "last_error": row["last_error"],
            })
            sku_by_id = {str(item.get("sku_id")): dict(item) for item in record.get("sku_publications") or []}
            sku_rows = db.execute("SELECT * FROM store_sku_publications WHERE publication_id=? ORDER BY sku_id", (row["id"],)).fetchall()
            for sku_row in sku_rows:
                item = sku_by_id.setdefault(str(sku_row["sku_id"]), {"sku_id": str(sku_row["sku_id"]), "errors": [], "warnings": []})
                item.update({
                    "offer_id": sku_row["offer_id"] or "unknown",
                    "task_id": sku_row["task_id"] or "unknown",
                    "ozon_product_id": sku_row["ozon_product_id"] or "unknown",
                    "payload_hash": sku_row["payload_hash"] or "unknown",
                    "moderation_status": str(sku_row["status"] or "NOT_SUBMITTED").lower(),
                })
                if sku_row["error_message"]:
                    item["errors"] = [sku_row["error_message"]]
            record["sku_publications"] = list(sku_by_id.values())
            stores[store_id] = record
    for store_id in store_ids:
        stores.setdefault(str(store_id), {"product_internal_id": product_dir.name, "store_id": str(store_id), "selected": False, "status": "NOT_SELECTED", "sku_publications": []})
    return {"schema_version": "1.0.0", "product_id": product_dir.name, "updated_at": product["updated_at"], "stores": stores}


def cutover_to_sqlite(root: Path) -> Dict[str, Any]:
    """Make SQLite the sole mutable task-state source after migration tests."""
    report = migrate_all(root)
    backup_count = 0
    for path in (root / "products").glob("P[0-9]*/output/store-publications.json"):
        backup = path.with_name(path.name + ".readonly-backup")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        backup_count += 1
    marker = root / CUTOVER_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {"state": "sqlite_authoritative", "schema_version": SCHEMA_VERSION, "cutover_at": now(), "json_backups": backup_count, "migration": report}
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


ARCHIVE_REASON = "user_archived_in_ozon"
RECOVERY_DAYS = 7


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _extract_product_ids(value: Any) -> List[str]:
    found: List[str] = []
    if isinstance(value, Mapping):
        if isinstance(value.get("product_id"), str) and value["product_id"].startswith("P"):
            found.append(value["product_id"])
        for child in value.values():
            found.extend(_extract_product_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_extract_product_ids(child))
    return list(dict.fromkeys(found))


def archive_legacy_local_data(
    root: Path,
    product_ids: Optional[Iterable[str]] = None,
    reason: str = ARCHIVE_REASON,
) -> Dict[str, Any]:
    """Archive legacy local products/batches without touching Ozon.

    This is deliberately local-only: it never constructs an Ozon client and
    only changes the SQLite projection plus local recovery manifests. Full
    product directories remain in place for the seven-day recovery window.
    """
    initialize(root)
    selected = {str(item) for item in (product_ids or [])}
    if not selected:
        selected = {path.name for path in (root / "products").glob("P[0-9]*") if path.is_dir()}
    archived_at = now()
    cleanup_after = (datetime.now(timezone.utc).astimezone() + timedelta(days=RECOVERY_DAYS)).isoformat(timespec="seconds")
    recovery_root = root / "runtime" / "recovery-archive"
    recovery_root.mkdir(parents=True, exist_ok=True)
    product_manifests: List[str] = []
    batch_manifests: List[str] = []
    with database(root) as db:
        for product_id in sorted(selected):
            product_dir = root / "products" / product_id
            if not product_dir.is_dir():
                continue
            existing_archive = db.execute(
                "SELECT archived_at, cleanup_after, reason FROM archive_records WHERE entity_type='product' AND entity_id=?",
                (product_id,),
            ).fetchone()
            entity_archived_at = str(existing_archive["archived_at"]) if existing_archive else archived_at
            entity_cleanup_after = str(existing_archive["cleanup_after"]) if existing_archive else cleanup_after
            entity_reason = str(existing_archive["reason"]) if existing_archive else reason
            product = db.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
            stores = db.execute("SELECT * FROM store_publications WHERE product_id=? ORDER BY store_id", (product_id,)).fetchall()
            tasks = db.execute("SELECT * FROM tasks WHERE product_id=? ORDER BY store_id, task_id", (product_id,)).fetchall()
            sku_rows = db.execute(
                """SELECT s.* FROM store_sku_publications s JOIN store_publications p ON p.id=s.publication_id
                   WHERE p.product_id=? ORDER BY p.store_id, s.sku_id""", (product_id,)
            ).fetchall()
            summary = {
                "product_id": product_id,
                "previous_product": dict(product) if product else None,
                "stores": [dict(item) for item in stores],
                "tasks": [dict(item) for item in tasks],
                "sku_publications": [dict(item) for item in sku_rows],
                "preserved_fields": ["task_id", "offer_id", "ozon_product_id", "payload_hash", "write_attempt_count", "api_write_count"],
            }
            summary_json = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            summary_hash = hashlib.sha256(summary_json.encode("utf-8")).hexdigest()
            db.execute(
                """INSERT OR IGNORE INTO archive_records(entity_type, entity_id, archived_at, cleanup_after, reason,
                   summary_json, summary_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("product", product_id, entity_archived_at, entity_cleanup_after, entity_reason, summary_json, summary_hash, entity_archived_at),
            )
            db.execute("UPDATE products SET aggregate_status='ARCHIVED', updated_at=? WHERE product_id=?", (archived_at, product_id))
            db.execute("UPDATE tasks SET status='ABANDONED', next_check_at=NULL, terminal_reason=?, updated_at=? WHERE product_id=?", (reason, archived_at, product_id))
            db.execute("UPDATE store_publications SET status='ARCHIVED', last_error=?, updated_at=? WHERE product_id=?", (reason, archived_at, product_id))
            db.execute("UPDATE store_sku_publications SET status='ARCHIVED', updated_at=? WHERE publication_id IN (SELECT id FROM store_publications WHERE product_id=?)", (archived_at, product_id))
            manifest = {
                "entity_type": "product", "entity_id": product_id,
                "archived_at": entity_archived_at, "cleanup_after": entity_cleanup_after, "reason": entity_reason,
                "summary_sha256": summary_hash, "full_data_retained_at": str(product_dir),
            }
            _write_json_atomic(recovery_root / "products" / f"{product_id}.json", manifest)
            status_path = product_dir / "status.json"
            status = _read_json(status_path, {})
            previous_status = str(status.get("status") or "unknown")
            history = status.setdefault("history", [])
            if not any(str(item.get("to") or "") == "ARCHIVED" for item in history):
                history_previous = str(history[-1].get("to") or previous_status) if history else previous_status
                status.setdefault("history", []).append({
                    "from": history_previous,
                    "to": "ARCHIVED",
                    "at": entity_archived_at,
                    "reason": entity_reason,
                })
                status.setdefault("steps", []).append({
                    "name": "archived", "status": "completed", "started_at": entity_archived_at,
                    "finished_at": entity_archived_at, "retry_count": 0, "retryable": False, "error": None,
                })
            status.update({
                "status": "ARCHIVED", "local_lifecycle_status": "ARCHIVED",
                "current_step": "archived", "active_step": None, "next_action": "none",
                "archived_at": entity_archived_at, "cleanup_after": entity_cleanup_after, "archive_reason": entity_reason,
                "remote_status_polling": "disabled", "full_data_retained": True,
            })
            _write_json_atomic(status_path, status)
            product_manifests.append(product_id)

        for batch_path in sorted((root / "batches").glob("*/batch.json")):
            batch_id = batch_path.parent.name
            existing_archive = db.execute(
                "SELECT archived_at, cleanup_after, reason FROM archive_records WHERE entity_type='batch' AND entity_id=?",
                (batch_id,),
            ).fetchone()
            entity_archived_at = str(existing_archive["archived_at"]) if existing_archive else archived_at
            entity_cleanup_after = str(existing_archive["cleanup_after"]) if existing_archive else cleanup_after
            entity_reason = str(existing_archive["reason"]) if existing_archive else reason
            batch = _read_json(batch_path, {})
            product_refs = _extract_product_ids(batch)
            summary = {"batch_id": batch_id, "product_ids": product_refs, "previous_status": batch.get("status")}
            summary_json = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            summary_hash = hashlib.sha256(summary_json.encode("utf-8")).hexdigest()
            db.execute(
                """INSERT OR IGNORE INTO archive_records(entity_type, entity_id, archived_at, cleanup_after, reason,
                   summary_json, summary_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("batch", batch_id, entity_archived_at, entity_cleanup_after, entity_reason, summary_json, summary_hash, entity_archived_at),
            )
            db.execute(
                """INSERT INTO batches(batch_id, local_status, archived_at, cleanup_after, archive_reason, summary_json, updated_at)
                   VALUES (?, 'ARCHIVED', ?, ?, ?, ?, ?)
                   ON CONFLICT(batch_id) DO UPDATE SET local_status='ARCHIVED', archived_at=excluded.archived_at,
                   cleanup_after=excluded.cleanup_after, archive_reason=excluded.archive_reason,
                   summary_json=excluded.summary_json, updated_at=excluded.updated_at""",
                (batch_id, entity_archived_at, entity_cleanup_after, entity_reason, summary_json, entity_archived_at),
            )
            batch.update({"local_lifecycle_status": "ARCHIVED", "archived_at": entity_archived_at, "cleanup_after": entity_cleanup_after, "archive_reason": entity_reason})
            _write_json_atomic(batch_path, batch)
            _write_json_atomic(recovery_root / "batches" / f"{batch_id}.json", {
                "entity_type": "batch", "entity_id": batch_id, "archived_at": entity_archived_at,
                "cleanup_after": entity_cleanup_after, "reason": entity_reason, "summary_sha256": summary_hash,
                "full_data_retained_at": str(batch_path.parent),
            })
            batch_manifests.append(batch_id)
    _ensure_private_database(root)
    return {
        "archived_products": product_manifests,
        "archived_batches": batch_manifests,
        "archived_at": archived_at,
        "cleanup_after": cleanup_after,
        "reason": reason,
        "ozon_write_api_calls": 0,
        "ozon_read_api_calls": 0,
        "inventory_api_calls": 0,
    }
def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Initialize/migrate local SQLite task state")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--cutover", action="store_true", help="make SQLite the sole mutable task-state source")
    parser.add_argument("--archive-legacy", action="store_true", help="archive old local products and batches without remote calls")
    parser.add_argument("--product")
    args = parser.parse_args()
    if args.archive_legacy:
        print(json.dumps(archive_legacy_local_data(args.root), ensure_ascii=False, indent=2))
    elif args.cutover:
        print(json.dumps(cutover_to_sqlite(args.root), ensure_ascii=False, indent=2))
    elif args.migrate:
        print(json.dumps(migrate_all(args.root), ensure_ascii=False, indent=2))
    elif args.product:
        print(json.dumps(product_snapshot(args.root, args.product), ensure_ascii=False, indent=2))
    else:
        print(str(initialize(args.root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
