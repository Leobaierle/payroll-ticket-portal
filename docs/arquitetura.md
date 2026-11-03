# Arquitetura

## Stack atual

- linguagem: `Python 3`
- servidor HTTP: `http.server`
- banco: `PostgreSQL` (SQLite mantido para testes e rollback)
- front-end: HTML renderizado pelo servidor + CSS local
- testes: `unittest`
- deploy: `nginx` na frente e `systemd` no processo principal

## Estrutura principal

- `server.py`: rotas HTTP, renderizacao e camada web
- `app_config.py`: configuração por variáveis de ambiente
- `app_db.py`: conexão, logging e migrações
- `app_services.py`: regras de negócio, autenticação, sessões e solicitações
- `app_form_ui.py`: builders do formulário público, dependentes e script dinâmico da tela de abertura
- `db/migrations/`: migrações versionadas do banco

## Modelo de funcionamento

O projeto roda como aplicação web local simples.

O servidor:

- garante schema atualizado ao carregar `server.py` e na inicialização normal
- publica as rotas HTML e CSS
- valida formularios públicos e internos
- autentica usuários do portal com cookie de sessão
- aplica CSRF, headers HTTP e rate limiting no login
- aplica autoajustes defensivos em partes do schema legado usadas por login, sessoes e solicitações

## Ponto de retomada

- o fluxo funcional está operacional em produção interna
- a retomada deve focar em ajuste fino de layout, refinamento de rotas e eventuais regras de negócio
- qualquer mudança estrutural deve continuar via migration ou ajuste explícito em `app_services.py`

## Areas do sistema

### Área pública

- expor consulta pública por token seguro
- concluir cadastro de usuários convidados por link

### Área autenticada

- exigir login para entrar no portal
- separar a solicitação entre `Alfa` e `Beta`
- restringir empresas permitidas por origem
- abrir solicitações da folha com perfil `DP` ou `Empresa`
- categorizar a solicitação por tipo operacional
- aceitar anexo opcional no envio
- gerar token público de acompanhamento
- autenticar usuários do Departamento Pessoal
- listar solicitações por abas
- filtrar por origem, responsável e busca textual
- alterar status
- assumir atendimento
- manter usuários internos
- baixar anexos enviados no painel do DP
