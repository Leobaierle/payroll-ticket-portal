# Banco do sistema

O projeto suporta dois backends:

- `postgres`: produção
- `sqlite`: dev, teste e rollback

Arquivos:

- `schema.sql`: representacao consolidada do schema atual
- `migrations/`: migracoes SQLite aplicadas automaticamente
- `migrations_pg/`: migracao inicial PostgreSQL
- `helpdesk.db`: banco local usado apenas com `DB_BACKEND=sqlite`
- `backups/`: backups operacionais
- `backups_test/`: backups de testes

Tabelas:

- `request_types`: categorias dos chamados
- `users`: usuarios internos do TI
- `sessions`: sessoes autenticadas do TI
- `login_attempts`: tentativas recentes de login por IP para rate limiting
- `tickets`: chamados abertos pelo formulario publico
- `ticket_events`: historico e comentarios dos chamados

Modelo atual:

- qualquer colaborador pode abrir chamado sem login
- apenas a equipe do TI acessa a area interna com usuario e senha
- cada chamado pode receber status, responsavel, resumo interno e respostas ao solicitante
- cada chamado possui token publico para acompanhamento sem login
