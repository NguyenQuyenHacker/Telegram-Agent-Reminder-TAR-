CREATE TABLE IF NOT EXISTS report (
    report_id   TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    "group"     TEXT,
    raw_text    TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_report_received_at ON report (received_at);
