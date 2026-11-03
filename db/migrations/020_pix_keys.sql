-- Chaves Pix dos funcionarios por origem (SQLite: testes/rollback). Ver migration PG 005.
CREATE TABLE IF NOT EXISTS pix_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL REFERENCES request_types(id),
  employee_name TEXT NOT NULL,
  name_key TEXT NOT NULL,
  pix_key TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (category_id, name_key)
);
CREATE INDEX IF NOT EXISTS idx_pix_keys_category ON pix_keys(category_id);

CREATE TABLE IF NOT EXISTS pix_key_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pix_key_id INTEGER NOT NULL REFERENCES pix_keys(id) ON DELETE CASCADE,
  old_key TEXT NOT NULL DEFAULT '',
  new_key TEXT NOT NULL DEFAULT '',
  changed_by_user_id INTEGER REFERENCES users(id),
  ticket_id INTEGER REFERENCES tickets(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pix_key_history_key ON pix_key_history(pix_key_id, id DESC);
