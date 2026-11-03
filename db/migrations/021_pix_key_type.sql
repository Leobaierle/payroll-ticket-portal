-- Tipo da chave Pix (SQLite: testes/rollback). Ver migration PG 006.
ALTER TABLE pix_keys ADD COLUMN key_type TEXT NOT NULL DEFAULT '';
ALTER TABLE pix_key_history ADD COLUMN old_type TEXT NOT NULL DEFAULT '';
ALTER TABLE pix_key_history ADD COLUMN new_type TEXT NOT NULL DEFAULT '';
