# Portal de solicitações da folha (DP)

Portal onde empresas clientes abrem solicitações para o Departamento Pessoal (admissão, rescisão, férias, folha
mensal...) e acompanham o atendimento. O DP trabalha por um painel interno. Python puro (`http.server`), sem
framework, com PostgreSQL.

> Projeto real, em produção, publicado aqui sem dados de clientes. As empresas aparecem como **Alfa**, **Beta** e
> **Gama**, e as pessoas e e-mails são fictícios.

## O que faz

- **Login obrigatório e isolamento por origem:** cada usuário externo pertence a uma empresa (origem) e só vê, busca
  e baixa o que é dela. Há perfis para quem atende mais de uma origem e para o Agente DP, que vê tudo.
- **Convites por e-mail:** o DP convida e a pessoa define a própria senha pelo link. Também há redefinição de senha,
  exclusão lógica de usuário e bloqueio para não excluir o último ativo.
- **Formulários por tipo de solicitação:** admissão (com dependentes e exportação em CSV no layout de importação do
  sistema de folha), rescisão, férias e outros. O título e a descrição são montados a partir dos campos.
- **Acompanhamento por link seguro:** o solicitante acompanha pelo link, e só responde estando logado. Chamado
  cancelado não aceita resposta.
- **Anexos que acumulam no histórico**, com upload em partes para passar do limite de corpo do proxy, e auditoria
  de arquivos órfãos (`scripts/attachments_audit.py`).
- **Gestão de chaves Pix dos funcionários:** busca com sugestão do funcionário do chamado, detecção do tipo da chave
  (CPF, CNPJ, telefone, e-mail, aleatória) só quando há certeza, e histórico de correções.
- **Integração com tarefas do SharePoint:** ao concluir, um worker externo cria a tarefa e anexa os arquivos pela
  API interna `/interno/sharepoint/*` (token Bearer, fila com reivindicação e reenvio).
- **Segurança:** rate limit de login persistido no banco, cookies `HttpOnly`/`Secure`, CSRF nos formulários
  internos, `X-Forwarded-For` só de proxy confiável.

## Stack

- Python 3.12, `http.server`, HTML renderizado no servidor + CSS próprio
- PostgreSQL em produção (`psycopg` + pool); SQLite para desenvolvimento e testes
- Migrações versionadas aplicadas no startup (`db/migrations/`, `db/migrations_pg/`)
- `unittest`: 121 testes de ponta a ponta (HTTP + banco)
- Deploy com `systemd` + `nginx` (`deploy/`)

## Rodando

```bash
cp .env.example .env
python3 server.py                 # http://localhost:8001 (SQLite por padrão)
# primeiro acesso interno: /dp/setup
```

Testes:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Estrutura

| Arquivo | Papel |
|---|---|
| `server.py` | Rotas HTTP e renderização das telas |
| `app_services.py` | Regras de negócio, autenticação, sessões, solicitações, Pix |
| `app_form_ui.py` | Formulário público (dependentes, campos dinâmicos) |
| `app_db.py` | Conexão SQLite/Postgres, logging e migrações |
| `app_config.py` | Configuração por variáveis de ambiente |
| `scripts/` | Healthcheck, auditoria de anexos, checagem de permissões, backup, importação de chaves Pix |
| `docs/` | Arquitetura, banco, rotas, instalação e testes |
