from __future__ import annotations

import hmac
import html
import json
import os
import sqlite3
import traceback
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from app_form_ui import render_admission_fields, render_public_form_script, render_rescission_events_section, render_select_options, render_vacation_fields
from app_config import APP_ENV, BASE_DIR, CSRF_COOKIE, DB_BACKEND, PORT, SESSION_COOKIE, SMTP_HOST, USE_SECURE_COOKIE
from app_db import configure_logging, ensure_schema, get_db, logger
from app_services import (
    ADMISSION_FIELD_SPECS,
    DEPENDENT_FIELD_SPECS,
    REQUEST_CATEGORY_OPTIONS,
    RESCISSION_NAME_MAX,
    EMPLOYEE_REGISTRATION_MAX,
    RESCISSION_TYPE_OPTIONS,
    VALID_TICKET_STATUSES,
    accept_user_invite,
    add_requester_comment,
    admission_payload_from_ticket_row,
    admission_summary_text,
    authenticate_user,
    build_cookie,
    build_admission_csv_export,
    cleanup_expired_sessions,
    company_options_for_category,
    create_user_invite,
    create_password_reset_request,
    create_session,
    create_ticket,
    create_user,
    delete_session,
    delete_ticket,
    field_error,
    format_datetime,
    get_public_dashboard_snapshot,
    get_ticket_attachment,
    generate_csrf_token,
    get_dashboard_data,
    get_user_invite,
    get_password_reset,
    get_public_ticket,
    get_public_ticket_events,
    get_public_ticket_token_by_id,
    get_session_user,
    get_ticket_filters,
    get_user_ticket_by_id,
    has_users,
    input_value,
    is_dp_user,
    is_login_rate_limited,
    list_categories,
    list_ticket_attachments,
    list_ti_users,
    make_backup,
    normalize_tab,
    parse_client_ip,
    register_login_attempt,
    reset_user_password,
    send_password_reset_email,
    send_user_invite_email,
    send_ticket_confirmation,
    send_ticket_opened_notification,
    update_ticket,
    update_user,
    ticket_cc_options,
    list_category_owners,
    can_download_attachment,
    user_ticket_category_scope,
    PIX_KEY_MAX,
    PIX_KEY_TYPES,
    PIX_MAX_ENTRIES,
    PIX_NAME_MAX,
    PIX_ORIGIN_NAMES,
    create_pix_key,
    delete_pix_key,
    detect_pix_key_type,
    list_pix_keys,
    pix_enabled_for,
    pix_origin_categories,
    pix_suggestion_for_ticket,
    search_pix_keys,
    update_pix_key,
    claim_sharepoint_request,
    finish_sharepoint_request,
    get_sharepoint_request_for_ticket,
    list_pending_sharepoint_requests,
    requeue_sharepoint_request,
    sharepoint_configured_category_ids,
    sharepoint_request_has_attachment,
    set_category_owner,
    ticket_status_label,
    user_allowed_category_names,
    user_role_label,
    vacation_payload_from_ticket_row,
    vacation_summary_text,
    VACATION_FIELD_SPECS,
)

# Some deployments import this module directly instead of executing server.py.
# Ensure the database schema is up to date before the first request is handled.
configure_logging()
ensure_schema()

try:
    STYLES_CSS_VERSION = int((BASE_DIR / "styles.css").stat().st_mtime)
except OSError:
    STYLES_CSS_VERSION = 0


RICH_TEXT_EVENT_TYPES = {"comentario", "solicitante"}


def safe_next_path(raw: str) -> str:
    """Aceita apenas caminhos locais absolutos como destino de redirecionamento pós-login."""
    raw = (raw or "").strip()
    if not raw.startswith("/"):
        return ""
    if raw.startswith("//") or raw.startswith("/\\"):
        return ""
    return raw


def render_event_message(row: sqlite3.Row | dict[str, str]) -> str:
    # comentario/solicitante messages are sanitized (allow-listed HTML) at write time in
    # app_services.sanitize_rich_text, so they render as trusted markup here. Every other
    # event type is built from internal labels and must stay escaped as plain text.
    if row["event_type"] in RICH_TEXT_EVENT_TYPES:
        return row["message"]
    return html.escape(row["message"])


def rich_text_editor(name: str, placeholder: str, rows_hint: int = 4, label_id: str = "") -> str:
    min_height = 44 + rows_hint * 22
    label_attr = f' aria-labelledby="{html.escape(label_id, quote=True)}"' if label_id else ""
    return f"""
    <div class="rte-wrapper">
      <div class="rte-toolbar" role="toolbar" aria-label="Formatação de texto">
        <button type="button" class="rte-btn" data-rte-cmd="bold" title="Negrito" aria-label="Negrito"><strong>N</strong></button>
        <button type="button" class="rte-btn" data-rte-cmd="italic" title="Itálico" aria-label="Itálico"><em>I</em></button>
        <button type="button" class="rte-btn" data-rte-cmd="underline" title="Sublinhado" aria-label="Sublinhado"><u>S</u></button>
      </div>
      <div
        class="rte-editor"
        contenteditable="true"
        data-placeholder="{html.escape(placeholder, quote=True)}"
        style="min-height:{min_height}px"
        role="textbox"
        aria-multiline="true"{label_attr}
      ></div>
      <textarea name="{html.escape(name, quote=True)}" class="rte-source" hidden></textarea>
    </div>
    """


def flash(kind: str, message: str) -> str:
    if not message:
        return ""
    role = "alert" if kind == "error" else "status"
    return f'<p class="flash flash--{kind}" role="{role}" aria-live="polite">{html.escape(message)}</p>'


def brand_logo() -> str:
    logo_path = BASE_DIR / "assets" / "logo-flat.png"
    if not logo_path.exists():
        return """
        <span class="brand-lockup">
          <span class="brand-lockup__text">
            <strong>Empresa</strong>
            <span>Departamento pessoal</span>
          </span>
        </span>
        """
    return """
    <span class="brand-lockup">
      <img class="brand-lockup__logo" src="/assets/logo-flat.png" alt="Logo">
      <span class="brand-lockup__text">
        <strong>Empresa</strong>
        <span>Departamento pessoal</span>
      </span>
    </span>
    """


def hero_mark_html() -> str:
    logo_path = BASE_DIR / "assets" / "logo.png"
    if not logo_path.exists():
        return ""
    return '<img class="hero__mark hero__mark--logo" src="/assets/logo.png" alt="Logo">'


def user_ticket_search_bar(message: str = "", kind: str = "info", q: str = "") -> str:
    return f"""
    <section class="request-search" aria-label="Consulta de solicitações">
      <form class="request-search__form" method="get" action="/buscar-solicitacao">
        <a class="button button--ghost" href="/minhas-solicitacoes">Todas solicitações</a>
        <label class="request-search__field">
          <span>Busca</span>
          <input name="q" type="text" autocomplete="off" value="{html.escape(q)}" placeholder="Nome, título ou número">
        </label>
        <button class="button button--primary" type="submit">Pesquisar</button>
      </form>
      {flash(kind, message)}
    </section>
    """


def row_value(row: sqlite3.Row | dict[str, str], key: str, default: str = "") -> str:
    if isinstance(row, sqlite3.Row):
        return str(row[key]) if key in row.keys() and row[key] is not None else default
    value = row.get(key, default)
    return str(value) if value is not None else default


def render_admission_ticket_summary(ticket: sqlite3.Row | dict[str, str]) -> str:
    if row_value(ticket, "request_category") == "Férias":
        payload = vacation_payload_from_ticket_row(ticket)
        if not payload:
            return f'<p class="ticket-description">{html.escape(row_value(ticket, "description"))}</p>'
        summary = vacation_summary_text(payload) or row_value(ticket, "description")
        return f'<p class="ticket-description">{html.escape(summary)}</p>'
    payload = admission_payload_from_ticket_row(ticket)
    if not payload:
        return f'<p class="ticket-description">{html.escape(row_value(ticket, "description"))}</p>'
    summary = admission_summary_text(payload) or row_value(ticket, "description")
    return f'<p class="ticket-description">{html.escape(summary)}</p>'


def render_admission_ticket_details(ticket: sqlite3.Row | dict[str, str]) -> str:
    if row_value(ticket, "request_category") == "Férias":
        payload = vacation_payload_from_ticket_row(ticket)
        if not payload:
            return ""
        vacation = payload.get("vacation") or {}
        items = "".join(
            f'<div><strong>{html.escape(field["label"])}</strong><span>{html.escape(str(vacation.get(field["name"], "") or "-"))}</span></div>'
            for field in VACATION_FIELD_SPECS
            if str(vacation.get(field["name"], "")).strip()
        )
        return f"""
        <section class="ticket-structured">
          <div class="ticket-structured__block">
            <h4>Dados das férias</h4>
            <div class="ticket-grid ticket-grid--public">{items}</div>
          </div>
        </section>
        """
    payload = admission_payload_from_ticket_row(ticket)
    if not payload:
        return ""
    employee = payload.get("employee") or {}
    employee_items = "".join(
        f'<div><strong>{html.escape(field["label"])}</strong><span>{html.escape(str(employee.get(field["name"], "") or "-"))}</span></div>'
        for field in ADMISSION_FIELD_SPECS
        if str(employee.get(field["name"], "")).strip()
    )
    dependent_sections: list[str] = []
    for index, dependent in enumerate(payload.get("dependents") or [], start=1):
        items = "".join(
            f'<div><strong>{html.escape(field["label"])}</strong><span>{html.escape(str(dependent.get(field["name"], "") or "-"))}</span></div>'
            for field in DEPENDENT_FIELD_SPECS
            if str(dependent.get(field["name"], "")).strip()
        )
        dependent_sections.append(
            f"""
            <section class="ticket-structured__block">
              <h4>Dependente {index}</h4>
              <div class="ticket-grid ticket-grid--public">{items}</div>
            </section>
            """
        )
    raw_description = row_value(ticket, "description").strip()
    raw_block = ""
    if raw_description:
        raw_block = f"""
        <details class="ticket-structured__raw">
          <summary>Ver texto completo gravado no chamado</summary>
          <p class="ticket-description">{html.escape(raw_description)}</p>
        </details>
        """
    return f"""
    <section class="ticket-structured">
      <div class="ticket-structured__block">
        <h4>Dados do funcionário</h4>
        <div class="ticket-grid ticket-grid--public">{employee_items}</div>
      </div>
      {''.join(dependent_sections)}
      {raw_block}
    </section>
    """

def html_page(title: str, content: str, user: sqlite3.Row | None = None, csrf_token: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700;800&family=Open+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/styles.css?v={STYLES_CSS_VERSION}">
</head>
<body>
  <a class="skip-link" href="#conteudo">Pular para o conteúdo</a>
  <header class="site-topbar">
    <div class="site-topbar__inner">
      <a class="button button--ghost" href="/">Início</a>
    </div>
  </header>
  <div class="site-shell">
    <div id="conteudo">{content}</div>
  </div>
  <script>
  (function () {{
    function closest(el, selector) {{
      return el && el.closest ? el.closest(selector) : null;
    }}

    document.addEventListener('mousedown', function (e) {{
      var btn = closest(e.target, '.rte-btn');
      if (btn) {{
        e.preventDefault();
      }}
    }});

    document.addEventListener('click', function (e) {{
      var btn = closest(e.target, '.rte-btn');
      if (!btn) return;
      document.execCommand(btn.dataset.rteCmd);
      updateRteToolbarState();
    }});

    function updateRteToolbarState() {{
      var sel = window.getSelection();
      var node = sel && sel.rangeCount > 0 ? sel.getRangeAt(0).startContainer : null;
      var startEl = node && node.nodeType === 3 ? node.parentElement : node;
      var editor = closest(startEl, '.rte-editor');
      document.querySelectorAll('.rte-btn[data-rte-cmd]').forEach(function (btn) {{
        var active = false;
        if (editor) {{
          try {{ active = document.queryCommandState(btn.dataset.rteCmd); }} catch (err) {{}}
        }}
        btn.classList.toggle('is-active', active);
      }});
    }}

    document.addEventListener('selectionchange', updateRteToolbarState);
    document.addEventListener('keyup', function (e) {{
      if (closest(e.target, '.rte-editor')) updateRteToolbarState();
    }});

    document.addEventListener('submit', function (e) {{
      var form = e.target;
      if (!form || !form.querySelectorAll) return;
      form.querySelectorAll('.rte-wrapper').forEach(function (wrapper) {{
        var editor = wrapper.querySelector('.rte-editor');
        var source = wrapper.querySelector('.rte-source');
        if (editor && source) {{
          source.value = editor.innerHTML.trim();
        }}
      }});
    }}, true);

    function fmtFileSize(n) {{
      if (n < 1024) return n + ' B';
      if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
      return (n / 1048576).toFixed(1) + ' MB';
    }}

    function fileKey(f) {{
      return f.name + '|' + f.size + '|' + (f.lastModified || 0);
    }}

    function syncInputFiles(input) {{
      try {{
        var dt = new DataTransfer();
        (input._accFiles || []).forEach(function (f) {{ dt.items.add(f); }});
        input.files = dt.files;
        input._accSupported = true;
      }} catch (err) {{
        input._accSupported = false;
      }}
    }}

    function renderFileList(input) {{
      var wrap = closest(input, 'label') || input.parentElement;
      var list = wrap.querySelector('.file-list-preview');
      var files = input._accFiles || [];
      if (!files.length) {{
        if (list) list.parentNode.removeChild(list);
        return;
      }}
      if (!list) {{
        list = document.createElement('ul');
        list.className = 'file-list-preview';
        wrap.appendChild(list);
      }}
      list.innerHTML = '';
      files.forEach(function (f, idx) {{
        var li = document.createElement('li');
        li.className = 'file-list-preview__item';
        var name = document.createElement('span');
        name.className = 'file-list-preview__name';
        name.textContent = f.name + ' (' + fmtFileSize(f.size) + ')';
        var rm = document.createElement('button');
        rm.type = 'button';
        rm.className = 'file-list-preview__remove';
        rm.setAttribute('aria-label', 'Remover ' + f.name);
        rm.dataset.idx = idx;
        rm.textContent = '\\u00d7';
        li.appendChild(name);
        li.appendChild(rm);
        list.appendChild(li);
      }});
    }}

    document.addEventListener('change', function (e) {{
      var input = e.target;
      if (!input.matches || !input.matches('input[type="file"][name="attachment"]')) return;
      if (!input._accFiles) input._accFiles = [];
      var seen = {{}};
      input._accFiles.forEach(function (f) {{ seen[fileKey(f)] = true; }});
      Array.prototype.forEach.call(input.files, function (f) {{
        var k = fileKey(f);
        if (!seen[k]) {{ seen[k] = true; input._accFiles.push(f); }}
      }});
      syncInputFiles(input);
      if (input._accSupported === false) {{
        input._accFiles = Array.prototype.slice.call(input.files);
      }}
      renderFileList(input);
    }});

    document.addEventListener('click', function (e) {{
      var rm = closest(e.target, '.file-list-preview__remove');
      if (!rm) return;
      var wrap = closest(rm, 'label') || rm.parentNode.parentNode.parentNode;
      var input = wrap && wrap.querySelector('input[type="file"][name="attachment"]');
      if (!input || !input._accFiles) return;
      input._accFiles.splice(parseInt(rm.dataset.idx, 10), 1);
      syncInputFiles(input);
      renderFileList(input);
    }});
  }})();
  </script>
</body>
</html>"""


def error_page(title: str, message: str, user: sqlite3.Row | None = None, csrf_token: str = "") -> str:
    return html_page(
        title,
        f"""
        <main class="content-flow">
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Erro</p>
                <h1>{html.escape(title)}</h1>
              </div>
            </div>
            {flash("error", message)}
          </section>
        </main>
        """,
        user,
        csrf_token,
    )


def ticket_badge(status: str) -> str:
    mapping = {
        "Aberto": "tag--amber",
        "Em analise": "tag--blue",
        "Em atendimento": "tag--green",
        "Aguardando retorno": "tag--slate",
        "Concluido": "tag--green",
        "Cancelado": "tag--slate",
    }
    return mapping.get(status, "tag--blue")

def ticket_tab_label(tab: str) -> str:
    return {
        "abertos": "Abertos",
        "andamento": "Em andamento",
        "concluidos": "Concluídos",
        "todos": "Todos",
    }.get(tab, "Todos")


def render_agent_invite_section(
    csrf_token: str,
    hidden_fields: dict[str, str] | None = None,
    form_data: dict[str, str] | None = None,
    message: str = "",
    kind: str = "info",
    open_by_default: bool = False,
) -> str:
    hidden_fields = hidden_fields or {}
    form_data = form_data or {}
    source = hidden_fields.get("source", "users")
    extra_hidden_fields = {key: value for key, value in hidden_fields.items() if key != "source"}
    hidden_inputs = "".join(
        f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(value)}">'
        for key, value in extra_hidden_fields.items()
    )
    role = form_data.get("role", "dp")
    details_attrs = " open" if open_by_default else ""
    invite_flash = flash(kind, message) if message else ""
    return f"""
    <section class="section-card" id="adicionar-agente">
      <div class="section-heading">
        <div>
          <p class="section-heading__eyebrow">Convites</p>
          <h2>Adicionar agente</h2>
        </div>
        <p class="section-heading__text">Envie um convite por e-mail para liberar acesso ao portal e cadastro de senha.</p>
      </div>
      {invite_flash}
      <details class="invite-disclosure"{details_attrs}>
        <summary class="button button--primary invite-disclosure__summary">Adicionar agente</summary>
        <form class="ticket-form invite-disclosure__form" method="post" action="/dp/usuarios/convites">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
          <input type="hidden" name="source" value="{html.escape(source)}">
          {hidden_inputs}
          <div class="field-grid">
            <label><span>Nome</span><input name="name" type="text" value="{html.escape(form_data.get('name', ''))}" required></label>
            <label><span>E-mail</span><input name="email" type="email" value="{html.escape(form_data.get('email', ''))}" required></label>
          </div>
          <label><span>Perfil</span>
            <select name="role" required>
              <option value="dp" {"selected" if role == "dp" else ""}>Agente DP</option>
              <option value="beta" {"selected" if role == "beta" else ""}>Beta</option>
              <option value="alfa" {"selected" if role == "alfa" else ""}>Alfa</option>
              <option value="ambos" {"selected" if role == "ambos" else ""}>Ambos</option>
              <option value="gama" {"selected" if role == "gama" else ""}>Gama</option>
            </select>
          </label>
          <div class="form-actions">
            <button class="button button--primary" type="submit">Enviar convite</button>
          </div>
        </form>
      </details>
    </section>
    """


def _decode_multipart_headers(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    headers: dict[str, str] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    return headers


def _parse_content_disposition(value: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for segment in value.split(";"):
        segment = segment.strip()
        if "=" not in segment:
            continue
        key, _, raw_value = segment.partition("=")
        params[key.strip().lower()] = raw_value.strip().strip('"')
    return params


def parse_multipart_form_data(content_type: str, body: bytes) -> tuple[dict[str, str], list[dict[str, str | bytes | int]]]:
    """Streaming-friendly multipart parser.

    Splits the request body on the boundary directly instead of feeding the whole
    payload to email.BytesParser, which inflates memory ~15x for large binary
    attachments (medido: anexo de 40 MB -> pico de 626 MB). Aqui o pico fica em
    ~2x o tamanho do corpo.
    """
    data: dict[str, str] = {}
    attachments: list[dict[str, str | bytes | int]] = []

    boundary = ""
    for segment in content_type.split(";"):
        segment = segment.strip()
        if segment.lower().startswith("boundary="):
            boundary = segment[len("boundary="):].strip().strip('"')
    if not boundary:
        return data, attachments

    delimiter = b"--" + boundary.encode("latin-1")
    for chunk in body.split(delimiter)[1:]:
        if chunk[:2] == b"--":
            break
        if chunk[:2] == b"\r\n":
            chunk = chunk[2:]
        elif chunk[:1] == b"\n":
            chunk = chunk[1:]
        header_end = chunk.find(b"\r\n\r\n")
        sep_len = 4
        if header_end == -1:
            header_end = chunk.find(b"\n\n")
            sep_len = 2
        if header_end == -1:
            continue
        headers = _decode_multipart_headers(chunk[:header_end])
        content = chunk[header_end + sep_len:]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        elif content.endswith(b"\n"):
            content = content[:-1]

        disposition = headers.get("content-disposition", "")
        if "form-data" not in disposition:
            continue
        params = _parse_content_disposition(disposition)
        field_name = params.get("name")
        if not field_name:
            continue

        filename = params.get("filename")
        if filename:
            attachments.append(
                {
                    "filename": filename,
                    "content_type": headers.get("content-type", "application/octet-stream")
                    or "application/octet-stream",
                    "content": content,
                    "size_bytes": len(content),
                }
            )
            continue

        charset = "utf-8"
        ctype = headers.get("content-type", "")
        if "charset=" in ctype.lower():
            charset = ctype.lower().split("charset=")[-1].split(";")[0].strip().strip('"') or "utf-8"
        data[field_name] = content.decode(charset, errors="replace").strip()
    return data, attachments


def public_home(
    categories: list[sqlite3.Row],
    in_progress_tickets: list[sqlite3.Row],
    completed_tickets: list[sqlite3.Row],
    user: sqlite3.Row | None = None,
    csrf_token: str = "",
    message: str = "",
    kind: str = "info",
) -> str:
    allowed_category_names = set(user_allowed_category_names(user["role"])) if user else set()
    visible_categories = [row for row in categories if not allowed_category_names or row["name"] in allowed_category_names]

    def request_type_badge(request_category: str) -> str:
        return {
            "Admissão": "tag--green",
            "Folha mensal": "tag--blue",
            "Férias": "tag--amber",
            "Rescisão": "tag--slate",
            "Processos trabalhistas": "tag--blue",
            "Outros": "tag--green",
        }.get(request_category, "tag--blue")

    def request_type_button(row: sqlite3.Row, request_category: str) -> str:
        href = f"/novo-chamado?category_id={row['id']}&request_category={quote(request_category)}"
        return f"""
        <a class="request-type-card" href="{href}">
          <span class="tag {request_type_badge(request_category)}">{html.escape(request_category)}</span>
        </a>
        """

    def request_type_group(row: sqlite3.Row) -> str:
        return f"""
        <section class="request-type-group">
          <div class="section-heading section-heading--stacked">
            <div>
              <p class="section-heading__eyebrow">Origem</p>
              <h3>{html.escape(row["name"])}</h3>
            </div>
            <p class="section-heading__text">{html.escape(row["description"] or "")}</p>
          </div>
          <div class="request-type-grid">
            {''.join(request_type_button(row, request_category) for request_category in REQUEST_CATEGORY_OPTIONS)}
          </div>
        </section>
        """

    request_type_section = (
        "".join(request_type_group(row) for row in visible_categories)
        if len(visible_categories) > 1
        else request_type_group(visible_categories[0])
        if visible_categories
        else ""
    )

    def short_text(value: str, limit: int = 120) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    def dashboard_column(title: str, eyebrow: str, rows: list[sqlite3.Row], empty_message: str) -> str:
        if not rows:
            items = f'<p class="dashboard-empty">{html.escape(empty_message)}</p>'
        else:
            items = "".join(
                f"""
                <a class="dashboard-ticket" href="/acompanhar/{html.escape(row['public_token'])}">
                  <div class="dashboard-ticket__head">
                    <div>
                      <h3>#{row["id"]} {html.escape(row["title"])}</h3>
                    </div>
                    <span class="tag {ticket_badge(row['status'])}">{html.escape(row["status"])}</span>
                  </div>
                  <p class="dashboard-ticket__summary">{html.escape(short_text(admission_summary_text(admission_payload_from_ticket_row(row)) or row["description"]))}</p>
                  <dl class="dashboard-ticket__meta">
                    <div><dt>Empresa</dt><dd>{html.escape(row["company_name"] or "-")}</dd></div>
                    <div><dt>Operador</dt><dd>{html.escape(row["assigned_name"] or "Não atribuído")}</dd></div>
                    <div><dt>Atualização</dt><dd>{html.escape(format_datetime(row["updated_at"]))}</dd></div>
                  </dl>
                </a>
                """
                for row in rows
            )
        return f"""
        <section class="dashboard-column">
          <div class="section-heading section-heading--stacked">
            <div>
              <p class="section-heading__eyebrow">{html.escape(eyebrow)}</p>
              <h2>{html.escape(title)}</h2>
            </div>
          </div>
          <div class="dashboard-column__list">
            {items}
          </div>
        </section>
        """

    dashboard_section = f"""
      <section class="section-card">
        <div class="section-heading">
          <div>
            <p class="section-heading__eyebrow">Dashboard</p>
            <h2>Acompanhamento operacional</h2>
          </div>
        </div>
        <div class="dashboard-grid">
          {dashboard_column("Em andamento", "Ativos", in_progress_tickets, "Nenhuma solicitação em andamento neste momento.")}
          {dashboard_column("Finalizados", "Últimas entregas", completed_tickets, "Nenhuma solicitação finalizada ainda.")}
        </div>
      </section>
    """
    dp_access_button = ""
    if is_dp_user(user):
        dp_access_button = '<div class="hero__actions"><a class="button button--ghost" href="/dp">Acesso ao DP</a></div>'

    return html_page(
        "Portal DP",
        f"""
        {user_ticket_search_bar(message, kind)}
        <header class="hero hero--request">
          <div class="hero__content">
            {hero_mark_html()}
            <div class="eyebrow">Empresa Contabilidade</div>
            <h1>Portal DP</h1>
            <p class="hero__text">Escolha o tipo de solicitação e siga direto para o preenchimento no fluxo padronizado da Êxito.</p>
            {dp_access_button}
          </div>
        </header>
        <main class="content-flow">
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Solicitações</p>
                <h2>Selecione o tipo de solicitação</h2>
              </div>
            </div>
            {request_type_section}
          </section>
          {dashboard_section}
        </main>
        """,
        user,
        csrf_token,
    )


def my_tickets_page(user: sqlite3.Row, tickets: list[sqlite3.Row], csrf_token: str, active_tab: str, summary: sqlite3.Row, q: str = "") -> str:
    page_title = "Todas solicitações"
    hero_title = "Todas solicitações."
    hero_text = "Veja todas as solicitações já abertas no portal."
    section_title = "Todas as solicitações do portal"
    empty = '<p class="section-heading__text">Nenhuma solicitação encontrada nesta aba.</p>'
    tab_items = [
        ("todos", summary["total_count"]),
        ("abertos", summary["open_count"]),
        ("andamento", summary["progress_count"]),
        ("concluidos", summary["done_count"]),
    ]
    q_suffix = f"&q={quote(q)}" if q else ""
    tabs = "".join(
        f'<a class="tab-link {"tab-link--active" if active_tab == key else ""}" href="/minhas-solicitacoes?tab={key}{q_suffix}">{ticket_tab_label(key)} <span>{count or 0}</span></a>'
        for key, count in tab_items
    )
    rows = "".join(
        f"""
        <article class="ticket-row">
          <div class="ticket-row__head">
            <div>
              <div class="ticket-row__badges">
                <span class="tag {ticket_badge(row['status'])}">{html.escape(ticket_status_label(row['status']))}</span>
              </div>
              <h3>{html.escape(row['title'])}</h3>
            </div>
            <div class="ticket-row__meta">
              <span>#{row['id']}</span>
              <span>{html.escape(format_datetime(row['created_at']))}</span>
            </div>
          </div>
          {render_admission_ticket_summary(row)}
          <div class="ticket-grid">
            <div><strong>Origem</strong><span>{html.escape(row['category_name'])}</span></div>
            <div><strong>Empresa</strong><span>{html.escape(row['company_name'] or '-')}</span></div>
            <div><strong>Categoria</strong><span>{html.escape(row['request_category'] or '-')}</span></div>
            <div><strong>Responsável</strong><span>{html.escape(row['assigned_name'] or 'Equipe DP')}</span></div>
            <div><strong>Abertura</strong><span>{html.escape(format_datetime(row['created_at']))}</span></div>
            <div><strong>Última atualização</strong><span>{html.escape(format_datetime(row['updated_at']))}</span></div>
          </div>
          <div class="form-actions">
            <a class="button button--ghost" href="/acompanhar/{html.escape(row['public_token'])}">Consultar solicitação</a>
          </div>
        </article>
        """
        for row in tickets
    )
    return html_page(
        page_title,
        f"""
        {user_ticket_search_bar(q=q)}
        <header class="hero hero--request">
          <div class="hero__content">
            {hero_mark_html()}
            <div class="eyebrow">Solicitações</div>
            <h1>{hero_title}</h1>
            <p class="hero__text">{hero_text}</p>
          </div>
        </header>
        <main class="content-flow">
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Histórico</p>
                <h2>{section_title}</h2>
              </div>
              <a class="button button--ghost" href="/">Início</a>
            </div>
            <div class="tabs-bar">{tabs}</div>
            <div class="ticket-list">
              {rows if rows else empty}
            </div>
          </section>
        </main>
        """,
        user,
        csrf_token,
    )


def public_form_page(
    categories: list[sqlite3.Row],
    csrf_token: str,
    user: sqlite3.Row | None = None,
    message: str = "",
    kind: str = "info",
    form_data: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    created_ticket_id: int | None = None,
    public_token: str | None = None,
) -> str:
    form_data = form_data or {}
    errors = errors or {}
    allowed_category_names = set(user_allowed_category_names(user["role"])) if user else set()
    visible_categories = [row for row in categories if not allowed_category_names or row["name"] in allowed_category_names]
    selected_category_id = form_data.get("category_id", "")
    selected_request_category = form_data.get("request_category", "").strip()
    selected_category = next((row for row in visible_categories if str(row["id"]) == selected_category_id), None)
    selected_category_badge = "tag--green" if selected_category and selected_category["name"].startswith("Alfa") else "tag--blue"
    company_options = company_options_for_category(selected_category["name"] if selected_category else None)
    is_admission_selected = selected_request_category == "Admissão"
    is_vacation_selected = selected_request_category == "Férias"
    is_rescission_selected = selected_request_category == "Rescisão"
    company_options_html = render_select_options(company_options, form_data.get("company_name", ""))
    rescission_type_options_html = render_select_options(RESCISSION_TYPE_OPTIONS, form_data.get("rescission_type", ""))
    admission_fields_html = render_admission_fields(form_data, errors)
    vacation_fields_html = render_vacation_fields(form_data, errors)
    rescission_employee_fields_html = ""
    rescission_events_html = ""
    if is_rescission_selected:
        rescission_employee_fields_html = f"""
              <div class="field-grid">
                <label class="field {'field--error' if errors.get('rescission_registration') else ''}">
                  <span>Matrícula</span>
                  <input name="rescission_registration" type="text" maxlength="{EMPLOYEE_REGISTRATION_MAX}" value="{input_value(form_data, 'rescission_registration')}" aria-invalid="{'true' if errors.get('rescission_registration') else 'false'}">
                  {field_error(errors, 'rescission_registration')}
                </label>
                <label class="field {'field--error' if errors.get('rescission_employee_name') else ''}">
                  <span>Nome</span>
                  <input name="rescission_employee_name" type="text" maxlength="{RESCISSION_NAME_MAX}" value="{input_value(form_data, 'rescission_employee_name')}" aria-invalid="{'true' if errors.get('rescission_employee_name') else 'false'}">
                  {field_error(errors, 'rescission_employee_name')}
                </label>
              </div>
        """
        title_or_rescission_date_html = f"""
                <label class="field {'field--error' if errors.get('rescission_date') else ''}">
                  <span>Data da rescisão</span>
                  <input name="rescission_date" type="date" value="{input_value(form_data, 'rescission_date')}" required aria-invalid="{'true' if errors.get('rescission_date') else 'false'}">
                  {field_error(errors, 'rescission_date')}
                </label>
        """
        rescission_events_html = render_rescission_events_section(form_data, errors)
    else:
        title_or_rescission_date_html = f"""
                <label class="field {'field--error' if errors.get('title') else ''}">
                  <span>Título da solicitação</span>
                  <input name="title" type="text" value="{input_value(form_data, 'title')}" required aria-invalid="{'true' if errors.get('title') else 'false'}" data-generic-required>
                  {field_error(errors, 'title')}
                </label>
        """
    success_cta = ""
    if created_ticket_id and public_token:
        success_cta = f"""
        <div class="success-panel">
          <strong>Solicitação #{created_ticket_id} registrada.</strong>
          <p>Use o botão abaixo para acompanhar o andamento da sua solicitação.</p>
          <a class="button button--ghost" href="/acompanhar/{html.escape(public_token)}">Consultar solicitação</a>
        </div>
        """
    return html_page(
        "Nova solicitação",
        f"""
        <main class="auth-shell auth-shell--wide">
          <section class="auth-card auth-card--form">
            <div class="form-card__header">
              <div class="section-heading section-heading--stacked form-card__heading">
                <div>
                  <p class="section-heading__eyebrow">Solicitação</p>
                  <h1>Nova solicitação de folha</h1>
                </div>
                <p class="section-heading__text">{
                  html.escape(f"Origem selecionada: {selected_category['name']}. Escolha a empresa e descreva a solicitação da folha." if selected_category else "Preencha o formulário para registrar sua solicitação da folha.")
                }</p>
              </div>
              <img class="form-card__logo" src="/assets/logo-flat.png" alt="Logo">
            </div>
            <div class="category-hero">
              <span class="tag {selected_category_badge}">{html.escape(selected_category['name'] if selected_category else 'Origem não selecionada')}</span>
              <strong>{html.escape(selected_category['description'] if selected_category and selected_category['description'] else 'Preencha os campos abaixo para registrar a solicitação.')}</strong>
            </div>
            {flash(kind, message)}
            {success_cta}
            <form class="ticket-form" method="post" action="/abrir-chamado" enctype="multipart/form-data" novalidate>
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
              <input type="hidden" name="category_id" value="{html.escape(selected_category_id)}">
              <input type="hidden" name="request_category" value="{html.escape(selected_request_category)}" data-request-category>
              <div class="field-grid">
                <label class="field {'field--error' if errors.get('company_name') else ''}">
                  <span class="field-label field-label--strong">Empresa</span>
                  <select name="company_name" required aria-invalid="{'true' if errors.get('company_name') else 'false'}">
                    {company_options_html}
                  </select>
                  {field_error(errors, 'company_name')}
                </label>
              </div>
              <div class="field field--readonly {'field--error' if errors.get('request_category') else ''}">
                <span class="field-label">Categoria</span>
                <strong class="field-readonly-value">{html.escape(selected_request_category or 'Tipo de solicitação não selecionado')}</strong>
                {field_error(errors, 'request_category')}
              </div>
              <div class="field-grid {'is-hidden' if not is_rescission_selected else ''}">
                <label class="field {'field--error' if errors.get('rescission_type') else ''}">
                  <span>Tipo de rescisão</span>
                  <select name="rescission_type" {'required' if is_rescission_selected else ''} aria-invalid="{'true' if errors.get('rescission_type') else 'false'}">
                    {rescission_type_options_html}
                  </select>
                  {field_error(errors, 'rescission_type')}
                </label>
              </div>
              {rescission_employee_fields_html}
              <div class="generic-request-fields {'is-hidden' if is_admission_selected else ''}">
                {title_or_rescission_date_html}
                <label class="field {'field--error' if errors.get('description') else ''}">
                  <span>Descrição da solicitação</span>
                  <textarea name="description" rows="6" required aria-invalid="{'true' if errors.get('description') else 'false'}" data-generic-required>{input_value(form_data, 'description')}</textarea>
                  {field_error(errors, 'description')}
                </label>
                {rescission_events_html}
              </div>
              <div class="{'is-hidden' if not is_admission_selected else ''}" data-admission-container>
                {admission_fields_html}
              </div>
              <div class="{'is-hidden' if not is_vacation_selected else ''}" data-vacation-container>
                {vacation_fields_html}
              </div>
              <label class="field {'field--error' if errors.get('attachment') else ''}">
                <span>Anexos</span>
                <input name="attachment" type="file" multiple>
                {field_error(errors, 'attachment')}
              </label>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Enviar solicitação</button>
              </div>
            </form>
            {render_public_form_script()}
          </section>
        </main>
        """,
        user=user,
        csrf_token=csrf_token,
    )


def auth_page(title: str, subtitle: str, form_html: str, csrf_token: str, message: str = "", kind: str = "info") -> str:
    return html_page(
        title,
        f"""
        <main class="auth-shell">
          <section class="auth-card">
            <div class="auth-card__header">
              <h1>{html.escape(title)}</h1>
              <div class="auth-brand">{brand_logo()}</div>
            </div>
            <p class="hero__text auth-card__text">{html.escape(subtitle)}</p>
            {flash(kind, message)}
            {form_html}
          </section>
        </main>
        """,
        csrf_token=csrf_token,
    )


def password_reset_request_page(csrf_token: str, message: str = "", kind: str = "info", form_data: dict[str, str] | None = None) -> str:
    form_data = form_data or {}
    form = f"""
    <form class="ticket-form" method="post" action="/esqueci-senha">
      <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
      <label><span>E-mail</span><input name="email" type="email" value="{input_value(form_data, 'email')}" required></label>
      <div class="form-actions">
        <button class="button button--primary" type="submit">Enviar link</button>
        <a class="button button--secondary" href="/dp/login">Voltar ao login</a>
      </div>
    </form>
    """
    return auth_page(
        "Redefinir senha",
        "Informe o e-mail cadastrado para receber um link de redefinição de senha.",
        form,
        csrf_token,
        message,
        kind,
    )


def password_reset_page(reset: sqlite3.Row, csrf_token: str, message: str = "", kind: str = "info", form_data: dict[str, str] | None = None) -> str:
    form = f"""
    <form class="ticket-form" method="post" action="/redefinir-senha/{html.escape(reset['reset_token'])}">
      <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
      <label><span>Nova senha</span><input name="password" type="password" required></label>
      <label><span>Confirmar senha</span><input name="confirm_password" type="password" required></label>
      <div class="form-actions">
        <button class="button button--primary" type="submit">Redefinir senha</button>
      </div>
    </form>
    """
    return auth_page(
        "Criar nova senha",
        f"Definindo uma nova senha para {reset['user_name'] or reset['email']}. Use pelo menos 8 caracteres, com letras e números.",
        form,
        csrf_token,
        message,
        kind,
    )


def invite_accept_page(invite: sqlite3.Row, csrf_token: str, message: str = "", kind: str = "info", form_data: dict[str, str] | None = None) -> str:
    form_data = form_data or {}
    role_label = user_role_label(invite["role"])
    return html_page(
        "Cadastro de acesso",
        f"""
        <main class="auth-shell">
          <section class="auth-card">
            <div class="auth-brand">{brand_logo()}</div>
            <div class="eyebrow">Convite</div>
            <h1>Defina sua senha</h1>
            <p class="hero__text auth-card__text">Convite para <strong>{html.escape(invite['user_name'] or invite['email'])}</strong> com perfil <strong>{role_label}</strong>.</p>
            {flash(kind, message)}
            <form class="ticket-form" method="post" action="/convites/{html.escape(invite['invite_token'])}">
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
              <label><span>Senha</span><input name="password" type="password" required></label>
              <label><span>Confirmar senha</span><input name="confirm_password" type="password" required></label>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Concluir cadastro</button>
              </div>
            </form>
          </section>
        </main>
        """,
        csrf_token=csrf_token,
    )


def _event_actor_name(row: "sqlite3.Row | dict[str, str]") -> str:
    try:
        return (row["actor_name"] or "").strip()
    except (KeyError, IndexError):
        return ""


def interaction_author(row: "sqlite3.Row | dict[str, str]", origin_name: str = "") -> tuple[str, str]:
    event_type = row["event_type"]
    actor_name = _event_actor_name(row)
    origin = (origin_name or "").strip()
    if event_type == "comentario":
        return (f"{actor_name} - DP" if actor_name else "Departamento Pessoal"), "dp"
    if event_type == "solicitante":
        if actor_name and origin:
            label = f"{actor_name} - {origin}"
        elif actor_name:
            label = actor_name
        elif origin:
            label = f"Empresa - {origin}"
        else:
            label = "Empresa"
        return label, "requester"
    return "Sistema", "system"


def render_interaction_feed(events: list[sqlite3.Row], origin_name: str = "") -> str:
    comment_events = [row for row in events if row["event_type"] in {"comentario", "solicitante"}]
    return "".join(
        f"""
        <article class="interaction-card interaction-card--{speaker_class}">
          <div class="interaction-card__header">
            <div class="interaction-card__identity">
              <span class="interaction-card__author">{html.escape(author_label)}</span>
              <span class="interaction-card__label">{'Resposta do DP' if speaker_class == 'dp' else 'Retorno da empresa'}</span>
            </div>
            <span class="interaction-card__time">{html.escape(format_datetime(row['created_at']))}</span>
          </div>
          <div class="interaction-card__body">
            <p>{render_event_message(row)}</p>
          </div>
        </article>
        """
        for row in comment_events
        for author_label, speaker_class in [interaction_author(row, origin_name)]
    ) or """
        <div class="interaction-empty">
          <strong>Nenhuma interação registrada ainda.</strong>
          <p>Quando o DP solicitar algo ou a empresa responder, as atualizações compartilhadas vão aparecer aqui.</p>
        </div>
    """


def public_ticket_page(
    ticket: sqlite3.Row,
    events: list[sqlite3.Row],
    attachments: list[sqlite3.Row],
    csrf_token: str,
    can_comment: bool = False,
    message: str = "",
    kind: str = "info",
    logged_in: bool = False,
) -> str:
    comment_items = render_interaction_feed(events, ticket["category_name"])
    status_events = [row for row in events if row["event_type"] not in {"comentario", "solicitante"}]
    allowed_events = "".join(
        f"""
        <li class="timeline-item">
          <strong>{render_event_message(row)}</strong>
          <span>{html.escape(format_datetime(row['created_at']))}</span>
        </li>
        """
        for row in status_events
    ) or '<li class="timeline-item"><strong>Nenhuma atualização registrada ainda.</strong></li>'
    attachment_items = "".join(
        f'<li class="attachment-list__item"><a href="/anexos/{row["id"]}?token={html.escape(ticket["public_token"])}">{html.escape(row["original_name"])}</a> <span class="attachment-list__date">{html.escape(format_datetime(row["created_at"]))}</span></li>'
        for row in attachments
    ) or '<li class="attachment-list__item">Nenhum anexo enviado.</li>'
    comment_form = ""
    ticket_cancelled = ticket["status"] == "Cancelado"
    can_comment = can_comment and not ticket_cancelled
    if ticket_cancelled:
        comment_form = f"""
        <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Mensagem</p>
                <h2>Solicitação cancelada</h2>
              </div>
            </div>
          {flash(kind, message)}
        </section>
        """
    elif can_comment and not logged_in:
        login_url = f"/dp/login?next=/acompanhar/{quote(ticket['public_token'])}"
        comment_form = f"""
        <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Mensagem</p>
                <h2>Enviar mensagem</h2>
                <p class="section-heading__text">Para responder a esta solicitação, entre com o e-mail e a senha do portal. Assim a resposta fica identificada com o seu nome.</p>
              </div>
            </div>
          {flash(kind, message)}
          <div class="form-actions">
            <a class="button button--primary" href="{html.escape(login_url, quote=True)}">Entrar para responder</a>
          </div>
        </section>
        """
    elif can_comment:
        comment_form = f"""
        <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Mensagem</p>
                <h2>Enviar mensagem</h2>
                <p class="section-heading__text">Responda ao Departamento Pessoal e, se precisar, anexe arquivos no mesmo envio.</p>
              </div>
            </div>
          {flash(kind, message)}
          <form class="ticket-form interaction-composer" method="post" action="/acompanhar/{html.escape(ticket['public_token'])}/comentario" enctype="multipart/form-data">
            <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
            <div class="field"><span id="comment-label" class="field-label">Mensagem</span>{rich_text_editor("comment", "Escreva aqui sua mensagem para o Departamento Pessoal.", 4, "comment-label")}</div>
            <label><span>Anexar arquivo(s)</span><input name="attachment" type="file" multiple></label>
            <p class="help-text">Você pode enviar só a mensagem, só o anexo, ou os dois juntos.</p>
            <div class="form-actions">
              <button class="button button--primary" type="submit">Enviar mensagem</button>
            </div>
          </form>
        </section>
        """
    return html_page(
        f"Solicitação #{ticket['id']}",
        f"""
        <header class="hero hero--request">
          <div class="hero__content">
            {hero_mark_html()}
            <div class="eyebrow">Acompanhamento público</div>
            <h1>Solicitação #{ticket['id']}</h1>
            <p class="hero__text">Consulte aqui o andamento da sua solicitação sem acessar a área interna do Departamento Pessoal.</p>
          </div>
        </header>
        <main class="content-flow">
          <section class="section-card">
              <div class="ticket-grid ticket-grid--public">
              <div><strong>Status</strong><span><span class="tag {ticket_badge(ticket['status'])}">{html.escape(ticket_status_label(ticket['status']))}</span></span></div>
              <div><strong>Origem</strong><span>{html.escape(ticket['category_name'])}</span></div>
              <div><strong>Empresa</strong><span>{html.escape(ticket['company_name'] or '-')}</span></div>
              <div><strong>Categoria</strong><span>{html.escape(ticket['request_category'] or '-')}</span></div>
              <div><strong>Abertura</strong><span>{html.escape(format_datetime(ticket['created_at']))}</span></div>
              <div><strong>Última atualização</strong><span>{html.escape(format_datetime(ticket['updated_at']))}</span></div>
              <div><strong>Responsável</strong><span>{html.escape(ticket['assigned_name'] or 'Equipe DP')}</span></div>
            </div>
            <h2>{html.escape(ticket['title'])}</h2>
            {render_admission_ticket_summary(ticket)}
            {render_admission_ticket_details(ticket)}
            <div class="attachment-list">
              <strong>Anexos</strong>
              <ul>{attachment_items}</ul>
            </div>
          </section>
          {comment_form}
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Interações</p>
                <h2>Atualizações compartilhadas</h2>
                <p class="section-heading__text">Este espaço reúne os retornos visíveis entre a empresa e o Departamento Pessoal em ordem de acompanhamento.</p>
              </div>
            </div>
            <div class="interaction-feed">{comment_items}</div>
          </section>
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Andamento</p>
                <h2>Histórico visível</h2>
              </div>
            </div>
            <ul class="timeline">{allowed_events}</ul>
          </section>
        </main>
        """,
        csrf_token=csrf_token,
    )


def render_ti_dashboard(
    user: sqlite3.Row,
    tickets: list[sqlite3.Row],
    events_by_ticket: dict[int, list[sqlite3.Row]],
    summary: sqlite3.Row,
    active_tab: str,
    csrf_token: str,
    categories: list[sqlite3.Row],
    users: list[sqlite3.Row],
    filters: dict[str, str],
    expanded_ticket_id: int | None = None,
    message: str = "",
    kind: str = "info",
) -> str:
    tab_items = [
        ("abertos", summary["open_count"]),
        ("andamento", summary["progress_count"]),
        ("concluidos", summary["done_count"]),
        ("todos", summary["total_count"]),
    ]
    category_lookup = {row["name"]: row["id"] for row in categories}
    dashboard_invite_section = render_agent_invite_section(
        csrf_token,
        {
            "source": "dashboard",
            "active_tab": active_tab,
            "category_id": filters.get("category_id", ""),
            "assigned_to": filters.get("assigned_to", ""),
            "folha_owner": filters.get("folha_owner", ""),
            "q": filters.get("q", ""),
            "sort": filters.get("sort", "recentes"),
            "ticket": str(expanded_ticket_id or ""),
        },
    )
    dp_users = [row for row in users if row["role"] == "dp"]
    sharepoint_categories = sharepoint_configured_category_ids()
    any_pix_box = False
    rows = []
    for row in tickets:
        attachments = list_ticket_attachments(row["id"])
        assignee_options = '<option value="">Selecione um agente DP</option>' + "".join(
            f'<option value="{user_row["id"]}" {"selected" if row["assigned_to_user_id"] == user_row["id"] else ""}>{html.escape(user_row["name"])}</option>'
            for user_row in dp_users
        )
        options_status = "".join(
            f'<option value="{value}" {"selected" if row["status"] == value else ""}>{html.escape(ticket_status_label(value))}</option>'
            for value in VALID_TICKET_STATUSES
        )
        ticket_events = events_by_ticket.get(row["id"], [])
        ticket_interaction_items = render_interaction_feed(ticket_events, row["category_name"])
        status_events = [event for event in ticket_events if event["event_type"] not in {"comentario", "solicitante"}]
        history = "".join(
            f"""
            <li class="timeline-item">
              <strong>{render_event_message(event)}</strong>
              <span>{html.escape(event['actor_name'] or 'Sistema')} • {html.escape(format_datetime(event['created_at']))}</span>
            </li>
            """
            for event in status_events[:6]
        ) or '<li class="timeline-item"><strong>Nenhuma movimentação registrada.</strong></li>'
        attachment_links = "".join(
            f'<li class="attachment-list__item"><a class="button button--ghost" href="/anexos/{attachment["id"]}">Baixar anexo: {html.escape(attachment["original_name"])}</a> {'<span class="tag tag--blue">oculto ao cliente</span> ' if not attachment["client_visible"] else ''}<span class="attachment-list__date">{html.escape(format_datetime(attachment["created_at"]))}</span></li>'
            for attachment in attachments
        ) or '<li class="attachment-list__item">Nenhum anexo enviado.</li>'
        admission_csv_action = ""
        if row["request_category"] == "Admissão":
            admission_csv_action = f'<a class="button button--ghost" href="/dp/chamados/{row["id"]}/csv">Baixar CSV admissão</a>'
        open_attr = " open" if expanded_ticket_id == row["id"] else ""
        summary_text = html.escape(" ".join((admission_summary_text(admission_payload_from_ticket_row(row)) or row["description"] or "").split())[:180])
        cc_checkboxes = "".join(
            f'<label class="cc-chip"><input type="checkbox" name="cc_{slug}" value="1"><span>{html.escape(label)}</span></label>'
            for slug, _email, label in ticket_cc_options(row["category_name"])
        )
        sharepoint_box = render_sharepoint_box(row, csrf_token, active_tab, sharepoint_categories)
        pix_box = render_pix_box(row)
        if pix_box:
            any_pix_box = True
        rows.append(
            f"""
            <details class="ticket-row ticket-row--collapsible"{open_attr}>
              <summary class="ticket-row__summary">
                <div class="ticket-row__summary-main">
                  <div class="ticket-row__badges">
                    <span class="tag {ticket_badge(row['status'])}">{html.escape(ticket_status_label(row['status']))}</span>
                    <span class="ticket-row__id">#{row['id']}</span>
                  </div>
                  <h3>{html.escape(row['title'])}</h3>
                  <p class="ticket-row__summary-text">{summary_text}</p>
                </div>
                <div class="ticket-row__summary-side">
                  <span>{html.escape(row['category_name'])}</span>
                  <span>{html.escape(row['company_name'] or '-')}</span>
                  <span>{html.escape(row['assigned_name'] or 'Não atribuído')}</span>
                  <span>{html.escape(format_datetime(row['updated_at']))}</span>
                </div>
              </summary>
              <div class="ticket-row__content">
                {render_admission_ticket_summary(row)}
                {render_admission_ticket_details(row)}
                <div class="ticket-grid">
                  <div><strong>Solicitante</strong><span>{html.escape(row['requester_name'])}</span></div>
                  <div><strong>Origem</strong><span>{html.escape(row['category_name'])}</span></div>
                  <div><strong>Empresa</strong><span>{html.escape(row['company_name'] or '-')}</span></div>
                  <div><strong>Categoria</strong><span>{html.escape(row['request_category'] or '-')}</span></div>
                  <div><strong>Responsável</strong><span>{html.escape(row['assigned_name'] or 'Não atribuído')}</span></div>
                  <div><strong>Última atualização</strong><span>{html.escape(format_datetime(row['updated_at']))}</span></div>
                </div>
                {sharepoint_box}
                <form class="ticket-actions" method="post" action="/dp/chamados/{row['id']}/atualizar" enctype="multipart/form-data">
                  <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
                  <input type="hidden" name="active_tab" value="{html.escape(active_tab)}">
                  <input type="hidden" name="category_id" value="{html.escape(filters.get('category_id', ''))}">
                  <input type="hidden" name="assigned_to" value="{html.escape(filters.get('assigned_to', ''))}">
                  <input type="hidden" name="folha_owner" value="{html.escape(filters.get('folha_owner', ''))}">
                  <input type="hidden" name="q" value="{html.escape(filters.get('q', ''))}">
                  <input type="hidden" name="sort" value="{html.escape(filters.get('sort', 'recentes'))}">
                  <input type="hidden" name="ticket" value="{row['id']}">
                  <label><span>Status</span><select name="status">{options_status}</select></label>
                  <div class="field"><span id="reply-label" class="field-label">Resposta ao solicitante</span>{rich_text_editor("comment", "Escreva a devolutiva que ficará visível na consulta pública da solicitação", 3, "reply-label")}</div>
                  {pix_box}
                  <label><span>Anexar arquivo(s)</span><input name="attachment" type="file" multiple></label>
                  <p class="help-text">Você pode responder com texto, com anexo, ou os dois juntos.</p>
                  <p class="help-text">Regra operacional: `Aguardando retorno` exige resposta ao solicitante.</p>
                  <details class="cc-disclosure">
                    <summary class="button button--ghost cc-disclosure__summary">Copiar e-mail para outros destinatários</summary>
                    <div class="cc-disclosure__content">{cc_checkboxes}</div>
                  </details>
                  <div class="form-actions">
                    <button class="button button--primary" type="submit">Salvar</button>
                    <button class="button button--ghost" type="submit" name="assign_to_me" value="1">Assumir atendimento</button>
                    <details class="assign-disclosure">
                      <summary class="button button--ghost assign-disclosure__summary">Designar atendimento</summary>
                      <div class="assign-disclosure__content">
                        <label><span>Agente DP responsável</span><select name="assign_to_user_id">{assignee_options}</select></label>
                        <button class="button button--ghost" type="submit" name="assign_selected" value="1">Confirmar designação</button>
                      </div>
                    </details>
                    {admission_csv_action}
                    <button class="button button--danger" type="submit" formaction="/dp/chamados/{row['id']}/excluir" onclick="return window.confirm('Tem certeza que deseja excluir esta solicitação?');">Excluir solicitação</button>
                  </div>
                </form>
                <div class="section-heading">
                  <div>
                    <p class="section-heading__eyebrow">Interações</p>
                    <h2>Atualizações compartilhadas</h2>
                    <p class="section-heading__text">Este espaço reúne os retornos visíveis entre a empresa e o Departamento Pessoal em ordem de acompanhamento.</p>
                  </div>
                </div>
                <div class="interaction-feed">{ticket_interaction_items}</div>
                <div class="ticket-history">
                  <h4>Histórico</h4>
                  <ul class="timeline">{history}</ul>
                </div>
                <div class="attachment-list">
                  <strong>Anexos ({len(attachments)})</strong>
                  <ul>{attachment_links}</ul>
                </div>
              </div>
            </details>
            """
        )
    tabs = "".join(
        f'<a class="tab-link {"tab-link--active" if active_tab == key else ""}" href="/dp?tab={key}&category_id={html.escape(filters.get("category_id", ""))}&assigned_to={html.escape(filters.get("assigned_to", ""))}&folha_owner={html.escape(filters.get("folha_owner", ""))}&q={html.escape(filters.get("q", ""))}&sort={html.escape(filters.get("sort", "recentes"))}">{ticket_tab_label(key)} <span>{count or 0}</span></a>'
        for key, count in tab_items
    )
    category_options = '<option value="">Todas</option>' + "".join(
        f'<option value="{row["id"]}" {"selected" if filters.get("category_id") == str(row["id"]) else ""}>{html.escape(row["name"])}</option>'
        for row in categories
    )
    assigned_options = '<option value="">Todos</option><option value="none" ' + ('selected' if filters.get("assigned_to") == "none" else '') + '>Sem responsável</option>' + "".join(
        f'<option value="{row["id"]}" {"selected" if filters.get("assigned_to") == str(row["id"]) else ""}>{html.escape(row["name"])}</option>'
        for row in users
    )
    folha_owner_options = '<option value="">Todos</option>' + "".join(
        f'<option value="{row["id"]}" {"selected" if filters.get("folha_owner") == str(row["id"]) else ""}>{html.escape(row["name"])}</option>'
        for row in dp_users
    )
    return html_page(
        "Painel DP",
        f"""
        <header class="hero hero--dashboard">
          <div class="hero__content">
            {hero_mark_html()}
            <div class="eyebrow">Painel DP</div>
            <div class="hero__topline">
              <span>Área restrita</span>
              <span>{html.escape(APP_ENV)}</span>
              <span>Usuário: {html.escape(user['name'])}</span>
            </div>
            <h1>Gestão de solicitações da folha.</h1>
            <p class="hero__text">Filtre a fila, altere o status, assuma atendimentos e acompanhe o histórico de cada solicitação.</p>
            <div class="hero__actions">
              <a class="button button--ghost" href="/dp/empresas">Gestão de empresas</a>
              <a class="button button--ghost" href="/dp/usuarios">Gerenciar agentes</a>
              <a class="button button--ghost" href="/dp/pix">Gestão de Pix</a>
            </div>
          </div>
          <aside class="hero__panel">
            <div class="hero__metric"><strong>{summary['open_count'] or 0}</strong><span>Solicitações abertas</span></div>
            <div class="hero__metric"><strong>{summary['progress_count'] or 0}</strong><span>Em andamento</span></div>
            <div class="hero__metric"><strong>{summary['done_count'] or 0}</strong><span>Concluídas</span></div>
          </aside>
        </header>
        <main class="content-flow">
          {dashboard_invite_section}
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Filtros</p>
                <h2>Fila do Departamento Pessoal</h2>
              </div>
            </div>
            {flash(kind, message)}
            <form class="filter-form" method="get" action="/dp">
              <input type="hidden" name="tab" value="{html.escape(active_tab)}">
              <label><span>Origem</span><select name="category_id">{category_options}</select></label>
              <label><span>Responsável</span><select name="assigned_to">{assigned_options}</select></label>
              <label><span>Responsável folha</span><select name="folha_owner">{folha_owner_options}</select></label>
              <label><span>Busca</span><input name="q" type="text" value="{html.escape(filters.get('q', ''))}" placeholder="Nome, título ou número"></label>
              <input type="hidden" name="ticket" value="{html.escape(str(expanded_ticket_id or ''))}">
              <label><span>Ordenação</span>
                <select name="sort">
                  <option value="recentes" {"selected" if filters.get("sort") == "recentes" else ""}>Mais recentes</option>
                  <option value="antigos" {"selected" if filters.get("sort") == "antigos" else ""}>Mais antigos</option>
                </select>
              </label>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Aplicar filtros</button>
                <a class="button button--ghost" href="/dp?tab={html.escape(active_tab)}">Limpar</a>
              </div>
            </form>
            <div class="tabs-bar">{tabs}</div>
            <div class="ticket-list">
              {''.join(rows) if rows else '<p class="section-heading__text">Nenhuma solicitação encontrada para os filtros atuais.</p>'}
            </div>
          </section>
        </main>
        {PIX_PANEL_SCRIPT if any_pix_box else ""}
        """,
        user,
        csrf_token,
    )


PIX_PANEL_SCRIPT = """
<script>
(function () {
  var MAX = __MAX__;
  var TYPES = __TYPES__;
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }
  function hidden(name, value) {
    var n = document.createElement('input');
    n.type = 'hidden'; n.name = name; n.value = value;
    return n;
  }
  function boxOf(node) { return node && node.closest ? node.closest('[data-pix-box]') : null; }
  function stateOf(b) {
    if (!b._pix) b._pix = { counter: 0, timer: null, seq: 0, query: '' };
    return b._pix;
  }
  function itemCount(b) { return b.querySelectorAll('.pix-item').length; }

  function addItem(b, entry) {
    var st = stateOf(b);
    if (itemCount(b) >= MAX) { alert('Máximo de ' + MAX + ' funcionários por resposta.'); return; }
    if (entry.id) {
      var dup = b.querySelector('.pix-item[data-id="' + entry.id + '"]');
      if (dup) { dup.querySelector('.pix-item__key').focus(); return; }
    }
    var n = st.counter++;
    var li = el('li', 'pix-item');
    li.dataset.id = entry.id || 0;
    li.appendChild(hidden('pix_id_' + n, entry.id || 0));
    var hint;
    if (entry.id) {
      li.appendChild(el('span', 'pix-item__name', entry.name));
      li.appendChild(hidden('pix_name_' + n, entry.name));
      hint = entry.key ? 'Chave cadastrada. Se estiver errada, corrija: ela será atualizada.'
                       : 'Sem chave cadastrada: informe a chave, ela ficará salva.';
    } else {
      var nameIn = el('input', 'pix-item__input pix-item__newname');
      nameIn.type = 'text'; nameIn.name = 'pix_name_' + n; nameIn.value = entry.name || '';
      nameIn.placeholder = 'Nome do funcionário'; nameIn.maxLength = 120;
      li.appendChild(nameIn);
      hint = 'Funcionário novo: nome e chave serão cadastrados.';
    }
    var typeIn = el('select', 'pix-item__input pix-item__type');
    typeIn.name = 'pix_type_' + n;
    typeIn.setAttribute('aria-label', 'Tipo da chave');
    var none = el('option', '', 'Tipo da chave…');
    none.value = '';
    typeIn.appendChild(none);
    TYPES.forEach(function (t) {
      var opt = el('option', '', t);
      opt.value = t;
      if (t === entry.type) opt.selected = true;
      typeIn.appendChild(opt);
    });
    li.appendChild(typeIn);
    var keyIn = el('input', 'pix-item__input pix-item__key');
    keyIn.type = 'text'; keyIn.name = 'pix_key_' + n; keyIn.value = entry.key || '';
    keyIn.placeholder = 'Chave Pix'; keyIn.maxLength = 200; keyIn.autocomplete = 'off';
    li.appendChild(keyIn);
    var rm = el('button', 'pix-item__remove', '×');
    rm.type = 'button'; rm.setAttribute('data-pix-remove', '1');
    rm.setAttribute('aria-label', 'Remover ' + (entry.name || 'funcionário'));
    li.appendChild(rm);
    li.appendChild(el('small', 'pix-item__hint', hint));
    b.querySelector('[data-pix-selected]').appendChild(li);
    return li;
  }

  function showResults(b, results, query) {
    var list = b.querySelector('[data-pix-results]');
    list.innerHTML = '';
    if (!query) return;
    if (!results.length) {
      var li = el('li', 'pix-results__empty', 'Nenhum funcionário encontrado.');
      list.appendChild(li);
      return;
    }
    results.forEach(function (r) {
      var li = el('li');
      var btn = el('button', 'pix-result', r.name + (r.key ? '' : '  (sem chave)'));
      btn.type = 'button';
      btn._entry = r;
      li.appendChild(btn);
      list.appendChild(li);
    });
  }

  function runSearch(b) {
    var st = stateOf(b);
    var input = b.querySelector('[data-pix-search]');
    var q = input.value.trim();
    st.query = q;
    if (!q) { showResults(b, [], ''); return; }
    var mine = ++st.seq;
    fetch('/dp/pix/buscar?category_id=' + encodeURIComponent(b.dataset.category) + '&q=' + encodeURIComponent(q),
          { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error('http ' + r.status); return r.json(); })
      .then(function (data) { if (mine === st.seq) showResults(b, data.results || [], q); })
      .catch(function () {
        if (mine !== st.seq) return;
        var list = b.querySelector('[data-pix-results]');
        list.innerHTML = '';
        list.appendChild(el('li', 'pix-results__empty', 'Não foi possível buscar agora. Atualize a página e tente de novo.'));
      });
  }

  document.addEventListener('change', function (e) {
    var t = e.target;
    if (!t.matches || !t.matches('[data-pix-toggle]')) return;
    var b = boxOf(t);
    b.querySelector('[data-pix-panel]').hidden = !t.checked;
    if (t.checked && !itemCount(b) && b.dataset.suggest) {
      try { addItem(b, JSON.parse(b.dataset.suggest)); } catch (err) {}
    }
  });

  document.addEventListener('input', function (e) {
    var t = e.target;
    if (!t.matches || !t.matches('[data-pix-search]')) return;
    var b = boxOf(t);
    var st = stateOf(b);
    clearTimeout(st.timer);
    st.timer = setTimeout(function () { runSearch(b); }, 200);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && e.target.matches && e.target.matches('.pix-box input[type="text"]')) e.preventDefault();
  });

  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t.closest) return;
    var pick = t.closest('.pix-result');
    if (pick) {
      var b = boxOf(pick);
      addItem(b, pick._entry);
      b.querySelector('[data-pix-search]').value = '';
      showResults(b, [], '');
      return;
    }
    var rm = t.closest('[data-pix-remove]');
    if (rm) { var li = rm.closest('.pix-item'); li.parentNode.removeChild(li); return; }
    var add = t.closest('[data-pix-new]');
    if (add) {
      var bb = boxOf(add);
      var search = bb.querySelector('[data-pix-search]');
      var created = addItem(bb, { id: 0, name: search.value.trim().toUpperCase(), key: '' });
      if (created) {
        search.value = '';
        showResults(bb, [], '');
        created.querySelector(created.querySelector('.pix-item__newname').value ? '.pix-item__key' : '.pix-item__newname').focus();
      }
    }
  });

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || !form.querySelector) return;
    var b = form.querySelector('[data-pix-box]');
    var toggle = b && b.querySelector('[data-pix-toggle]');
    if (!toggle || !toggle.checked) return;
    var action = e.submitter && e.submitter.getAttribute('formaction');
    if (action && action.indexOf('/excluir') !== -1) return;
    var items = b.querySelectorAll('.pix-item');
    var problem = null, focusEl = null;
    if (!items.length) {
      problem = 'Selecione pelo menos um funcionário para enviar a chave Pix.';
      focusEl = b.querySelector('[data-pix-search]');
    }
    items.forEach(function (li) {
      if (problem) return;
      var nameIn = li.querySelector('.pix-item__newname');
      var keyIn = li.querySelector('.pix-item__key');
      var typeIn = li.querySelector('.pix-item__type');
      if (nameIn && !nameIn.value.trim()) { problem = 'Informe o nome do funcionário novo.'; focusEl = nameIn; }
      else if (!keyIn.value.trim()) { problem = 'Informe a chave Pix de cada funcionário selecionado.'; focusEl = keyIn; }
      else if (!typeIn.value) { problem = 'Informe o tipo da chave Pix (CPF, Telefone, E-mail...) de cada funcionário.'; focusEl = typeIn; }
    });
    if (problem) {
      e.preventDefault();
      e.stopImmediatePropagation();
      alert(problem);
      if (focusEl) focusEl.focus();
    }
  }, true);
})();
</script>
""".replace("__MAX__", str(PIX_MAX_ENTRIES)).replace("__TYPES__", json.dumps(PIX_KEY_TYPES))


def render_pix_box(row: sqlite3.Row) -> str:
    """Checkbox 'Enviar chave pix' na resposta do DP (so Beta/Alfa; Folha mensal, Ferias e Rescisao)."""
    if not pix_enabled_for(row["category_name"], row["request_category"]):
        return ""
    suggestion = pix_suggestion_for_ticket(row)
    suggest_attr = html.escape(json.dumps(suggestion, ensure_ascii=True), quote=True) if suggestion else ""
    return f"""
    <div class="pix-box" data-pix-box data-category="{row['category_id']}" data-suggest="{suggest_attr}">
      <label class="cc-chip pix-toggle"><input type="checkbox" name="pix_send" value="1" data-pix-toggle><span>Enviar chave pix</span></label>
      <div class="pix-panel" data-pix-panel hidden>
        <div class="pix-search">
          <input type="text" data-pix-search placeholder="Buscar funcionário pelo nome" autocomplete="off" aria-label="Buscar funcionário pelo nome">
          <ul class="pix-results" data-pix-results></ul>
        </div>
        <ul class="pix-selected" data-pix-selected></ul>
        <button type="button" class="button button--ghost pix-new" data-pix-new>Cadastrar novo funcionário</button>
        <p class="help-text">A chave vai no fim da resposta, como "Chave Pix para pagamento:" com o nome, o tipo e a chave de cada funcionário. Confira o tipo sugerido (CPF, Telefone, E-mail...). Chave e tipo informados aqui ficam salvos para as próximas vezes.</p>
      </div>
    </div>"""


def render_sharepoint_box(row: sqlite3.Row, csrf_token: str, active_tab: str, configured: set[int]) -> str:
    """Estado do envio ao portal de tarefas (so origens com configuracao)."""
    if row["category_id"] not in configured:
        return ""
    request = get_sharepoint_request_for_ticket(row["id"])
    hint = '<p class="help-text">Anexos enviados <strong>ao concluir</strong> esta solicitação não ficam disponíveis ao cliente aqui: seguem pela tarefa do portal de tarefas.</p>'
    if not request:
        return f'<div class="sharepoint-box">{hint}</div>'
    labels = {"pendente": "Aguardando envio", "enviando": "Enviando", "criada": "Enviada", "erro": "Erro no envio", "aviso": "Atenção"}
    task = f" — tarefa #{request['task_id']}" if request["task_id"] else ""
    message = f"<p class=\"help-text\">{html.escape(request['message'])}</p>" if request["message"] else ""
    resend = ""
    if request["status"] in ("erro", "aviso"):
        resend = f"""
        <form method="post" action="/dp/chamados/{row['id']}/sharepoint/reenviar">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
          <input type="hidden" name="active_tab" value="{html.escape(active_tab)}">
          <input type="hidden" name="ticket" value="{row['id']}">
          <button class="button button--ghost" type="submit">Reenviar ao portal de tarefas</button>
        </form>"""
    return f"""
    <div class="sharepoint-box">
      <strong>Portal de tarefas:</strong> {html.escape(labels.get(request['status'], request['status']))}{task}
      {message}{resend}
      {hint}
    </div>"""


def render_company_management(
    user: sqlite3.Row,
    categories: list[sqlite3.Row],
    agents: list[sqlite3.Row],
    owners: dict[int, set[int]],
    csrf_token: str,
    message: str = "",
    kind: str = "info",
) -> str:
    cards = []
    for category in categories:
        selected = owners.get(category["id"], set())
        owner_options = '<option value="">Sem responsável</option>' + "".join(
            f'<option value="{agent["id"]}" {"selected" if agent["id"] in selected else ""}>{html.escape(agent["name"])}</option>'
            for agent in agents
        )
        companies = ", ".join(company_options_for_category(category["name"])) or "—"
        cards.append(
            f"""
            <article class="user-card">
              <div class="user-card__head">
                <div class="user-card__id">
                  <h3>{html.escape(category['name'])}</h3>
                  <p>Empresas: {html.escape(companies)}</p>
                </div>
              </div>
              <form method="post" action="/dp/empresas/{category['id']}/responsaveis">
                <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
                <label><span>Responsável pela folha</span><select name="owner">{owner_options}</select></label>
                <div class="form-actions"><button class="button button--primary" type="submit">Salvar</button></div>
              </form>
            </article>
            """
        )
    return html_page(
        "Gestão de empresas",
        f"""
        <header class="hero hero--dashboard">
          <div class="hero__content">
            {hero_mark_html()}
            <div class="eyebrow">Painel DP</div>
            <h1>Gestão de empresas.</h1>
            <p class="hero__text">Defina o Agente DP responsável pela folha de cada empresa. O filtro "Responsável folha" da fila usa essa atribuição.</p>
            <div class="hero__actions"><a class="button button--ghost" href="/dp">Voltar ao painel</a></div>
          </div>
        </header>
        <main class="content-flow">
          <section class="section-card">
            {flash(kind, message)}
            <div class="user-card-grid">{"".join(cards)}</div>
          </section>
        </main>
        """,
        user,
        csrf_token,
    )


def render_pix_management(
    user: sqlite3.Row,
    origins: list[sqlite3.Row],
    pix_rows: list[sqlite3.Row],
    filters: dict[str, str],
    csrf_token: str,
    message: str = "",
    kind: str = "info",
    add_form: dict[str, str] | None = None,
) -> str:
    add_form = add_form or {}
    filter_hidden = (
        f'<input type="hidden" name="filter_category_id" value="{html.escape(filters["category_id"], quote=True)}">'
        f'<input type="hidden" name="filter_q" value="{html.escape(filters["q"], quote=True)}">'
    )

    def _pix_card(row: sqlite3.Row) -> str:
        selected_type = row["key_type"] or detect_pix_key_type(row["pix_key"])
        type_options = '<option value="">Não definido</option>' + "".join(
            f'<option value="{html.escape(value, quote=True)}" {"selected" if selected_type == value else ""}>{html.escape(value)}</option>'
            for value in PIX_KEY_TYPES
        )
        has_key = bool(row["pix_key"])
        status = (
            html.escape(row["key_type"]) if row["key_type"]
            else "Tipo não confirmado" if has_key
            else "Sem chave"
        )
        return f"""
        <article class="user-card{'' if has_key else ' user-card--inactive'}">
          <div class="user-card__head">
            <div class="user-card__id">
              <h3>{html.escape(row['employee_name'])}</h3>
              <p>{html.escape(row['pix_key']) if has_key else 'Nenhuma chave cadastrada'}</p>
            </div>
            <span class="user-card__status{'' if row['key_type'] else ' is-inactive'}">{status}</span>
          </div>
          <details class="user-card__edit">
            <summary>Alterar</summary>
            <form class="ticket-form user-edit-form" method="post" action="/dp/pix/{row['id']}/atualizar">
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
              {filter_hidden}
              <label><span>Nome</span><input name="name" type="text" maxlength="{PIX_NAME_MAX}" value="{html.escape(row['employee_name'], quote=True)}" required></label>
              <label><span>Chave Pix</span><input name="key" type="text" maxlength="{PIX_KEY_MAX}" autocomplete="off" value="{html.escape(row['pix_key'], quote=True)}"></label>
              <label><span>Tipo da chave</span><select name="key_type">{type_options}</select></label>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Salvar</button>
                <button class="button button--danger" type="submit" formaction="/dp/pix/{row['id']}/excluir" formnovalidate onclick="return window.confirm('Excluir o Pix de ' + this.dataset.name + '? O histórico deste cadastro também será apagado.');" data-name="{html.escape(row['employee_name'], quote=True)}">Excluir</button>
              </div>
            </form>
          </details>
        </article>
        """

    groups_html = ""
    for origin in origins:
        origin_rows = [row for row in pix_rows if row["category_id"] == origin["id"]]
        if filters["category_id"] and filters["category_id"] != str(origin["id"]):
            continue
        cards = (
            f'<div class="user-card-grid">{"".join(_pix_card(row) for row in origin_rows)}</div>'
            if origin_rows
            else '<p class="section-heading__text">Nenhum Pix encontrado nesta origem.</p>'
        )
        groups_html += f"""
        <div class="user-group">
          <h3 class="user-group__title">{html.escape(origin['name'])} <span>{len(origin_rows)}</span></h3>
          {cards}
        </div>
        """
    selected_origin = add_form.get("origin_id") or filters["category_id"]
    add_origin_options = '<option value="">Selecione</option>' + "".join(
        f'<option value="{origin["id"]}" {"selected" if selected_origin == str(origin["id"]) else ""}>{html.escape(origin["name"])}</option>'
        for origin in origins
    )
    filter_origin_options = '<option value="">Todas</option>' + "".join(
        f'<option value="{origin["id"]}" {"selected" if filters["category_id"] == str(origin["id"]) else ""}>{html.escape(origin["name"])}</option>'
        for origin in origins
    )
    return html_page(
        "Gestão de Pix",
        f"""
        <header class="hero hero--dashboard">
          <div class="hero__content">
            {hero_mark_html()}
            <div class="eyebrow">Painel DP</div>
            <h1>Gestão de Pix.</h1>
            <p class="hero__text">Consulte, cadastre, altere e exclua as chaves Pix dos funcionários usadas nas respostas do DP.</p>
            <div class="hero__actions"><a class="button button--ghost" href="/dp">Voltar ao painel</a></div>
          </div>
        </header>
        <main class="content-flow">
          {flash(kind, message) if message else ""}
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Cadastro</p>
                <h2>Adicionar Pix</h2>
              </div>
              <p class="section-heading__text">O tipo da chave é identificado automaticamente e pode ser corrigido em Alterar.</p>
            </div>
            <details class="invite-disclosure"{" open" if add_form else ""}>
              <summary class="button button--primary invite-disclosure__summary">Adicionar Pix</summary>
              <form class="ticket-form invite-disclosure__form" method="post" action="/dp/pix/adicionar">
                <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
                {filter_hidden}
                <div class="field-grid">
                  <label><span>Nome</span><input name="name" type="text" maxlength="{PIX_NAME_MAX}" value="{html.escape(add_form.get('name', ''), quote=True)}" required></label>
                  <label><span>Chave Pix</span><input name="key" type="text" maxlength="{PIX_KEY_MAX}" autocomplete="off" value="{html.escape(add_form.get('key', ''), quote=True)}" required></label>
                </div>
                <label><span>Origem</span><select name="origin_id" required>{add_origin_options}</select></label>
                <div class="form-actions">
                  <button class="button button--primary" type="submit">Cadastrar</button>
                </div>
              </form>
            </details>
          </section>
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Cadastrados</p>
                <h2>Chaves Pix ({len(pix_rows)})</h2>
              </div>
            </div>
            <form class="filter-form" method="get" action="/dp/pix">
              <label><span>Origem</span><select name="category_id">{filter_origin_options}</select></label>
              <label><span>Busca</span><input name="q" type="text" value="{html.escape(filters['q'], quote=True)}" placeholder="Nome do funcionário"></label>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Aplicar filtros</button>
                <a class="button button--ghost" href="/dp/pix">Limpar</a>
              </div>
            </form>
            <div class="user-groups">{groups_html}</div>
          </section>
        </main>
        """,
        user,
        csrf_token,
    )


def render_ti_users(
    user: sqlite3.Row,
    users: list[sqlite3.Row],
    csrf_token: str,
    message: str = "",
    kind: str = "info",
    invite_form_data: dict[str, str] | None = None,
    invite_message: str = "",
    invite_kind: str = "info",
    invite_open: bool = False,
) -> str:
    def _user_card(row: sqlite3.Row) -> str:
        active = bool(row["is_active"])
        return f"""
        <article class="user-card{'' if active else ' user-card--inactive'}">
          <div class="user-card__head">
            <div class="user-card__id">
              <h3>{html.escape(row['name'])}</h3>
              <p>{html.escape(row['email'])}</p>
            </div>
            <span class="user-card__status{'' if active else ' is-inactive'}">{'Ativo' if active else 'Inativo'}</span>
          </div>
          <details class="user-card__edit">
            <summary>Editar acesso</summary>
            <form class="ticket-form user-edit-form" method="post" action="/dp/usuarios/{row['id']}/atualizar">
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
              <label><span>Nome</span><input name="name" type="text" value="{html.escape(row['name'])}" required></label>
              <label><span>E-mail</span><input name="email" type="email" value="{html.escape(row['email'])}" required></label>
              <label><span>Setor</span><input name="department" type="text" value="{html.escape(row['department'] or 'DP')}"></label>
              <label><span>Perfil</span>
                <select name="role">
                  <option value="dp" {"selected" if row['role'] == 'dp' else ""}>Agente DP</option>
                  <option value="beta" {"selected" if row['role'] == 'beta' else ""}>Beta</option>
                  <option value="alfa" {"selected" if row['role'] == 'alfa' else ""}>Alfa</option>
                  <option value="ambos" {"selected" if row['role'] == 'ambos' else ""}>Ambos</option>
                  <option value="gama" {"selected" if row['role'] == 'gama' else ""}>Gama</option>
                </select>
              </label>
              <label><span>Nova senha</span><input name="password" type="password" placeholder="Preencha apenas para trocar"></label>
              <label><span>Status</span>
                <select name="is_active">
                  <option value="1" {"selected" if row['is_active'] else ""}>Ativo</option>
                  <option value="0" {"selected" if not row['is_active'] else ""}>Inativo</option>
                </select>
              </label>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Salvar usuário</button>
              </div>
            </form>
          </details>
        </article>
        """

    def _user_grid(rows: list[sqlite3.Row]) -> str:
        return f'<div class="user-card-grid">{"".join(_user_card(r) for r in rows)}</div>'

    known_roles = {"dp", "beta", "alfa", "ambos", "gama"}
    dp_users = [r for r in users if r["role"] == "dp"]
    empresa_subgroups = [
        (label, [r for r in users if r["role"] == role])
        for role, label in (("beta", "Beta"), ("alfa", "Alfa"), ("gama", "Gama"), ("ambos", "Ambos"))
    ]
    outros_users = [r for r in users if r["role"] not in known_roles]
    empresa_total = sum(len(rows) for _, rows in empresa_subgroups) + len(outros_users)

    groups_html = ""
    if dp_users:
        groups_html += f"""
        <div class="user-group">
          <h3 class="user-group__title">Agentes DP <span>{len(dp_users)}</span></h3>
          {_user_grid(dp_users)}
        </div>
        """
    if empresa_total:
        subgroups_html = "".join(
            f"""
            <div class="user-subgroup">
              <h4 class="user-subgroup__title">{label} <span>{len(rows)}</span></h4>
              {_user_grid(rows)}
            </div>
            """
            for label, rows in ([*empresa_subgroups, ("Sem perfil definido", outros_users)])
            if rows
        )
        groups_html += f"""
        <div class="user-group">
          <h3 class="user-group__title">Empresas <span>{empresa_total}</span></h3>
          {subgroups_html}
        </div>
        """
    return html_page(
        "Usuários DP",
        f"""
        <header class="hero hero--request">
          <div class="hero__content">
            {hero_mark_html()}
            <div class="eyebrow">Equipe DP</div>
            <h1>Usuários e convites.</h1>
            <p class="hero__text">Separe o envio de convites da manutenção dos acessos já cadastrados.</p>
            <div class="hero__actions">
              <a class="button button--ghost" href="/dp">Voltar ao painel</a>
            </div>
          </div>
        </header>
        <main class="content-flow">
          {flash(kind, message) if message else ""}
          {render_agent_invite_section(csrf_token, {"source": "users"}, invite_form_data, invite_message, invite_kind, invite_open)}
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Equipe</p>
                <h2>Acessos cadastrados ({len(users)})</h2>
              </div>
              <p class="section-heading__text">Agrupados por perfil. Abra "Editar acesso" para ajustar perfil, status, dados e senha.</p>
            </div>
            <div class="user-groups">{groups_html}</div>
          </section>
        </main>
        """,
        user,
        csrf_token,
    )


class AppHandler(BaseHTTPRequestHandler):
    server_version = "Projeto1HTTP/1.0"

    def response_body_allowed(self) -> bool:
        return self.command != "HEAD"

    def do_GET(self) -> None:
        try:
            self.route_get()
        except Exception:
            self.handle_unexpected_error("GET", self.path)

    def do_HEAD(self) -> None:
        try:
            self.route_get()
        except Exception:
            self.handle_unexpected_error("HEAD", self.path)

    def do_POST(self) -> None:
        try:
            self.route_post()
        except Exception:
            self.handle_unexpected_error("POST", self.path)

    def do_PUT(self) -> None:
        self.handle_method_not_allowed()

    def do_DELETE(self) -> None:
        self.handle_method_not_allowed()

    def do_PATCH(self) -> None:
        self.handle_method_not_allowed()

    def route_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/interno/"):
            return self.handle_internal_get(path)
        if path == "/healthz":
            return self.handle_healthcheck()
        if path == "/styles.css":
            return self.serve_static("styles.css", "text/css; charset=utf-8")
        if path == "/assets/logo.png":
            return self.serve_static_binary("assets/logo.png", "image/png")
        if path == "/assets/logo-flat.png":
            return self.serve_static_binary("assets/logo-flat.png", "image/png")
        if path == "/assets/mark.png":
            return self.serve_static_binary("assets/mark.png", "image/png")
        if path == "/":
            return self.handle_public_home()
        if path == "/buscar-solicitacao":
            params = parse_qs(parsed.query)
            return self.handle_ticket_search(params)
        if path == "/minhas-solicitacoes":
            params = parse_qs(parsed.query)
            return self.handle_my_tickets(params)
        if path.startswith("/convites/"):
            return self.handle_invite_accept_page(path.strip("/").split("/", 1)[1])
        if path == "/esqueci-senha":
            return self.handle_password_reset_request_page()
        if path.startswith("/redefinir-senha/"):
            parts = path.strip("/").split("/", 1)
            if len(parts) < 2 or not parts[1]:
                return self.handle_not_found()
            return self.handle_password_reset_page(parts[1])
        if path == "/novo-chamado":
            params = parse_qs(parsed.query)
            return self.handle_public_form(params)
        if path in {"/ti/login", "/dp/login"}:
            next_path = safe_next_path(parse_qs(parsed.query).get("next", [""])[0])
            return self.handle_login_page(next_path=next_path)
        if path in {"/ti/setup", "/dp/setup"}:
            return self.handle_setup_page()
        if path in {"/ti", "/dp"}:
            params = parse_qs(parsed.query)
            return self.handle_ti_dashboard(params)
        if path in {"/ti/usuarios", "/dp/usuarios"}:
            return self.handle_ti_users()
        if path == "/dp/empresas":
            return self.handle_company_management()
        if path == "/dp/pix/buscar":
            return self.handle_pix_search(parsed.query)
        if path == "/dp/pix":
            params = parse_qs(parsed.query)
            return self.handle_pix_management(params.get("category_id", [""])[0], params.get("q", [""])[0])
        if path.startswith("/acompanhar/"):
            return self.handle_public_ticket(path.strip("/").split("/", 1)[1])
        if path.startswith("/anexos/"):
            params = parse_qs(parsed.query)
            return self.handle_ticket_attachment(path.strip("/").split("/", 1)[1], params)
        if (path.startswith("/ti/chamados/") or path.startswith("/dp/chamados/")) and path.endswith("/csv"):
            return self.handle_ti_ticket_csv_export(path)
        self.handle_not_found()

    def route_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/interno/"):
            return self.handle_internal_post(path)
        if path.startswith("/dp/chamados/") and path.endswith("/sharepoint/reenviar"):
            return self.handle_sharepoint_resend(path)
        if path == "/abrir-chamado":
            return self.handle_ticket_submit()
        if path in {"/ti/login", "/dp/login"}:
            return self.handle_login_submit()
        if path in {"/ti/logout", "/dp/logout"}:
            return self.handle_logout()
        if path in {"/ti/setup", "/dp/setup"}:
            return self.handle_setup_submit()
        if path.startswith("/dp/empresas/") and path.endswith("/responsaveis"):
            return self.handle_company_owners_submit(path)
        if path.startswith("/dp/pix/"):
            return self.handle_pix_management_submit(path)
        if path in {"/ti/usuarios/convites", "/dp/usuarios/convites"}:
            return self.handle_ti_user_invite_submit()
        if path in {"/ti/usuarios", "/dp/usuarios"}:
            return self.handle_ti_users_submit()
        if path.startswith("/convites/"):
            return self.handle_invite_accept_submit(path.strip("/").split("/", 1)[1])
        if path == "/esqueci-senha":
            return self.handle_password_reset_request_submit()
        if path.startswith("/redefinir-senha/"):
            parts = path.strip("/").split("/", 1)
            if len(parts) < 2 or not parts[1]:
                return self.handle_not_found()
            return self.handle_password_reset_submit(parts[1])
        if path.startswith("/acompanhar/") and path.endswith("/comentario"):
            return self.handle_public_ticket_comment(path)
        if (path.startswith("/ti/usuarios/") or path.startswith("/dp/usuarios/")) and path.endswith("/atualizar"):
            return self.handle_ti_user_update(path)
        if (path.startswith("/ti/chamados/") or path.startswith("/dp/chamados/")) and path.endswith("/excluir"):
            return self.handle_ti_ticket_delete(path)
        if (path.startswith("/ti/chamados/") or path.startswith("/dp/chamados/")) and path.endswith("/atualizar"):
            return self.handle_ti_ticket_update(path)
        self.handle_not_found()

    def add_common_headers(self) -> None:
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src https://fonts.gstatic.com; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'")

    def serve_static(self, file_name: str, content_type: str) -> None:
        with open(BASE_DIR / file_name, "r", encoding="utf-8") as file:
            body = file.read()
        encoded_body = body.encode("utf-8")
        self.send_response(200)
        self.add_common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded_body)))
        self.end_headers()
        if self.response_body_allowed():
            self.wfile.write(encoded_body)

    def serve_static_binary(self, file_name: str, content_type: str) -> None:
        with open(BASE_DIR / file_name, "rb") as file:
            body = file.read()
        self.send_response(200)
        self.add_common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.response_body_allowed():
            self.wfile.write(body)

    def respond_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.add_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.response_body_allowed():
            self.wfile.write(body)

    def parse_form(self) -> tuple[dict[str, str], list[dict[str, str | bytes | int]]]:
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            return parse_multipart_form_data(content_type, body)
        length = int(self.headers.get("Content-Length", "0"))
        raw_bytes = self.rfile.read(length)
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw_bytes.decode("latin-1")
        data = parse_qs(raw)
        return {key: values[0].strip() for key, values in data.items()}, []

    def get_cookie_value(self, key: str) -> str | None:
        header = self.headers.get("Cookie")
        if not header:
            return None
        jar = cookies.SimpleCookie()
        jar.load(header)
        value = jar.get(key)
        return value.value if value else None

    def ensure_csrf(self) -> tuple[str, str | None]:
        token = self.get_cookie_value(CSRF_COOKIE) or generate_csrf_token()
        cookie_header = None
        if self.get_cookie_value(CSRF_COOKIE) != token:
            cookie_header = build_cookie(CSRF_COOKIE, token, secure=USE_SECURE_COOKIE, http_only=False)
        return token, cookie_header

    def verify_csrf(self, form: dict[str, str]) -> bool:
        return bool(form.get("csrf_token") and self.get_cookie_value(CSRF_COOKIE) == form.get("csrf_token"))

    def respond_html(self, body: str, status: int = 200, set_cookie: str | list[str] | None = None) -> None:
        encoded_body = body.encode("utf-8")
        self.send_response(status)
        self.add_common_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded_body)))
        if set_cookie:
            cookies_to_set = set_cookie if isinstance(set_cookie, list) else [set_cookie]
            for cookie in cookies_to_set:
                self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.response_body_allowed():
            self.wfile.write(encoded_body)

    def redirect(self, location: str, set_cookie: str | list[str] | None = None) -> None:
        self.send_response(302)
        self.add_common_headers()
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        if set_cookie:
            cookies_to_set = set_cookie if isinstance(set_cookie, list) else [set_cookie]
            for cookie in cookies_to_set:
                self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def current_user(self) -> sqlite3.Row | None:
        return get_session_user(self.get_cookie_value(SESSION_COOKIE))

    def require_auth(self) -> sqlite3.Row | None:
        user = self.current_user()
        if not user:
            self.redirect("/dp/login")
            return None
        return user

    def require_dp_auth(self) -> sqlite3.Row | None:
        user = self.require_auth()
        if not user:
            return None
        if not is_dp_user(user):
            self.redirect("/")
            return None
        return user

    def allowed_categories_for_user(self, user: sqlite3.Row) -> list[sqlite3.Row]:
        allowed_names = set(user_allowed_category_names(user["role"]))
        return [row for row in list_categories() if row["name"] in allowed_names]

    def user_can_access_category(self, user: sqlite3.Row, category_id: str) -> bool:
        return any(str(row["id"]) == category_id for row in self.allowed_categories_for_user(user))

    def handle_not_found(self) -> None:
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(error_page("Página não encontrada", "A rota informada não existe no sistema.", csrf_token=csrf_token), 404, csrf_cookie)

    def handle_method_not_allowed(self) -> None:
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(error_page("Método não permitido", "Esse método HTTP não é aceito para esta rota.", csrf_token=csrf_token), 405, csrf_cookie)

    def log_exception(self, context: str) -> None:
        logger.error("%s\n%s", context, traceback.format_exc())

    def handle_unexpected_error(self, method: str, path: str) -> None:
        self.log_exception(f"Erro inesperado em {method} {path}")
        csrf_token, csrf_cookie = self.ensure_csrf()
        try:
            self.respond_html(error_page("Falha interna", "Ocorreu um erro inesperado. Tente novamente em instantes.", csrf_token=csrf_token), 500, csrf_cookie)
        except BrokenPipeError:
            pass

    def handle_public_home(self, message: str = "", kind: str = "info") -> None:
        user = self.require_auth()
        if not user:
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        in_progress_tickets, completed_tickets = get_public_dashboard_snapshot(user=user)
        self.respond_html(public_home(list_categories(), in_progress_tickets, completed_tickets, user, csrf_token, message, kind), set_cookie=csrf_cookie)

    def handle_healthcheck(self) -> None:
        try:
            with get_db() as conn:
                ticket_count = int(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
            self.respond_json(
                {
                    "status": "ok",
                    "db_backend": DB_BACKEND,
                    "ticket_count": ticket_count,
                }
            )
        except Exception as exc:
            logger.exception("Healthcheck falhou")
            self.respond_json(
                {
                    "status": "error",
                    "db_backend": DB_BACKEND,
                    "error": str(exc),
                },
                status=503,
            )

    def handle_ticket_search(self, params: dict[str, list[str]]) -> None:
        user = self.require_auth()
        if not user:
            return
        term = (params.get("q") or params.get("numero") or [""])[0].strip()
        if not term:
            return self.handle_public_home("Informe um termo para pesquisar.", "error")
        if term.isdigit():
            ticket = get_user_ticket_by_id(int(term), user)
            if ticket:
                return self.redirect(f"/acompanhar/{ticket['public_token']}")
        self.redirect(f"/minhas-solicitacoes?q={quote(term)}")

    def handle_my_tickets(self, params: dict[str, list[str]] | None = None) -> None:
        user = self.require_auth()
        if not user:
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        search_q = ((params or {}).get("q") or [""])[0].strip()
        default_tab = "todos" if search_q else "abertos"
        active_tab = normalize_tab((params or {}).get("tab", [default_tab])[0])
        filters = {"category_id": "", "assigned_to": "", "q": search_q, "sort": "recentes"}
        tickets, summary, _events_by_ticket = get_dashboard_data(active_tab, filters, user_ticket_category_scope(user))
        self.respond_html(
            my_tickets_page(user, tickets, csrf_token, active_tab, summary, search_q),
            set_cookie=csrf_cookie,
        )

    def handle_public_form(self, params: dict[str, list[str]] | None = None, message: str = "", kind: str = "info", form_data: dict[str, str] | None = None, errors: dict[str, str] | None = None, created_ticket_id: int | None = None, public_token: str | None = None) -> None:
        user = self.require_auth()
        if not user:
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        form_data = dict(form_data or {})
        selected_category_id = (params or {}).get("category_id", [""])[0].strip()
        selected_request_category = (params or {}).get("request_category", [""])[0].strip()
        if selected_category_id and not form_data.get("category_id"):
            form_data["category_id"] = selected_category_id
        if selected_request_category and not form_data.get("request_category"):
            form_data["request_category"] = selected_request_category
        if form_data.get("category_id") and not self.user_can_access_category(user, form_data["category_id"]):
            form_data["category_id"] = ""
            message = "Você não tem acesso a essa origem de solicitação."
            kind = "error"
        self.respond_html(
            public_form_page(list_categories(), csrf_token, user, message, kind, form_data, errors, created_ticket_id, public_token),
            set_cookie=csrf_cookie,
        )

    def handle_ticket_attachment(self, attachment_id_text: str, params: dict[str, list[str]]) -> None:
        try:
            attachment_id = int(attachment_id_text)
        except ValueError:
            return self.handle_not_found()
        attachment, file_path = get_ticket_attachment(attachment_id)
        if not attachment or not file_path or not file_path.exists():
            return self.handle_not_found()

        user = self.current_user()
        token = (params.get("token") or [""])[0].strip()
        if not user:
            public_token = get_public_ticket_token_by_id(attachment["ticket_id"])
            if not token or token != public_token:
                self.redirect("/dp/login")
                return
        if not can_download_attachment(attachment, user, token):
            return self.handle_not_found()

        with open(file_path, "rb") as file:
            body = file.read()
        self.send_response(200)
        self.add_common_headers()
        self.send_header("Content-Type", attachment["content_type"] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{attachment["original_name"]}"')
        self.end_headers()
        if self.response_body_allowed():
            self.wfile.write(body)

    def handle_ti_ticket_csv_export(self, path: str) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        try:
            ticket_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_not_found()
        try:
            file_name, body = build_admission_csv_export(ticket_id)
        except ValueError:
            return self.handle_not_found()
        except FileNotFoundError as exc:
            logger.error("Template CSV não encontrado para exportação: %s", exc)
            csrf_token, csrf_cookie = self.ensure_csrf()
            return self.respond_html(
                error_page("Template CSV não encontrado", "O arquivo modelo da exportação não está disponível no servidor.", csrf_token=csrf_token),
                500,
                csrf_cookie,
            )
        self.send_response(200)
        self.add_common_headers()
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{file_name}"')
        self.end_headers()
        if self.response_body_allowed():
            self.wfile.write(body)

    def handle_ticket_submit(self) -> None:
        user = self.require_auth()
        if not user:
            return
        form, attachments = self.parse_form()
        if not form.get("requester_name", "").strip():
            form["requester_name"] = (user["name"] or user["email"] or "").strip()
        if not self.user_can_access_category(user, form.get("category_id", "").strip()):
            return self.handle_public_form(
                message="Você não tem acesso a essa origem de solicitação.",
                kind="error",
                form_data=form,
            )
        if not self.verify_csrf(form):
            return self.handle_public_form(message="Não foi possível validar a solicitação. Atualize a página e tente novamente.", kind="error", form_data=form)
        normalized, errors, ticket_id = create_ticket(form, attachments, user)
        if errors:
            return self.handle_public_form(message="Revise os campos destacados e tente novamente.", kind="error", form_data=normalized, errors=errors)
        public_token = get_public_ticket_token_by_id(ticket_id) if ticket_id else None
        if ticket_id:
            send_ticket_confirmation(ticket_id)
            send_ticket_opened_notification(ticket_id)
        self.handle_public_form(
            params={"category_id": [form.get("category_id", "")]},
            message="Solicitação enviada com sucesso. O Departamento Pessoal vai acompanhar o atendimento.",
            kind="success",
            form_data={},
            errors={},
            created_ticket_id=ticket_id,
            public_token=public_token,
        )

    def handle_login_page(self, message: str = "", kind: str = "info", next_path: str = "") -> None:
        next_path = safe_next_path(next_path)
        if self.current_user():
            return self.redirect(next_path or "/")
        csrf_token, csrf_cookie = self.ensure_csrf()
        next_field = f'<input type="hidden" name="next" value="{html.escape(next_path, quote=True)}">' if next_path else ""
        form = f"""
        <form class="ticket-form" method="post" action="/dp/login">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
          {next_field}
          <label><span>E-mail</span><input name="email" type="email" required></label>
          <label><span>Senha</span><input name="password" type="password" required></label>
          <div class="form-actions">
            <button class="button button--primary" type="submit">Entrar</button>
            <button class="button button--secondary" type="submit" formaction="/esqueci-senha" formnovalidate>Redefinir senha</button>
          </div>
          <p class="help-text">Para redefinir a senha, digite o e-mail cadastrado no campo acima e clique em "Redefinir senha". Você receberá um link por e-mail.</p>
        </form>
        """
        self.respond_html(auth_page("Login", "Acesse o portal para abrir solicitações ou operar a área interna do DP.", form, csrf_token, message, kind), set_cookie=csrf_cookie)

    def handle_login_submit(self) -> None:
        form, _ = self.parse_form()
        ip = parse_client_ip(self)
        next_path = safe_next_path(form.get("next", ""))
        if not self.verify_csrf(form):
            return self.handle_login_page("Sessão do formulário expirada. Atualize a tela e tente novamente.", "error", next_path=next_path)
        if is_login_rate_limited(ip):
            logger.warning("Login bloqueado por rate limit para IP %s", ip)
            return self.handle_login_page("Não foi possível concluir o login neste momento. Aguarde alguns minutos e tente novamente.", "error", next_path=next_path)
        user = authenticate_user(form.get("email", "").lower(), form.get("password", ""))
        if not user:
            register_login_attempt(ip, success=False)
            return self.handle_login_page("Não foi possível concluir o login neste momento. Verifique suas credenciais.", "error", next_path=next_path)
        register_login_attempt(ip, success=True)
        token = create_session(user["id"])
        session_cookie = build_cookie(SESSION_COOKIE, token, secure=USE_SECURE_COOKIE)
        csrf_cookie = build_cookie(CSRF_COOKIE, generate_csrf_token(), secure=USE_SECURE_COOKIE, http_only=False)
        self.redirect(next_path or "/", set_cookie=[session_cookie, csrf_cookie])

    def handle_password_reset_request_page(self, message: str = "", kind: str = "info", form_data: dict[str, str] | None = None) -> None:
        if self.current_user():
            return self.redirect("/")
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(password_reset_request_page(csrf_token, message, kind, form_data), set_cookie=csrf_cookie)

    def handle_password_reset_request_submit(self) -> None:
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_login_page("Sessão do formulário expirada. Atualize a tela e tente novamente.", "error")
        email = form.get("email", "").strip()
        if not email:
            return self.handle_login_page(
                'Digite o e-mail cadastrado no campo acima e clique em "Redefinir senha".',
                "error",
            )
        reset_id = create_password_reset_request(email)
        if reset_id:
            send_password_reset_email(reset_id)
        self.handle_login_page(
            "Se o e-mail estiver cadastrado, enviamos um link para redefinir sua senha. O link expira em 2 horas.",
            "success",
        )

    def handle_password_reset_page(self, reset_token: str, message: str = "", kind: str = "info", form_data: dict[str, str] | None = None) -> None:
        if self.current_user():
            return self.redirect("/")
        reset = get_password_reset(reset_token)
        csrf_token, csrf_cookie = self.ensure_csrf()
        if not reset:
            return self.respond_html(
                error_page("Link inválido", "Esse link de redefinição não existe ou já expirou. Solicite um novo na tela de login.", csrf_token=csrf_token),
                404,
                csrf_cookie,
            )
        self.respond_html(password_reset_page(reset, csrf_token, message, kind, form_data), set_cookie=csrf_cookie)

    def handle_password_reset_submit(self, reset_token: str) -> None:
        if self.current_user():
            return self.redirect("/")
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_password_reset_page(reset_token, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error", form)
        success, message = reset_user_password(reset_token, form)
        if not success:
            return self.handle_password_reset_page(reset_token, message, "error", form)
        self.handle_login_page("Senha redefinida. Entre com seu e-mail e a nova senha.", "success")

    def handle_logout(self) -> None:
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_login_page("Sessão expirada. Atualize a página e tente novamente.", "error")
        delete_session(self.get_cookie_value(SESSION_COOKIE))
        self.redirect(
            "/dp/login",
            set_cookie=[
                build_cookie(SESSION_COOKIE, "", max_age=0, secure=USE_SECURE_COOKIE),
                build_cookie(CSRF_COOKIE, "", max_age=0, secure=USE_SECURE_COOKIE, http_only=False),
            ],
        )

    def handle_setup_page(self, message: str = "", kind: str = "info") -> None:
        if has_users():
            return self.redirect("/dp/login")
        csrf_token, csrf_cookie = self.ensure_csrf()
        form = f"""
        <form class="ticket-form" method="post" action="/dp/setup">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
          <label><span>Nome</span><input name="name" type="text" required></label>
          <label><span>E-mail</span><input name="email" type="email" required></label>
          <label><span>Setor</span><input name="department" type="text" value="DP"></label>
          <label><span>Senha</span><input name="password" type="password" required></label>
          <div class="form-actions">
            <button class="button button--primary" type="submit">Criar primeiro acesso</button>
          </div>
        </form>
        """
        self.respond_html(auth_page("Primeiro acesso DP", "Crie o primeiro usuário interno do Departamento Pessoal.", form, csrf_token, message, kind), set_cookie=csrf_cookie)

    def handle_setup_submit(self) -> None:
        if has_users():
            return self.redirect("/dp/login")
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_setup_page("Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        _, errors = create_user(form, initial_setup=True)
        if errors:
            return self.handle_setup_page(next(iter(errors.values())), "error")
        self.handle_login_page("Primeiro usuário do DP criado. Entre com suas credenciais.", "success")

    def handle_ti_dashboard(self, params: dict[str, list[str]], message: str = "", kind: str = "info") -> None:
        user = self.require_dp_auth()
        if not user:
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        active_tab = normalize_tab(params.get("tab", ["abertos"])[0])
        expanded_ticket_raw = params.get("ticket", [""])[0].strip()
        expanded_ticket_id = int(expanded_ticket_raw) if expanded_ticket_raw.isdigit() else None
        filters = get_ticket_filters({
            "category_id": params.get("category_id", [""])[0],
            "assigned_to": params.get("assigned_to", [""])[0],
            "folha_owner": params.get("folha_owner", [""])[0],
            "q": params.get("q", [""])[0],
            "sort": params.get("sort", ["recentes"])[0],
        })
        tickets, summary, events_by_ticket = get_dashboard_data(active_tab, filters)
        self.respond_html(
            render_ti_dashboard(user, tickets, events_by_ticket, summary, active_tab, csrf_token, list_categories(), list_ti_users(active_only=True), filters, expanded_ticket_id, message, kind),
            set_cookie=csrf_cookie,
        )

    def handle_ti_ticket_update(self, path: str) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        form, attachments = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        try:
            ticket_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Chamado inválido.", "error")
        success, msg = update_ticket(ticket_id, form, user["id"], attachments)
        params = {
            "tab": [normalize_tab(form.get("active_tab", "todos"))],
            "category_id": [form.get("category_id", "")],
            "assigned_to": [form.get("assigned_to", "")],
"folha_owner": [form.get("folha_owner", "")],
            "q": [form.get("q", "")],
            "sort": [form.get("sort", "recentes")],
            "ticket": [form.get("ticket", "")],
        }
        self.handle_ti_dashboard(params, msg, "success" if success else "error" if "expirada" not in msg and "Nenhuma" not in msg else "info")

    def handle_ti_ticket_delete(self, path: str) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        try:
            ticket_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Chamado inválido.", "error")
        success, msg = delete_ticket(ticket_id)
        params = {
            "tab": [normalize_tab(form.get("active_tab", "todos"))],
            "category_id": [form.get("category_id", "")],
            "assigned_to": [form.get("assigned_to", "")],
"folha_owner": [form.get("folha_owner", "")],
            "q": [form.get("q", "")],
            "sort": [form.get("sort", "recentes")],
        }
        self.handle_ti_dashboard(params, msg, "success" if success else "error")

    def handle_ti_users(
        self,
        message: str = "",
        kind: str = "info",
        invite_form_data: dict[str, str] | None = None,
        invite_message: str = "",
        invite_kind: str = "info",
        invite_open: bool = False,
    ) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(
            render_ti_users(
                user,
                list_ti_users(),
                csrf_token,
                message,
                kind,
                invite_form_data,
                invite_message,
                invite_kind,
                invite_open,
            ),
            set_cookie=csrf_cookie,
        )

    # ------------------------------------------------------------------
    # API interna do worker que cria tarefas no SharePoint.
    # Token obrigatorio; nunca pelo dominio publico (bloqueia headers da Cloudflare).
    # ------------------------------------------------------------------
    def internal_auth_ok(self) -> bool:
        expected = os.environ.get("PORTALDP_INTERNAL_TOKEN", "")
        if not expected:
            return False
        if self.headers.get("Cf-Ray") or self.headers.get("Cf-Connecting-Ip"):
            return False
        header = self.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > 65536:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def handle_internal_get(self, path: str) -> None:
        if not self.internal_auth_ok():
            return self.respond_json({"error": "not found"}, 404)
        parts = path.strip("/").split("/")
        if parts == ["interno", "sharepoint", "pendentes"]:
            return self.respond_json({"requests": list_pending_sharepoint_requests()})
        if len(parts) == 5 and parts[:2] == ["interno", "sharepoint"] and parts[3] == "anexos" and parts[2].isdigit() and parts[4].isdigit():
            request_id, attachment_id = int(parts[2]), int(parts[4])
            if not sharepoint_request_has_attachment(request_id, attachment_id):
                return self.respond_json({"error": "not found"}, 404)
            attachment, file_path = get_ticket_attachment(attachment_id)
            if not attachment or not file_path or not file_path.exists():
                return self.respond_json({"error": "not found"}, 404)
            body = file_path.read_bytes()
            self.send_response(200)
            self.add_common_headers()
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.response_body_allowed():
                self.wfile.write(body)
            return
        self.respond_json({"error": "not found"}, 404)

    def handle_internal_post(self, path: str) -> None:
        if not self.internal_auth_ok():
            return self.respond_json({"error": "not found"}, 404)
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["interno", "sharepoint"] and parts[2].isdigit():
            request_id = int(parts[2])
            if parts[3] == "iniciar":
                claimed = claim_sharepoint_request(request_id)
                if not claimed:
                    return self.respond_json({"error": "conflict"}, 409)
                return self.respond_json(claimed)
            if parts[3] == "resultado":
                body = self.read_json_body()
                task_id = body.get("task_id")
                ok, msg = finish_sharepoint_request(
                    request_id,
                    str(body.get("status", "")),
                    int(task_id) if isinstance(task_id, int) else None,
                    str(body.get("message", "")),
                )
                return self.respond_json({"ok": ok, "message": msg}, 200 if ok else 400)
        self.respond_json({"error": "not found"}, 404)

    def handle_sharepoint_resend(self, path: str) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        try:
            ticket_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Chamado inválido.", "error")
        success, msg = requeue_sharepoint_request(ticket_id)
        self.handle_ti_dashboard(
            {"tab": [normalize_tab(form.get("active_tab", "todos"))], "ticket": [str(ticket_id)]},
            msg,
            "success" if success else "error",
        )

    def handle_pix_search(self, query: str) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        params = parse_qs(query)
        try:
            category_id = int(params.get("category_id", [""])[0])
        except ValueError:
            return self.respond_json({"results": []}, 400)
        category = next((row for row in list_categories() if row["id"] == category_id), None)
        if not category or category["name"].strip().lower() not in PIX_ORIGIN_NAMES:
            return self.respond_json({"results": []}, 404)
        self.respond_json({"results": search_pix_keys(category_id, params.get("q", [""])[0])})

    def handle_pix_management(
        self,
        category_id: str = "",
        q: str = "",
        message: str = "",
        kind: str = "info",
        add_form: dict[str, str] | None = None,
    ) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        filters = {"category_id": category_id.strip(), "q": q.strip()}
        self.respond_html(
            render_pix_management(
                user, pix_origin_categories(), list_pix_keys(filters["category_id"], filters["q"]), filters, csrf_token, message, kind, add_form
            ),
            set_cookie=csrf_cookie,
        )

    def handle_pix_management_submit(self, path: str) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        form, _ = self.parse_form()
        category_id, q = form.get("filter_category_id", ""), form.get("filter_q", "")
        if not self.verify_csrf(form):
            return self.handle_pix_management(category_id, q, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        parts = path.strip("/").split("/")
        if parts == ["dp", "pix", "adicionar"]:
            success, message = create_pix_key(form, user["id"])
            return self.handle_pix_management(category_id, q, message, "success" if success else "error", None if success else form)
        if len(parts) == 4 and parts[2].isdigit() and parts[3] == "atualizar":
            success, message = update_pix_key(int(parts[2]), form, user["id"])
        elif len(parts) == 4 and parts[2].isdigit() and parts[3] == "excluir":
            success, message = delete_pix_key(int(parts[2]), user["id"])
        else:
            return self.handle_not_found()
        self.handle_pix_management(category_id, q, message, "success" if success else "error")

    def handle_company_management(self, message: str = "", kind: str = "info") -> None:
        user = self.require_dp_auth()
        if not user:
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        agents = [row for row in list_ti_users(active_only=True) if row["role"] == "dp"]
        self.respond_html(
            render_company_management(user, list_categories(), agents, list_category_owners(), csrf_token, message, kind),
            set_cookie=csrf_cookie,
        )

    def handle_company_owners_submit(self, path: str) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_company_management("Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        try:
            category_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_company_management("Empresa inválida.", "error")
        owner_raw = form.get("owner", "").strip()
        if owner_raw and not owner_raw.isdigit():
            return self.handle_company_management("Responsável inválido.", "error")
        success, message = set_category_owner(category_id, int(owner_raw) if owner_raw else None)
        self.handle_company_management(message, "success" if success else "error")

    def handle_ti_users_submit(self) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_users("Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        _, errors = create_user(form, current_user_id=user["id"])
        if errors:
            return self.handle_ti_users(next(iter(errors.values())), "error")
        self.handle_ti_users("Usuário salvo com sucesso.", "success")

    def handle_ti_user_update(self, path: str) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_users("Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        try:
            user_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_ti_users("Usuário inválido.", "error")
        success, message = update_user(user_id, form, user["id"])
        self.handle_ti_users(message, "success" if success else "error")

    def handle_public_ticket(self, public_token: str) -> None:
        ticket = get_public_ticket(public_token)
        if not ticket:
            return self.handle_not_found()
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(
            public_ticket_page(ticket, get_public_ticket_events(ticket["id"]), list_ticket_attachments(ticket["id"], include_hidden=False), csrf_token, True, logged_in=bool(self.current_user())),
            set_cookie=csrf_cookie,
        )

    def handle_public_ticket_comment(self, path: str) -> None:
        user = self.current_user()
        public_token = path.strip("/").split("/", 2)[1]
        ticket = get_public_ticket(public_token)
        if not ticket:
            return self.handle_not_found()
        if not user:
            return self.redirect(f"/dp/login?next=/acompanhar/{quote(public_token)}")
        form, attachments = self.parse_form()
        if not self.verify_csrf(form):
            csrf_token, csrf_cookie = self.ensure_csrf()
            return self.respond_html(
                public_ticket_page(ticket, get_public_ticket_events(ticket["id"]), list_ticket_attachments(ticket["id"], include_hidden=False), csrf_token, True, "Sessão expirada. Atualize a página e tente novamente.", "error", logged_in=True),
                set_cookie=csrf_cookie,
            )
        success, msg = add_requester_comment(public_token, user, form.get("comment", ""), attachments)
        csrf_token, csrf_cookie = self.ensure_csrf()
        ticket = get_public_ticket(public_token)
        self.respond_html(
            public_ticket_page(ticket, get_public_ticket_events(ticket["id"]), list_ticket_attachments(ticket["id"], include_hidden=False), csrf_token, True, msg, "success" if success else "error", logged_in=True),
            set_cookie=csrf_cookie,
        )

    def handle_ti_user_invite_submit(self) -> None:
        user = self.require_dp_auth()
        if not user:
            return
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            message = "Sessão do formulário expirada. Atualize a página e tente novamente."
            source = form.get("source", "users")
            if source == "dashboard":
                params = {
                    "tab": [normalize_tab(form.get("active_tab", "todos"))],
                    "category_id": [form.get("category_id", "")],
                    "assigned_to": [form.get("assigned_to", "")],
"folha_owner": [form.get("folha_owner", "")],
                    "q": [form.get("q", "")],
                    "sort": [form.get("sort", "recentes")],
                }
                return self.handle_ti_dashboard(params, message, "error")
            return self.handle_ti_users(invite_message=message, invite_kind="error", invite_open=True, invite_form_data=form)
        normalized, errors, invite_id = create_user_invite(form, user["id"])
        if errors or not invite_id:
            source = form.get("source", "users")
            message = next(iter(errors.values()))
            if source == "dashboard":
                params = {
                    "tab": [normalize_tab(form.get("active_tab", "todos"))],
                    "category_id": [form.get("category_id", "")],
                    "assigned_to": [form.get("assigned_to", "")],
"folha_owner": [form.get("folha_owner", "")],
                    "q": [form.get("q", "")],
                    "sort": [form.get("sort", "recentes")],
                }
                return self.handle_ti_dashboard(params, message, "error")
            return self.handle_ti_users(invite_form_data=normalized, invite_message=message, invite_kind="error", invite_open=True)
        send_user_invite_email(invite_id)
        source = form.get("source", "users")
        if source == "dashboard":
            params = {
                "tab": [normalize_tab(form.get("active_tab", "todos"))],
                "category_id": [form.get("category_id", "")],
                "assigned_to": [form.get("assigned_to", "")],
"folha_owner": [form.get("folha_owner", "")],
                "q": [form.get("q", "")],
                "sort": [form.get("sort", "recentes")],
            }
            return self.handle_ti_dashboard(params, "Convite enviado com sucesso.", "success")
        self.handle_ti_users(invite_message="Convite enviado com sucesso.", invite_kind="success")

    def handle_invite_accept_page(self, invite_token: str, message: str = "", kind: str = "info", form_data: dict[str, str] | None = None) -> None:
        invite = get_user_invite(invite_token)
        if not invite:
            csrf_token, csrf_cookie = self.ensure_csrf()
            return self.respond_html(error_page("Convite inválido", "Esse convite não existe ou já expirou.", csrf_token=csrf_token), 404, csrf_cookie)
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(invite_accept_page(invite, csrf_token, message, kind, form_data), set_cookie=csrf_cookie)

    def handle_invite_accept_submit(self, invite_token: str) -> None:
        form, _ = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_invite_accept_page(invite_token, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error", form)
        success, message = accept_user_invite(invite_token, form)
        if not success:
            return self.handle_invite_accept_page(invite_token, message, "error", form)
        self.handle_login_page("Cadastro concluído. Entre com seu e-mail e senha.", "success")

def main() -> None:
    configure_logging()
    ensure_schema()
    cleanup_expired_sessions()
    if not SMTP_HOST:
        logger.warning("SMTP não configurado. Convites e confirmações serão gravados no outbox local.")
    backup_path = make_backup(test=APP_ENV == "test")
    logger.info("Backup de inicialização gerado em %s", backup_path)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AppHandler)
    print(f"Servidor iniciado em http://localhost:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
