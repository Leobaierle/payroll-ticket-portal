PRAGMA foreign_keys = ON;

INSERT OR IGNORE INTO request_types (id, name, description) VALUES
  (3, 'Processos trabalhistas', 'Solicitacoes relacionadas a processos e demandas trabalhistas.'),
  (4, 'Outros', 'Solicitacoes que nao se encaixam nas demais categorias.');
