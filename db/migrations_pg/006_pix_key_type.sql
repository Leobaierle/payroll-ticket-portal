-- Tipo da chave Pix (CPF, CNPJ, Telefone, E-mail, Chave aleatoria), informado/confirmado pelo Agente DP.
-- Vazio = ainda nao confirmado (carga da planilha): o portal sugere pela deteccao automatica.
ALTER TABLE pix_keys ADD COLUMN IF NOT EXISTS key_type TEXT NOT NULL DEFAULT '';
ALTER TABLE pix_key_history ADD COLUMN IF NOT EXISTS old_type TEXT NOT NULL DEFAULT '';
ALTER TABLE pix_key_history ADD COLUMN IF NOT EXISTS new_type TEXT NOT NULL DEFAULT '';
