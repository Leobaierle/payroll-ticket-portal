# Testes

## Suíte automatizada

Executar:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Checks operacionais:

```bash
python3 scripts/healthcheck.py
python3 scripts/check_runtime_permissions.py
python3 scripts/attachments_audit.py
```

## Cobertura atual

- abertura de chamado
- setup inicial e autenticação
- criação e validação de sessão
- importação do servidor com banco legado e migrations pendentes
- autoajuste de schema legado em login, sessão e envio de solicitação
- atualização de chamado
- criação, convite e edição de usuários
- criação de solicitação com categoria funcional
- criação de solicitação com anexo
- payload estruturado de admissão e férias
- exportação CSV de admissão
- limpeza de campos condicionais
- notificações por e-mail
- backup consistente do SQLite
- comportamento do backend PostgreSQL por mocks focados em schema, `lastrowid` e `make_backup()`
- validação do schema consolidado
- rotas HTTP principais, incluindo formulário público e respostas 404/405

## Estrategia

Os testes usam banco temporário por variável `APP_DB_PATH`, aplicam as migrações e validam:

- fluxos principais na camada de serviços
- rotas HTTP reais com servidor em porta efêmera
- comportamento de cookies e proteções básicas de formulários

No estado atual, a suíte cobre dezenas de cenários automatizados entre serviço, banco e rotas HTTP.

Para validação visual e manual, usar `docs/validacao-manual.md`.
