-- A returned Ozon task_id is the local terminal state.
-- The executable, idempotent migration is in scripts/task_database.py.
-- It changes no Ozon data and schedules no remote status requests.
PRAGMA foreign_keys = ON;

UPDATE store_sku_publications
SET status = 'HANDED_OFF_TO_OZON'
WHERE task_id IS NOT NULL
  AND TRIM(task_id) NOT IN ('', 'unknown', 'UNKNOWN', 'null', 'None')
  AND status IN ('SUBMITTED', 'PENDING_REMOTE', 'UPLOADING', 'QUEUED', 'OZON_MODERATION');

UPDATE tasks
SET status = 'HANDED_OFF_TO_OZON',
    next_check_at = NULL,
    terminal_reason = 'task_id_local_handoff'
WHERE task_id IS NOT NULL
  AND TRIM(task_id) NOT IN ('', 'unknown', 'UNKNOWN', 'null', 'None')
  AND status IN ('SUBMITTED', 'PENDING_REMOTE', 'UPLOADING', 'QUEUED', 'OZON_MODERATION');
