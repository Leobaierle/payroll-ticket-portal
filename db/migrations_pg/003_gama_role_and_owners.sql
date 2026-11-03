-- Nova empresa Gama: perfil, origem e "Gestao de empresas" (responsaveis da folha por origem)
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
  CHECK (role IN ('dp', 'company', 'beta', 'alfa', 'ambos', 'gama'));

ALTER TABLE user_invites DROP CONSTRAINT IF EXISTS user_invites_role_check;
ALTER TABLE user_invites ADD CONSTRAINT user_invites_role_check
  CHECK (role IN ('dp', 'company', 'beta', 'alfa', 'ambos', 'gama'));

INSERT INTO request_types (name, description)
SELECT 'Gama', 'Solicitacoes da folha enviadas pela empresa Gama para o Departamento Pessoal.'
WHERE NOT EXISTS (SELECT 1 FROM request_types WHERE name = 'Gama');

CREATE TABLE IF NOT EXISTS category_owners (
  category_id INTEGER NOT NULL REFERENCES request_types(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (category_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_category_owners_user ON category_owners(user_id);
