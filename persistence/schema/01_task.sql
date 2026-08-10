CREATE TABLE IF NOT EXISTS task (
    task_id             TEXT PRIMARY KEY,
    "group"             TEXT NOT NULL,
    content             TEXT NOT NULL,
    due_date            DATE,
    priority            TEXT NOT NULL CHECK (priority IN ('urgent', 'normal')),
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'snoozed')),
    remind_interval_min INTEGER NOT NULL,
    next_remind_at      TIMESTAMPTZ NOT NULL,
    last_reminded_at    TIMESTAMPTZ,
    done_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_due_scan ON task (status, next_remind_at);
