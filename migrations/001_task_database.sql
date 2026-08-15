-- Reference migration for runtime/task-db.sqlite3.
-- The executable migration is in scripts/task_database.py so deployment does
-- not require a separate sqlite CLI.  This file documents the durable schema.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
