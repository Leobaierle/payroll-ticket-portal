PRAGMA foreign_keys = ON;

ALTER TABLE tickets ADD COLUMN admission_payload TEXT NOT NULL DEFAULT '';
ALTER TABLE tickets ADD COLUMN admission_search_text TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_tickets_admission_search ON tickets(admission_search_text);
