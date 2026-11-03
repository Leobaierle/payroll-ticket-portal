ALTER TABLE tickets ADD COLUMN created_by_user_id INTEGER REFERENCES users(id);

CREATE INDEX IF NOT EXISTS idx_tickets_created_by_user_id ON tickets(created_by_user_id);
