# Banco de Dados

## Arquivos

- `db/schema.sql`: representacao consolidada do schema atual
- `db/migrations/`: migrações SQLite aplicadas na inicialização
- `db/migrations_pg/`: migração estrutural do PostgreSQL
- `db/helpdesk.db`: banco local de fallback / rollback
- `db/backups/`: backups operacionais
- `db/backups_test/`: backups de ambiente de teste

## Tabelas principais

### `request_types`

Origens do portal.

Valores atuais:

- `Alfa`
- `Beta`

### `tickets`

Tabela central dos chamados.

Campo `request_category`:

- `Admissão`
- `Folha mensal`
- `Férias`
- `Rescisão`
- `Processos trabalhistas`
- `Outros`

Campos principais:

- dados do solicitante
- origem
- empresa
- categoria funcional
- título
- descrição
- `admission_payload` estruturado para `Admissão` e `Férias`
- `admission_search_text` para busca operacional
- prioridade
- status
- responsável do DP
- resumo interno
- token público de acompanhamento
- `created_by_user_id`
- `created_at`
- `updated_at`

### `ticket_events`

Histórico das movimentações do chamado.

Campos principais:

- chamado
- usuário autor
- tipo de evento
- mensagem
- status anterior
- novo status
- data do evento

### `ticket_attachments`

Arquivos anexados pelo solicitante na abertura da solicitação.

Campos principais:

- solicitação vinculada
- nome original do arquivo
- nome interno armazenado no disco
- content type
- tamanho em bytes
- data de criação

### `origin_sharepoint_config`

Origens que geram tarefa no portal de tarefas ao concluir (Gama): `category_id`, `portal_cliente_id`, `client_site_url`, `revisao_assignee`, `prazo_dias`, `process_map` (JSON), `enabled`.

### `sharepoint_task_requests`

Fila de pedidos de tarefa: `ticket_id`, `status` (`pendente`/`enviando`/`criada`/`erro`/`aviso`), `payload` (JSON com processo, cliente, competencia, prazo, descricao), `attachment_ids` (JSON), `task_id`, `message`, `attempts`.

### `pix_keys`

Chaves Pix dos funcionarios, **por origem** (`category_id` = Beta ou Alfa): `employee_name` (como cadastrado, maiusculo), `name_key` (sem acento, maiusculo, espacos colapsados; unico por origem), `pix_key` (texto livre, pode ser vazio), `key_type` (CPF/CNPJ/Telefone/E-mail/Chave aleatória confirmado pelo DP; vazio = ainda nao confirmado), `created_at`, `updated_at`. Os dados nao ficam no repo: entram por `scripts/import_pix.py` e pelo proprio portal (ver `docs/operacao.md`). Migrations `005_pix_keys.sql` + `006_pix_key_type.sql` (PG) e `020_pix_keys.sql` + `021_pix_key_type.sql` (SQLite).

### `pix_key_history`

Cada cadastro ou alteracao de chave/tipo: `pix_key_id`, `old_key`, `new_key`, `old_type`, `new_type`, `changed_by_user_id`, `ticket_id` (chamado em que foi feita), `created_at`.

### `ticket_attachments` (colunas novas)

`uploaded_by_user_id` (quem enviou) e `client_visible` (0 = enviado so pela tarefa do SharePoint, oculto ao cliente).

### `user_invites`

Convites pendentes para ativação de usuários do portal.

Campos principais:

- usuário alvo
- e-mail
- perfil
- token de convite
- usuário que convidou
- expiração
- data de aceite

## Índices aplicados

- status + data de criação do chamado
- categoria do chamado
- responsável do chamado
- data de atualização do chamado
- token público do chamado
- token e expiração da sessão
- histórico por chamado
- anexos por chamado
- e-mail + status do usuário

## Migrações

O projeto aplica migrações versionadas automaticamente antes do primeiro request.

Fluxo atual:

1. cria a tabela `schema_migrations` se necessário
2. identifica migrações ainda não aplicadas
3. executa os arquivos SQL do backend ativo
4. registra a versão aplicada

Backends:

- `sqlite`: usa `db/migrations/`
- `postgres`: usa `db/migrations_pg/`

Mesmo em ambientes que apenas importam `server.py`, o carregamento do módulo garante que migrations pendentes sejam aplicadas.

No backend `sqlite`, o fluxo principal também contém autoajustes defensivos para bancos legados sem algumas tabelas ou colunas ainda exigidas por login, sessões, solicitações e anexos.

## Admissão e férias estruturadas

- `Admissão` e `Férias` já não dependem apenas do parse textual de `description`
- o payload estruturado é salvo em `tickets.admission_payload`
- a busca operacional usa `tickets.admission_search_text`
- a exportação CSV de admissões usa esse payload estruturado

## Auditoria

As datas `updated_at` são atualizadas pela camada de aplicação quando um usuário ou chamado sofre alteração relevante.

## Limpeza de legado

O estado atual do banco não inclui mais:

- a tabela `activities`, que não fazia parte do fluxo oficial servido por `server.py`
- a coluna `requester_phone`, removida do fluxo público e agora retirada também do schema consolidado
