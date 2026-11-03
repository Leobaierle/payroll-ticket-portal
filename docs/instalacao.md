# Instalação

## Requisitos

- `Python 3.12+`
- `python3-psycopg`
- `python3-psycopg-pool` quando `DB_BACKEND=postgres`

## Passos

1. abrir a pasta do projeto
2. opcionalmente definir variáveis de ambiente ou criar um arquivo `.env` na raiz
3. iniciar o servidor:

```bash
python3 server.py
```

## Variáveis de ambiente

- `DB_BACKEND`: `sqlite` ou `postgres`
- `DATABASE_URL`: connection string PostgreSQL
- `PG_POOL_MIN`, `PG_POOL_MAX`: tamanho do pool PostgreSQL
- `PORT`: porta HTTP
- `APP_DB_PATH`: caminho do banco SQLite
- `SESSION_DAYS`: duração da sessão em dias
- `APP_ENV`: ambiente de execução
- `APP_BASE_URL`: URL base usada nos links públicos enviados por e-mail
- `APP_OUTBOX_PATH`: pasta de fallback para mensagens `.eml`
- `USE_SECURE_COOKIE`: ativa cookie `Secure`
- `LOGIN_RATE_LIMIT_ATTEMPTS`: limite de tentativas de login
- `LOGIN_RATE_LIMIT_WINDOW`: janela do rate limit
- `MAIL_FROM`: remetente das confirmações
- `MAIL_REPLY_TO`: reply-to opcional
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
- `SMTP_USE_TLS`, `SMTP_USE_SSL`

## Produção

Exemplo de configuração:

- `DB_BACKEND=postgres`
- `DATABASE_URL=postgresql://portaldp_app:<senha>@db.example.internal:5432/portaldp`
- `PORT=8001`
- `APP_BASE_URL=https://portal.example.internal`
- `APP_OUTBOX_PATH=/opt/portaldp/outbox`
- `APP_ATTACHMENTS_PATH=/opt/portaldp/attachments`
- `USE_SECURE_COOKIE=1`

O `.env` da raiz guarda só configurações não sensíveis. `DATABASE_URL` e `SMTP_PASSWORD` ficam num arquivo
fora do diretório da aplicação (`/etc/portaldp/portaldp.secrets.env`, dono `root:portaldp`, permissão `640`),
carregado pelo `systemd` (`deploy/portaldp.service`).

## O que acontece na inicialização

- o log local é configurado
- as migrações de banco são aplicadas antes do primeiro request
- sessões expiradas são removidas
- em modo `sqlite`, um backup inicial do banco é gerado
- o sistema prepara o envio de confirmações por e-mail e usa `outbox/` como fallback se necessário
- se existir `.env` na raiz, ele é carregado automaticamente antes da leitura das variáveis

Em ambientes que importam `server.py` sem executar `main()`, o schema continua sendo atualizado automaticamente no carregamento do módulo.
