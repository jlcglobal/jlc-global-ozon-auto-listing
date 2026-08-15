-- Local-only archive and recovery migration for runtime/task-db.sqlite3.
-- The executable migration remains in scripts/task_database.py; this file is
-- the reviewable schema record used by backups and future installers.
PRAGMA foreign_keys = ON;

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

-- Old JSON remains a read-only recovery backup after SQLite cutover.
