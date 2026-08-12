-- Toàn bộ schema của TAR, một file duy nhất.
--   psql "$DATABASE_URL" -f persistence/schema/schema.sql
--
-- Bảng checkpoint của LangGraph KHÔNG nằm ở đây: checkpointer.setup() tự tạo
-- lúc app khởi động.

CREATE TABLE IF NOT EXISTS task (
    task_id          TEXT PRIMARY KEY,
    code             TEXT UNIQUE,
    "group"          TEXT NOT NULL,
    content          TEXT NOT NULL,
    due_date         DATE,
    priority         TEXT NOT NULL CHECK (priority IN ('urgent', 'normal')),
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'done', 'cancelled')),
    next_remind_at   TIMESTAMPTZ NOT NULL,
    last_reminded_at TIMESTAMPTZ,
    done_at          TIMESTAMPTZ,
    cancelled_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_due_scan ON task (status, next_remind_at);

-- Bộ đếm mã việc theo nhóm: "App Trưởng thôn, trưởng bản" -> TB-001, TB-002, ...
CREATE TABLE IF NOT EXISTS group_code (
    "group"  TEXT PRIMARY KEY,
    prefix   TEXT NOT NULL UNIQUE,
    next_seq INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS report (
    report_id   TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    "group"     TEXT,
    raw_text    TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_report_received_at ON report (received_at);
