# Rotas

## Públicas

### `GET /`

Entrada autenticada do portal.

Comportamento atual:

- redireciona para login quando não há sessão
- exibe os cards `Alfa` e `Beta`
- cada card abre o formulário dedicado em nova aba para usuários logados

### `GET /convites/{invite_token}`

Tela para o convidado definir sua senha.

### `POST /convites/{invite_token}`

Conclui o cadastro do usuário convidado.

### `GET /novo-chamado`

Página autenticada dedicada ao formulário de abertura.

Parâmetros suportados:

- `category_id`

### `POST /abrir-chamado`

Recebe o formulário público, valida os campos e grava a solicitação no banco.

Campos principais:

- `requester_name`
- `category_id`
- `company_name`
- `request_category` com opções `Admissão`, `Folha mensal`, `Férias`, `Rescisão`, `Processos trabalhistas` e `Outros`
- `title` (não existe em `Rescisão`: o título é gerado como `Rescisão - <nome ou tipo>`)
- `description`
- `attachment`
- `csrf_token`
- só em `Rescisão`: `rescission_type` e `rescission_date` (obrigatórios), `rescission_registration`, `rescission_employee_name` e as linhas de evento `rescission_event_<n>_name` / `rescission_event_<n>_value` (opcionais); entram no texto da descrição, sem coluna própria

Comportamento especial:

- quando `request_category=Admissão`, a tela substitui `title` e `description` por um formulário específico do funcionário
- todos os campos marcados com `*` nesse formulário são obrigatórios
- o backend gera automaticamente `title` e `description` a partir dos dados preenchidos na admissão

### `GET /acompanhar/{public_token}`

Consulta pública da solicitação por token seguro.

Exibe:

- status
- origem
- empresa
- categoria funcional
- abertura
- ultima atualização
- histórico público de andamento
- anexos com link de download

### `GET /anexos/{attachment_id}`

Entrega o anexo de uma solicitação.

Comportamento atual:

- Agente DP baixa qualquer anexo
- usuário logado de outro perfil só baixa anexo de solicitação da **própria origem** (Beta só Beta, Alfa só Alfa, Ambos Beta+Alfa; Gama só as próprias solicitações); de outra origem recebe 404
- acessível pela consulta pública quando acompanhado do token da solicitação, mesmo sem login
- anexos ocultos (enviados só pela tarefa do SharePoint) só o Agente DP baixa
- responde com download do arquivo original

## Internas

### `GET /dp/login`

Tela de login da equipe do Departamento Pessoal.

### `POST /dp/login`

Autentica o usuário interno.

### `POST /dp/logout`

Encerra a sessão do usuário interno.

### `GET /dp/setup`

Tela para criar o primeiro acesso do DP.

### `POST /dp/setup`

Cria o primeiro usuário interno.

### `GET /dp`

Painel interno do DP.

Parâmetros suportados:

- `tab=abertos|andamento|concluídos|todos`
- `category_id`
- `assigned_to`
- `folha_owner` (Responsavel folha: origens atribuidas ao agente em `/dp/empresas`)
- `q`
- `sort=recentes|antigos`

### `POST /dp/chamados/{id}/atualizar`

Atualiza uma solicitação pelo painel interno.

Campos extras para enviar a chave Pix (so Beta/Alfa em Folha mensal, Férias e Rescisão; ver `docs/operacao.md`):

- `pix_send=1`: liga o envio
- por funcionario, com indice `N` (0..49): `pix_id_N` (id em `pix_keys`, `0` para funcionario novo), `pix_name_N` (so usado quando `pix_id_N=0`), `pix_key_N` (obrigatoria) e `pix_type_N` (obrigatorio: `CPF`, `CNPJ`, `Telefone`, `E-mail` ou `Chave aleatória`)

### `GET /dp/pix/buscar`

Busca de funcionarios para o envio de chave Pix (somente Agente DP). Parametros: `category_id` (origem Beta ou Alfa; outras dao 404) e `q` (nome, sem acento/caixa, todas as palavras). Responde JSON `{"results": [{"id", "name", "key", "type"}]}` (ate 15; `type` = tipo confirmado ou, se ainda nao houver, o sugerido pela deteccao, vazio se incerto), com `Cache-Control: no-store`.

### `GET /dp/pix`

Gestao de Pix (somente Agente DP): lista as chaves das origens Beta e Alfa. Parametros: `category_id` e `q` (nome, sem acento/caixa).

### `POST /dp/pix/adicionar`

Cadastra funcionario: `name`, `key` e `origin_id` (Beta ou Alfa). O tipo vem de `detect_pix_key_type`; se incerto, fica vazio.

### `POST /dp/pix/{id}/atualizar`

Altera `name`, `key` e `key_type`. Chave so pode ficar vazia em cadastro que ja estava sem chave.

### `POST /dp/pix/{id}/excluir`

Exclui o cadastro e o seu historico em `pix_key_history`.

### API interna do worker do SharePoint (`/interno/sharepoint/...`)

Somente com `Authorization: Bearer <PORTALDP_INTERNAL_TOKEN>` e fora do proxy público; caso contrario responde 404.

- `GET /interno/sharepoint/pendentes` — pedidos `pendente` (ou `enviando` parado ha mais de 15 min)
- `POST /interno/sharepoint/{id}/iniciar` — reivindica o pedido (409 se ja reivindicado)
- `GET /interno/sharepoint/{id}/anexos/{attachment_id}` — bytes de um anexo do pedido
- `POST /interno/sharepoint/{id}/resultado` — JSON `{status: enviando|criada|erro|aviso, task_id, message}`

### `POST /dp/chamados/{id}/sharepoint/reenviar`

Recoloca o pedido do chamado na fila quando esta com `erro` ou `aviso` (Agente DP).

### `GET /dp/empresas`

Gestao de empresas (somente Agente DP). Lista cada origem com as empresas e os Agentes DP ativos; o DP escolhe (select) o unico responsavel pela folha da origem.

### `POST /dp/empresas/{category_id}/responsaveis`

Define o unico responsavel da origem (campo `owner` = id do usuario; vazio remove). Recusa quem nao e Agente DP ativo.

### `GET /dp/usuarios`

Tela para cadastrar e visualizar usuários internos do DP.

Comportamento atual:

- separa visualmente o envio de convites da manutenção dos acessos já cadastrados
- o bloco `Adicionar agente` começa fechado e reabre automaticamente quando houver erro de validação
- exibe instruções curtas para os perfis `Agente DP`, `Beta`, `Alfa` e `Ambos`

### `POST /dp/usuarios/convites`

Envia convite por e-mail para um novo usuário `Agente DP`, `Beta`, `Alfa` ou `Ambos`.

Observação: as rotas antigas em `/ti` continuam aceitas por compatibilidade, mas a interface oficial aponta para `/dp`.
