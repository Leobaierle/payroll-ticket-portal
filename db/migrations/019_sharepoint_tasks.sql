-- Origens que geram tarefa no portal de tarefas do SharePoint (SQLite: testes/rollback). Ver migration PG 004.
ALTER TABLE ticket_attachments ADD COLUMN uploaded_by_user_id INTEGER REFERENCES users(id);
ALTER TABLE ticket_attachments ADD COLUMN client_visible INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS origin_sharepoint_config (
  category_id INTEGER PRIMARY KEY REFERENCES request_types(id) ON DELETE CASCADE,
  portal_cliente_id INTEGER NOT NULL,
  client_site_url TEXT NOT NULL,
  revisao_assignee TEXT NOT NULL,
  prazo_dias INTEGER NOT NULL DEFAULT 3,
  process_map TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sharepoint_task_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pendente',
  payload TEXT NOT NULL,
  attachment_ids TEXT NOT NULL DEFAULT '[]',
  task_id INTEGER,
  message TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_by_user_id INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sharepoint_task_requests_status ON sharepoint_task_requests(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_sharepoint_task_requests_ticket ON sharepoint_task_requests(ticket_id, id DESC);

INSERT OR IGNORE INTO origin_sharepoint_config (category_id, portal_cliente_id, client_site_url, revisao_assignee, prazo_dias, process_map)
SELECT id, 273, 'https://empresa.sharepoint.com/sites/Empresa/gama', '31', 3,
       '{"Admissão": 29, "Folha mensal": 9, "Férias": 33, "Rescisão": 31, "Processos trabalhistas": 28, "Outros": 28}'
FROM request_types WHERE name = 'Gama';
