from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import secrets
import sqlite3
import unicodedata
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app_config import (
    ATTACHMENTS_PATH,
    ATTACHMENT_MAX_BYTES,
    ATTACHMENT_MAX_COUNT,
    APP_BASE_URL,
    APP_TIMEZONE,
    BACKUP_DIR,
    BASE_DIR,
    DB_BACKEND,
    DB_PATH,
    LOGIN_RATE_LIMIT_ATTEMPTS,
    LOGIN_RATE_LIMIT_WINDOW,
    MAIL_FROM,
    MAIL_OUTBOX_PATH,
    MAIL_REPLY_TO,
    SESSION_DAYS,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_USE_SSL,
    SMTP_USE_TLS,
    TEST_BACKUP_DIR,
    TICKET_OPENED_NOTIFY_EMAILS,
    TRUSTED_PROXY_IPS,
)
from app_db import DBIntegrityError, get_db, logger


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_TICKET_STATUSES = ("Aberto", "Em analise", "Em atendimento", "Aguardando retorno", "Concluido", "Cancelado")
VALID_TABS = {"abertos", "andamento", "concluidos", "todos"}
ALLOWED_SORTS = {"recentes", "antigos"}
DEFAULT_TICKET_PRIORITY = "Media"
VALID_USER_ROLES = {"dp", "company", "beta", "alfa", "ambos", "gama"}
EMPLOYEE_REGISTRATION_MAX = 30
RESCISSION_EVENT_FIELD_RE = re.compile(r"rescission_event_(\d{1,4})_(?:name|value)")
RESCISSION_NAME_MAX = 120
RESCISSION_EVENT_NAME_MAX = 120
RESCISSION_EVENT_VALUE_MAX = 40
RESCISSION_EVENTS_MAX_COUNT = 30
REQUEST_CATEGORY_OPTIONS = ("Admissão", "Folha mensal", "Férias", "Rescisão", "Processos trabalhistas", "Outros")
RESCISSION_TYPE_OPTIONS = (
    "Demissão aviso indenizado",
    "Demissão aviso trabalhado",
    "Demissão por justa causa",
    "Pedido de demissão",
    "Acordo mútuo",
    "Término de contrato",
    "Término de contrato antecipado",
)
REQUEST_CATEGORY_ALIASES = {
    "Admissao": "Admissão",
    "Ferias": "Férias",
    "Rescisao": "Rescisão",
}
ADMISSION_COMPANY_SIGLA_MAP = {
    "Beta": "Beta",
    "Beta Filial": "Beta Filial",
    "Alfa Matriz": "Alfa Matriz",
    "Alfa SPE 1": "S22",
    "Alfa SPE 2": "T33",
}
ADMISSION_CSV_TEMPLATE_PATH = BASE_DIR / "FUNCIONARIOPADRAO (2).csv"
ADMISSION_PAYLOAD_VERSION = 1
YES_NO_OPTIONS = ("Sim", "Não")
BRAZILIAN_STATE_FIELD_NAMES = {
    "employee_state",
    "employee_birth_state",
    "employee_rg_issuer_state",
    "employee_work_card_state",
}
try:
    DISPLAY_TIMEZONE = ZoneInfo(APP_TIMEZONE)
except ZoneInfoNotFoundError:
    DISPLAY_TIMEZONE = timezone.utc
    logger.warning("APP_TIMEZONE invalido: %s. Usando UTC.", APP_TIMEZONE)

ADMISSION_FIELD_SPECS = (
    {"name": "employee_name", "label": "Nome do funcionário", "required": True, "type": "text"},
    {"name": "employee_cpf", "label": "CPF", "required": True, "type": "text"},
    {"name": "employee_birth_date", "label": "Data de nascimento", "required": True, "type": "date"},
    {"name": "employee_company_unit_type", "label": "Empresa", "required": False, "type": "select", "options": ("Matriz", "Filial")},
    {"name": "employee_worksite", "label": "Obra", "required": False, "type": "text"},
    {"name": "employee_role", "label": "Função", "required": False, "type": "text"},
    {"name": "employee_cbo", "label": "CBO", "required": False, "type": "text"},
    {"name": "employee_department", "label": "Departamento", "required": False, "type": "text"},
    {"name": "employee_cost_center", "label": "Centro de custo", "required": False, "type": "text"},
    {"name": "employee_admission_date", "label": "Data de admissão", "required": True, "type": "date"},
    {"name": "employee_salary", "label": "Salário", "required": True, "type": "text"},
    {"name": "employee_work_schedule", "label": "Horário de trabalho", "required": False, "type": "text"},
    {"name": "employee_work_interval", "label": "Intervalo", "required": False, "type": "text"},
    {
        "name": "employee_experience_contract",
        "label": "Contrato de experiência",
        "required": False,
        "type": "select",
        "options": ("30/60", "45/45", "60/30", "Outro"),
    },
    {
        "name": "employee_experience_contract_other",
        "label": "Outro contrato de experiência",
        "required": False,
        "type": "text",
        "show_when": {"employee_experience_contract": "Outro"},
        "required_when": {"employee_experience_contract": "Outro"},
    },
    {
        "name": "employee_advance_opt_in",
        "label": "Optante pelo adiantamento",
        "required": False,
        "type": "select",
        "options": YES_NO_OPTIONS,
    },
    {
        "name": "employee_insalubrity",
        "label": "Insalubridade",
        "required": False,
        "type": "select",
        "options": YES_NO_OPTIONS,
    },
    {
        "name": "employee_insalubrity_percentage",
        "label": "Porcentagem de insalubridade",
        "required": False,
        "type": "select",
        "options": ("10%", "20%", "40%"),
        "show_when": {"employee_insalubrity": "Sim"},
        "required_when": {"employee_insalubrity": "Sim"},
    },
    {
        "name": "employee_hazard_pay",
        "label": "Periculosidade",
        "required": False,
        "type": "select",
        "options": YES_NO_OPTIONS,
    },
    {
        "name": "employee_advance_enabled",
        "label": "Adiantamento",
        "required": False,
        "type": "select",
        "options": YES_NO_OPTIONS,
    },
    {
        "name": "employee_transport_voucher",
        "label": "Vale transporte",
        "required": False,
        "type": "select",
        "options": YES_NO_OPTIONS,
    },
    {
        "name": "employee_transport_voucher_rule",
        "label": "Regra VT",
        "required": False,
        "type": "text",
    },
    {
        "name": "employee_meal_voucher",
        "label": "Vale alimentação",
        "required": False,
        "type": "select",
        "options": YES_NO_OPTIONS,
    },
    {
        "name": "employee_meal_voucher_rule",
        "label": "Regra VA",
        "required": False,
        "type": "text",
    },
    {
        "name": "employee_ethnicity",
        "label": "Etnia/Raça",
        "required": True,
        "type": "select",
        "options": ("Branco", "Preto", "Pardo", "Amarelo", "Indígena"),
    },
    {
        "name": "employee_gender",
        "label": "Sexo",
        "required": True,
        "type": "select",
        "options": ("Feminino", "Masculino"),
    },
    {
        "name": "employee_education",
        "label": "Grau de formação",
        "required": True,
        "type": "select",
        "options": (
            "Analfabeto",
            "Até 5ª ano incompleto",
            "5ª ano completo",
            "Fundamental Incompleto",
            "Fundamental Completo",
            "Ensino médio Incompleto",
            "Ensino médio Completo",
            "Superior Incompleto",
            "Superior Completo",
        ),
    },
    {"name": "employee_zip_code", "label": "CEP", "required": True, "type": "text"},
    {"name": "employee_address", "label": "Endereço", "required": False, "type": "text"},
    {"name": "employee_address_number", "label": "Número", "required": False, "type": "text"},
    {"name": "employee_city", "label": "Município", "required": False, "type": "text"},
    {"name": "employee_neighborhood", "label": "Bairro", "required": False, "type": "text"},
    {"name": "employee_state", "label": "UF", "required": False, "type": "text"},
    {"name": "employee_phone", "label": "Telefone", "required": True, "type": "text"},
    {"name": "employee_father_name", "label": "Nome do pai", "required": False, "type": "text"},
    {"name": "employee_mother_name", "label": "Nome da mãe", "required": True, "type": "text"},
    {"name": "employee_birth_city", "label": "Cidade onde nasceu", "required": True, "type": "text"},
    {"name": "employee_birth_state", "label": "UF de nascimento", "required": True, "type": "text"},
    {"name": "employee_marital_status", "label": "Estado civil", "required": False, "type": "text"},
    {"name": "employee_pis", "label": "PIS", "required": False, "type": "text"},
    {"name": "employee_rg", "label": "RG", "required": False, "type": "text"},
    {"name": "employee_rg_issuer", "label": "Órgão expedidor", "required": False, "type": "text"},
    {"name": "employee_rg_issuer_state", "label": "UF do RG", "required": False, "type": "text"},
    {"name": "employee_rg_issue_date", "label": "Data de expedição do RG", "required": True, "type": "date"},
    {
        "name": "employee_work_card_type",
        "label": "Carteira de trabalho",
        "required": False,
        "type": "select",
        "options": ("Física", "Digital"),
    },
    {
        "name": "employee_work_card_number",
        "label": "N° da carteira",
        "required": False,
        "type": "text",
        "show_when": {"employee_work_card_type": "Física"},
    },
    {
        "name": "employee_work_card_series",
        "label": "Série da carteira",
        "required": False,
        "type": "text",
        "show_when": {"employee_work_card_type": "Física"},
    },
    {
        "name": "employee_work_card_state",
        "label": "UF da carteira",
        "required": False,
        "type": "text",
        "show_when": {"employee_work_card_type": "Física"},
    },
    {
        "name": "employee_work_card_issue_date",
        "label": "Data de emissão da carteira",
        "required": False,
        "type": "date",
        "show_when": {"employee_work_card_type": "Física"},
    },
    {"name": "employee_voter_id", "label": "Título de eleitor", "required": False, "type": "text"},
    {"name": "employee_voter_zone", "label": "Zona", "required": False, "type": "text"},
    {"name": "employee_voter_section", "label": "Seção", "required": False, "type": "text"},
    {
        "name": "employee_has_alimony",
        "label": "Terá pensão alimentícia",
        "required": False,
        "type": "select",
        "options": YES_NO_OPTIONS,
    },
    {
        "name": "employee_alimony_percentage",
        "label": "Percentual aplicar",
        "required": False,
        "type": "text",
        "show_when": {"employee_has_alimony": "Sim"},
    },
    {
        "name": "employee_alimony_beneficiary_name",
        "label": "Nome beneficiário",
        "required": False,
        "type": "text",
        "show_when": {"employee_has_alimony": "Sim"},
    },
    {
        "name": "employee_alimony_beneficiary_cpf",
        "label": "CPF do beneficiário",
        "required": False,
        "type": "text",
        "show_when": {"employee_has_alimony": "Sim"},
    },
    {
        "name": "employee_alimony_bank",
        "label": "Banco do depósito",
        "required": False,
        "type": "text",
        "show_when": {"employee_has_alimony": "Sim"},
    },
    {
        "name": "employee_alimony_agency",
        "label": "Agência do depósito",
        "required": False,
        "type": "text",
        "show_when": {"employee_has_alimony": "Sim"},
    },
    {
        "name": "employee_alimony_account",
        "label": "Conta do depósito",
        "required": False,
        "type": "text",
        "show_when": {"employee_has_alimony": "Sim"},
    },
    {
        "name": "employee_bank_name",
        "label": "Banco",
        "required": True,
        "type": "text",
    },
    {
        "name": "employee_bank_account_type",
        "label": "Tipo de conta",
        "required": True,
        "type": "select",
        "options": ("Corrente", "Salário", "Poupança"),
    },
    {
        "name": "employee_bank_account_number",
        "label": "Número da conta",
        "required": True,
        "type": "text",
    },
    {
        "name": "employee_bank_agency",
        "label": "Agência bancária",
        "required": True,
        "type": "text",
    },
    {
        "name": "employee_pix_key",
        "label": "Chave Pix",
        "required": True,
        "type": "text",
    },
    {
        "name": "employee_pix_key_type",
        "label": "Tipo da chave Pix",
        "required": True,
        "type": "select",
        "options": ("CPF", "Telefone", "E-mail", "Aleatória"),
    },
    {"name": "employee_notes", "label": "Observações", "required": False, "type": "textarea"},
)
ADMISSION_FIELD_SPECS_BY_NAME = {field["name"]: field for field in ADMISSION_FIELD_SPECS}
ADMISSION_FIELD_LABELS = {field["label"]: field["name"] for field in ADMISSION_FIELD_SPECS}
DEPENDENT_FIELD_SPECS = (
    {"name": "name", "label": "Nome", "required": True, "type": "text"},
    {"name": "cpf", "label": "CPF", "required": True, "type": "text"},
    {"name": "birth_date", "label": "Data de nascimento", "required": True, "type": "date"},
    {
        "name": "relationship",
        "label": "Tipo de dependente",
        "required": True,
        "type": "select",
        "options": ("Cônjuge", "Filho(a) ou enteado(a)"),
    },
    {
        "name": "ir_dependent",
        "label": "Será dependente para IR?",
        "required": True,
        "type": "select",
        "options": ("Sim", "Não"),
    },
    {"name": "birth_city", "label": "Cidade em que nasceu", "required": False, "type": "text"},
    {"name": "certificate_registration", "label": "Matrícula certidão", "required": False, "type": "text"},
    {"name": "registry_number", "label": "Número registro cartório", "required": False, "type": "text"},
    {"name": "registry_book", "label": "Livro", "required": False, "type": "text"},
    {"name": "registry_page", "label": "Folha", "required": False, "type": "text"},
)
DEPENDENT_FIELD_LABELS = {field["label"]: field["name"] for field in DEPENDENT_FIELD_SPECS}
VACATION_FIELD_SPECS = (
    {"name": "vacation_registration", "label": "Matrícula", "required": False, "type": "text", "max_length": EMPLOYEE_REGISTRATION_MAX},
    {"name": "vacation_employee_name", "label": "Nome do funcionário", "required": True, "type": "text"},
    {"name": "vacation_start_date", "label": "Data de início", "required": True, "type": "date"},
    {"name": "vacation_days", "label": "Dias de férias", "required": True, "type": "number"},
    {
        "name": "vacation_has_bonus",
        "label": "Abono de férias",
        "required": True,
        "type": "select",
        "options": ("Não", "Sim"),
    },
    {
        "name": "vacation_bonus_days",
        "label": "Dias de abono",
        "required": False,
        "type": "number",
        "show_when": {"vacation_has_bonus": "Sim"},
        "required_when": {"vacation_has_bonus": "Sim"},
    },
)
TICKET_STATUS_LABELS = {
    "Aberto": "Aberto",
    "Em analise": "Em análise",
    "Em atendimento": "Em atendimento",
    "Aguardando retorno": "Aguardando retorno",
    "Concluido": "Concluído",
    "Cancelado": "Cancelado",
}


TICKET_CC_RECIPIENTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "alfa": (
        ("alfa_ana_souza", "ana.souza@alfa.example.com", "Ana Souza"),
        ("alfa_bruna_lima", "bruna.lima@alfa.example.com", "Bruna Lima"),
        ("alfa_carla_rocha", "carla.rocha@alfa.example.com", "Carla Rocha"),
        ("alfa_diana_alves", "diana.alves@alfa.example.com", "Diana Alves"),
    ),
    "beta": (
        ("beta_elisa_moura", "elisa.moura@alfa.example.com", "Elisa Moura"),
        ("beta_fabia_nunes", "fabia.nunes@beta.example.com", "Fábia Nunes"),
        ("beta_gabi_costa", "gabi.costa@beta.example.com", "Gabi Costa"),
    ),
    "gama": (
        ("gama_hugo_reis", "hugo.reis@gama.example.com", "Hugo Reis"),
    ),
    "ambas": (
        ("ambas_iris_melo", "iris.melo@beta.example.com", "Íris Melo"),
        ("ambas_joao_pinto", "joao.pinto@beta.example.com", "João Pinto"),
        ("ambas_karen", "karen@example.com", "Karen"),
        ("ambas_laura_dias", "laura.dias@example.com", "Laura Dias"),
        ("ambas_marta", "marta@example.com", "Marta"),
        ("ambas_nuno_silva", "nuno.silva@example.com", "Nuno Silva"),
    ),
}


# dp@ vai em copia em TODA notificacao de atualizacao, de qualquer empresa
TICKET_CC_ALWAYS: tuple[str, ...] = ("dp@example.com",)


def ticket_cc_options(category_name: str | None) -> tuple[tuple[str, str, str], ...]:
    normalized = (category_name or "").strip().lower()
    if normalized == "gama":
        return TICKET_CC_RECIPIENTS["gama"]
    if normalized == "alfa":
        return TICKET_CC_RECIPIENTS["alfa"] + TICKET_CC_RECIPIENTS["ambas"]
    if normalized == "beta":
        return TICKET_CC_RECIPIENTS["beta"] + TICKET_CC_RECIPIENTS["ambas"]
    return TICKET_CC_RECIPIENTS["ambas"]


def ticket_cc_emails_from_form(category_name: str | None, form: dict[str, str]) -> list[str]:
    emails: list[str] = list(TICKET_CC_ALWAYS)
    for slug, email, _label in ticket_cc_options(category_name):
        if form.get(f"cc_{slug}") == "1" and email not in emails:
            emails.append(email)
    return emails


GAMA_COMPANIES = ("Matriz", "Filial 1 ES", "Filial 2 SC", "Filial 4 SP")


def company_options_for_category(category_name: str | None) -> tuple[str, ...]:
    normalized = (category_name or "").strip().lower()
    if normalized == "alfa":
        return ("Alfa Matriz", "Alfa SPE 1", "Alfa SPE 2")
    if normalized == "beta":
        return ("Beta", "Beta Filial")
    if normalized == "gama":
        return GAMA_COMPANIES
    return ()


def normalize_request_category(value: str) -> str:
    normalized = value.strip()
    return REQUEST_CATEGORY_ALIASES.get(normalized, normalized)


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _normalize_short_text(value: str, max_length: int | None = None, uppercase: bool = False) -> str:
    normalized = value.strip()
    if uppercase:
        normalized = normalized.upper()
    if max_length is not None:
        normalized = normalized[:max_length]
    return normalized


def _normalize_brazilian_currency(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    digits = re.sub(r"\D", "", normalized)
    if not digits:
        return ""
    integer_part = digits[:-2] or "0"
    decimal_part = digits[-2:].rjust(2, "0")
    return f"{int(integer_part)},{decimal_part}"


def normalize_admission_field_value(name: str, value: str) -> str:
    normalized = value.strip()
    if name in {"employee_cpf", "employee_phone", "employee_zip_code", "employee_pis", "employee_alimony_beneficiary_cpf"}:
        return _only_digits(normalized)
    if name == "employee_salary":
        return _normalize_brazilian_currency(normalized)
    if name == "employee_rg":
        return re.sub(r"[^0-9A-Za-z]", "", normalized).upper()
    if name in BRAZILIAN_STATE_FIELD_NAMES:
        return _normalize_short_text(normalized, max_length=2, uppercase=True)
    return normalized


def normalize_vacation_field_value(name: str, value: str) -> str:
    normalized = value.strip()
    if name in {"vacation_days", "vacation_bonus_days"}:
        return _only_digits(normalized)
    return normalized


def normalize_dependent_field_value(name: str, value: str) -> str:
    normalized = value.strip()
    if name == "cpf":
        return _only_digits(normalized)
    return normalized


def cleanup_admission_conditionals(normalized: dict[str, str]) -> dict[str, str]:
    cleaned = dict(normalized)
    for field in ADMISSION_FIELD_SPECS:
        field_name = field["name"]
        dependencies = field.get("show_when") or field.get("required_when") or {}
        if dependencies and not all(cleaned.get(dep, "") == expected for dep, expected in dependencies.items()):
            cleaned[field_name] = ""
    return cleaned


def normalize_admission_form(form: dict[str, str]) -> dict[str, str]:
    normalized = {
        field["name"]: normalize_admission_field_value(field["name"], form.get(field["name"], ""))
        for field in ADMISSION_FIELD_SPECS
    }
    return cleanup_admission_conditionals(normalized)


def cleanup_vacation_conditionals(normalized: dict[str, str]) -> dict[str, str]:
    cleaned = dict(normalized)
    for field in VACATION_FIELD_SPECS:
        field_name = field["name"]
        dependencies = field.get("show_when") or field.get("required_when") or {}
        if dependencies and not all(cleaned.get(dep, "") == expected for dep, expected in dependencies.items()):
            cleaned[field_name] = ""
    return cleaned


def normalize_vacation_form(form: dict[str, str]) -> dict[str, str]:
    normalized = {
        field["name"]: normalize_vacation_field_value(field["name"], form.get(field["name"], ""))
        for field in VACATION_FIELD_SPECS
    }
    return cleanup_vacation_conditionals(normalized)


def extract_dependents_from_form(form: dict[str, str]) -> list[dict[str, str]]:
    indices: set[int] = set()
    for key in form:
        if not key.startswith("dependent_"):
            continue
        parts = key.split("_", 2)
        if len(parts) < 3:
            continue
        try:
            indices.add(int(parts[1]))
        except ValueError:
            continue

    dependents: list[dict[str, str]] = []
    for index in sorted(indices):
        item = {
            field["name"]: normalize_dependent_field_value(field["name"], form.get(f"dependent_{index}_{field['name']}", ""))
            for field in DEPENDENT_FIELD_SPECS
        }
        if any(value for value in item.values()):
            dependents.append(item)
    return dependents


def _is_admission_field_required(field: dict[str, Any], normalized: dict[str, str]) -> bool:
    if field.get("required"):
        return True
    required_when = field.get("required_when") or {}
    if not required_when:
        return False
    return all(normalized.get(dep, "") == expected for dep, expected in required_when.items())


def validate_admission_form(normalized: dict[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for field in ADMISSION_FIELD_SPECS:
        value = normalized.get(field["name"], "").strip()
        label = field["label"]
        if _is_admission_field_required(field, normalized) and not value:
            errors[field["name"]] = f"Informe {label.lower()}."
            continue
        options = field.get("options")
        if value and options and value not in options:
            errors[field["name"]] = f"Selecione uma opção válida para {label.lower()}."
        if field["name"] == "employee_cpf" and value:
            digits = re.sub(r"\D", "", value)
            if len(digits) != 11:
                errors[field["name"]] = "Informe um CPF com 11 dígitos."
        if field["name"] == "employee_zip_code" and value and len(_only_digits(value)) != 8:
            errors[field["name"]] = "Informe um CEP com 8 dígitos."
        if field["name"] == "employee_phone" and value and len(_only_digits(value)) not in {10, 11}:
            errors[field["name"]] = "Informe um telefone com DDD válido."
        if field["name"] == "employee_pis" and value and len(_only_digits(value)) != 11:
            errors[field["name"]] = "Informe um PIS com 11 dígitos."
        if field["name"] == "employee_salary" and value and not re.fullmatch(r"\d+,\d{2}", value):
            errors[field["name"]] = "Informe o salário no formato 9999,99."
        if field["name"] in BRAZILIAN_STATE_FIELD_NAMES:
            if value and not re.fullmatch(r"[A-Z]{2}", value):
                errors[field["name"]] = f"Informe {label.lower()} com 2 letras."
    return errors


def validate_vacation_form(normalized: dict[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for field in VACATION_FIELD_SPECS:
        value = normalized.get(field["name"], "").strip()
        label = field["label"]
        if _is_admission_field_required(field, normalized) and not value:
            errors[field["name"]] = f"Informe {label.lower()}."
            continue
        options = field.get("options")
        if value and options and value not in options:
            errors[field["name"]] = f"Selecione uma opção válida para {label.lower()}."
        max_length = field.get("max_length")
        if max_length and len(value) > max_length:
            errors[field["name"]] = f"{label} deve ter no máximo {max_length} caracteres."
    vacation_start_date = normalized.get("vacation_start_date", "").strip()
    if vacation_start_date:
        try:
            start_date = datetime.strptime(vacation_start_date, "%Y-%m-%d").date()
        except ValueError:
            errors["vacation_start_date"] = "Informe uma data de início válida."
        else:
            if start_date.weekday() >= 3:
                errors["vacation_start_date"] = "O início das férias deve ser entre segunda e quarta-feira."
    vacation_days = normalized.get("vacation_days", "").strip()
    if vacation_days:
        if not vacation_days.isdigit():
            errors["vacation_days"] = "Informe uma quantidade válida de dias de férias."
        elif not 1 <= int(vacation_days) <= 30:
            errors["vacation_days"] = "Os dias de férias devem ficar entre 1 e 30."
    bonus_days = normalized.get("vacation_bonus_days", "").strip()
    if bonus_days:
        if not bonus_days.isdigit():
            errors["vacation_bonus_days"] = "Informe uma quantidade válida de dias de abono."
        elif not 1 <= int(bonus_days) <= 10:
            errors["vacation_bonus_days"] = "Os dias de abono devem ficar entre 1 e 10."
        elif vacation_days.isdigit() and int(bonus_days) >= int(vacation_days):
            errors["vacation_bonus_days"] = "Os dias de abono devem ser menores que os dias de férias."
    return errors


def validate_dependents(dependents: list[dict[str, str]]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for index, dependent in enumerate(dependents):
        for field in DEPENDENT_FIELD_SPECS:
            value = dependent.get(field["name"], "").strip()
            error_key = f"dependent_{index}_{field['name']}"
            if field.get("required") and not value:
                errors[error_key] = f"Informe {field['label'].lower()}."
                continue
            options = field.get("options")
            if value and options and value not in options:
                errors[error_key] = f"Selecione uma opção válida para {field['label'].lower()}."
            if field["name"] == "cpf" and value:
                digits = re.sub(r"\D", "", value)
                if len(digits) != 11:
                    errors[error_key] = "Informe um CPF com 11 dígitos."
    return errors


def build_admission_ticket_payload(normalized: dict[str, str], dependents: list[dict[str, str]]) -> tuple[str, str]:
    title = f"Admissão - {normalized['employee_name']}"
    description_lines = [
        f"{field['label']}: {normalized[field['name']]}"
        for field in ADMISSION_FIELD_SPECS
        if normalized.get(field["name"], "").strip()
    ]
    for index, dependent in enumerate(dependents, start=1):
        description_lines.append("")
        description_lines.append(f"Dependente {index}:")
        for field in DEPENDENT_FIELD_SPECS:
            value = dependent.get(field["name"], "").strip()
            if value:
                description_lines.append(f"{field['label']}: {value}")
    return title[:160], "\n".join(description_lines)


def build_admission_payload(normalized: dict[str, str], dependents: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "version": ADMISSION_PAYLOAD_VERSION,
        "employee": {field["name"]: normalized.get(field["name"], "") for field in ADMISSION_FIELD_SPECS},
        "dependents": [
            {field["name"]: dependent.get(field["name"], "") for field in DEPENDENT_FIELD_SPECS}
            for dependent in dependents
        ],
    }


def build_vacation_ticket_payload(normalized: dict[str, str]) -> tuple[str, str]:
    title = f"Férias - {normalized['vacation_employee_name']}"
    description_lines = [
        f"Nome do funcionário: {normalized['vacation_employee_name']}",
        f"Data de início: {normalized['vacation_start_date']}",
        f"Dias de férias: {normalized['vacation_days']}",
        f"Abono de férias: {normalized['vacation_has_bonus']}",
    ]
    if normalized.get("vacation_registration", "").strip():
        description_lines.insert(0, f"Matrícula: {normalized['vacation_registration']}")
    if normalized.get("vacation_bonus_days", "").strip():
        description_lines.append(f"Dias de abono: {normalized['vacation_bonus_days']}")
    return title[:160], "\n".join(description_lines)


def extract_rescission_events(form: dict[str, str]) -> list[dict[str, str]]:
    indices = {int(match.group(1)) for match in map(RESCISSION_EVENT_FIELD_RE.fullmatch, form) if match}
    events = [
        {
            "name": form.get(f"rescission_event_{index}_name", "").strip(),
            "value": form.get(f"rescission_event_{index}_value", "").strip(),
        }
        for index in sorted(indices)
    ]
    return [event for event in events if event["name"] or event["value"]]


def validate_rescission_fields(normalized: dict[str, str], events: list[dict[str, str]]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if len(normalized["rescission_registration"]) > EMPLOYEE_REGISTRATION_MAX:
        errors["rescission_registration"] = f"A matrícula deve ter no máximo {EMPLOYEE_REGISTRATION_MAX} caracteres."
    if len(normalized["rescission_employee_name"]) > RESCISSION_NAME_MAX:
        errors["rescission_employee_name"] = f"O nome deve ter no máximo {RESCISSION_NAME_MAX} caracteres."
    if len(events) > RESCISSION_EVENTS_MAX_COUNT:
        errors["rescission_events"] = f"Inclua no máximo {RESCISSION_EVENTS_MAX_COUNT} eventos."
    for index, event in enumerate(events):
        if not event["name"]:
            errors[f"rescission_event_{index}_name"] = "Informe o evento."
        elif len(event["name"]) > RESCISSION_EVENT_NAME_MAX:
            errors[f"rescission_event_{index}_name"] = f"O evento deve ter no máximo {RESCISSION_EVENT_NAME_MAX} caracteres."
        if len(event["value"]) > RESCISSION_EVENT_VALUE_MAX:
            errors[f"rescission_event_{index}_value"] = f"O valor deve ter no máximo {RESCISSION_EVENT_VALUE_MAX} caracteres."
    if not normalized["rescission_date"]:
        errors["rescission_date"] = "Informe a data da rescisão."
    else:
        try:
            rescission_date = datetime.strptime(normalized["rescission_date"], "%Y-%m-%d").date()
        except ValueError:
            errors["rescission_date"] = "Informe uma data da rescisão válida."
        else:
            if not 2000 <= rescission_date.year <= 2100:
                errors["rescission_date"] = "Informe uma data da rescisão válida."
    return errors


def build_rescission_ticket_payload(normalized: dict[str, str], events: list[dict[str, str]]) -> tuple[str, str]:
    employee_name = normalized["rescission_employee_name"]
    title = f"Rescisão - {employee_name or normalized['rescission_type']}"
    rescission_date = datetime.strptime(normalized["rescission_date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    header_lines = [f"Tipo de rescisão: {normalized['rescission_type']}"]
    if normalized["rescission_registration"]:
        header_lines.append(f"Matrícula: {normalized['rescission_registration']}")
    if employee_name:
        header_lines.append(f"Nome do funcionário: {employee_name}")
    header_lines.append(f"Data da rescisão: {rescission_date}")
    description = "\n".join(header_lines) + f"\n\n{normalized['description']}"
    if events:
        event_lines = [f"{event['name']}: {event['value']}" if event["value"] else event["name"] for event in events]
        description += "\n\nEventos:\n" + "\n".join(event_lines)
    return title[:160], description


def build_vacation_payload(normalized: dict[str, str]) -> dict[str, Any]:
    return {
        "version": 1,
        "vacation": {field["name"]: normalized.get(field["name"], "") for field in VACATION_FIELD_SPECS},
    }


def serialize_admission_payload(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def deserialize_admission_payload(payload_text: str | None) -> dict[str, Any] | None:
    if not payload_text:
        return None
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _row_value(ticket: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(ticket, sqlite3.Row):
        return ticket[key] if key in ticket.keys() else default
    return ticket.get(key, default)


def parse_admission_description(description: str) -> dict[str, Any] | None:
    employee: dict[str, str] = {field["name"]: "" for field in ADMISSION_FIELD_SPECS}
    dependents: list[dict[str, str]] = []
    current_dependent: dict[str, str] | None = None
    for raw_line in description.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"Dependente \d+:", line):
            current_dependent = {field["name"]: "" for field in DEPENDENT_FIELD_SPECS}
            dependents.append(current_dependent)
            continue
        if ":" not in line:
            continue
        label, value = (part.strip() for part in line.split(":", 1))
        if current_dependent is not None and label in DEPENDENT_FIELD_LABELS:
            field_name = DEPENDENT_FIELD_LABELS[label]
            current_dependent[field_name] = normalize_dependent_field_value(field_name, value)
            continue
        if label in ADMISSION_FIELD_LABELS:
            field_name = ADMISSION_FIELD_LABELS[label]
            employee[field_name] = normalize_admission_field_value(field_name, value)
    employee = cleanup_admission_conditionals(employee)
    dependents = [dependent for dependent in dependents if any(dependent.values())]
    if not any(employee.values()) and not dependents:
        return None
    return build_admission_payload(employee, dependents)


def admission_payload_from_ticket_row(ticket: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if not ticket or _row_value(ticket, "request_category") != "Admissão":
        return None
    payload = deserialize_admission_payload(_row_value(ticket, "admission_payload"))
    if payload:
        return payload
    description = _row_value(ticket, "description", "")
    if not description:
        return None
    return parse_admission_description(description)


def vacation_payload_from_ticket_row(ticket: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if not ticket or _row_value(ticket, "request_category") != "Férias":
        return None
    payload = deserialize_admission_payload(_row_value(ticket, "admission_payload"))
    if payload and isinstance(payload.get("vacation"), dict):
        return payload
    return None


def build_admission_search_text(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    values: list[str] = []
    employee = payload.get("employee") or {}
    for field in ADMISSION_FIELD_SPECS:
        value = str(employee.get(field["name"], "")).strip()
        if value:
            values.append(value)
            if field["name"] in {"employee_cpf", "employee_phone", "employee_zip_code", "employee_pis"}:
                values.append(_only_digits(value))
    for dependent in payload.get("dependents") or []:
        for field in DEPENDENT_FIELD_SPECS:
            value = str(dependent.get(field["name"], "")).strip()
            if value:
                values.append(value)
                if field["name"] == "cpf":
                    values.append(_only_digits(value))
    return " ".join(values).lower()


def build_vacation_search_text(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    values: list[str] = []
    vacation = payload.get("vacation") or {}
    for field in VACATION_FIELD_SPECS:
        value = str(vacation.get(field["name"], "")).strip()
        if value:
            values.append(value)
    return " ".join(values).lower()


def admission_summary_text(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    employee = payload.get("employee") or {}
    dependent_count = len(payload.get("dependents") or [])
    summary_parts = [
        employee.get("employee_name", "").strip(),
        employee.get("employee_role", "").strip(),
        employee.get("employee_admission_date", "").strip(),
    ]
    summary = " • ".join(part for part in summary_parts if part)
    if dependent_count:
        suffix = f"{dependent_count} dependente" if dependent_count == 1 else f"{dependent_count} dependentes"
        return f"{summary} • {suffix}" if summary else suffix
    return summary


def vacation_summary_text(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    vacation = payload.get("vacation") or {}
    summary_parts = [
        str(vacation.get("vacation_employee_name", "")).strip(),
        str(vacation.get("vacation_start_date", "")).strip(),
        f"{vacation.get('vacation_days', '').strip()} dias" if str(vacation.get("vacation_days", "")).strip() else "",
    ]
    summary = " • ".join(part for part in summary_parts if part)
    if str(vacation.get("vacation_has_bonus", "")).strip() == "Sim" and str(vacation.get("vacation_bonus_days", "")).strip():
        bonus = f"abono de {vacation.get('vacation_bonus_days', '').strip()} dias"
        return f"{summary} • {bonus}" if summary else bonus
    return summary


def load_admission_csv_header() -> list[str]:
    if not ADMISSION_CSV_TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Template CSV não encontrado: {ADMISSION_CSV_TEMPLATE_PATH}")
    with ADMISSION_CSV_TEMPLATE_PATH.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file, delimiter=";")
        try:
            return next(reader)
        except StopIteration as exc:
            raise ValueError("Template CSV está vazio.") from exc


def _csv_date(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        year, month, day = normalized.split("-")
        return f"{day}/{month}/{year}"
    return normalized


def _experience_contract_days(value: str) -> tuple[str, str]:
    normalized = value.strip()
    if "/" not in normalized:
        return "", ""
    first, second = normalized.split("/", 1)
    return first.strip(), second.strip()


def build_admission_csv_row(ticket: sqlite3.Row | dict[str, Any]) -> list[str]:
    payload = admission_payload_from_ticket_row(ticket)
    if not payload:
        raise ValueError("Ticket informado não possui payload estruturado de admissão.")
    employee = payload.get("employee") or {}
    contract_days, extension_days = _experience_contract_days(str(employee.get("employee_experience_contract", "")))
    company_name = _row_value(ticket, "company_name", "")
    work_card_type = str(employee.get("employee_work_card_type", "")).strip()
    row_by_column = {
        "Sigla": ADMISSION_COMPANY_SIGLA_MAP.get(company_name, ""),
        "Filial": "",
        "Codigo funcionario": "",
        "Cpf": str(employee.get("employee_cpf", "")),
        "Nome": str(employee.get("employee_name", "")),
        "Data admissao": _csv_date(str(employee.get("employee_admission_date", ""))),
        "Departamento": str(employee.get("employee_department", "")),
        "Tipo inscricao departamento": "",
        "Inscricao departamento": "",
        "Secao": "",
        "Vinculo empregaticio": "",
        "Centro de custos": str(employee.get("employee_cost_center", "")),
        "Data nascimento": _csv_date(str(employee.get("employee_birth_date", ""))),
        "Pais nascimento": "Brasil",
        "Cargo": str(employee.get("employee_role", "")),
        "CBO Cargo": str(employee.get("employee_cbo", "")),
        "Sindicato": "",
        "CNPJ Sindicato": "",
        "Turno": str(employee.get("employee_work_schedule", "")),
        "Categoria trabalhador": "",
        "Tipo salario": "",
        "Valor salario": str(employee.get("employee_salary", "")),
        "Cor/raca": str(employee.get("employee_ethnicity", "")),
        "Sexo": str(employee.get("employee_gender", "")),
        "Grau instrucao": str(employee.get("employee_education", "")),
        "Tipo deficiencia": "",
        "Cep": str(employee.get("employee_zip_code", "")),
        "Logradouro": str(employee.get("employee_address", "")),
        "Numero": str(employee.get("employee_address_number", "")),
        "Complemento": "",
        "Bairro": str(employee.get("employee_neighborhood", "")),
        "Cidade": str(employee.get("employee_city", "")),
        "Estado": str(employee.get("employee_state", "")),
        "Telefone": str(employee.get("employee_phone", "")),
        "Celular": "",
        "E-mail": "",
        "Nome pai": str(employee.get("employee_father_name", "")),
        "Nome mae": str(employee.get("employee_mother_name", "")),
        "Nacionalidade": "Brasil",
        "Pais nacionalidade": "Brasil",
        "Cidade naturalidade": str(employee.get("employee_birth_city", "")),
        "UF naturalidade": str(employee.get("employee_birth_state", "")),
        "Data chegada pais": "",
        "Data naturalizacao": "",
        "Estado civil": str(employee.get("employee_marital_status", "")),
        "PIS/PASEP": str(employee.get("employee_pis", "")),
        "Data cadastro PIS/PASEP": "",
        "Numero identidade": str(employee.get("employee_rg", "")),
        "Orgao exp identidade": str(employee.get("employee_rg_issuer", "")),
        "UF exp identidade": str(employee.get("employee_rg_issuer_state", "")),
        "Data exp identidade": _csv_date(str(employee.get("employee_rg_issue_date", ""))),
        "Numero carteira trabalho": str(employee.get("employee_work_card_number", "")),
        "Serie carteira trabalho": str(employee.get("employee_work_card_series", "")),
        "UF carteira trabalho": str(employee.get("employee_work_card_state", "")),
        "Data exp carteira trabalho": _csv_date(str(employee.get("employee_work_card_issue_date", ""))),
        "Titulo eleitor": str(employee.get("employee_voter_id", "")),
        "Zona eleitoral": str(employee.get("employee_voter_zone", "")),
        "Secao eleitoral": str(employee.get("employee_voter_section", "")),
        "Numero carteira motorista": "",
        "Categoria carteira motorista": "",
        "Validade carteira motorista": "",
        "Data emissao carteira motorista": "",
        "Situacao servico militar": "",
        "Orgao servico militar": "",
        "Numero certificado militar": "",
        "Serie certificado militar": "",
        "Orgao classe": "",
        "Numero registro orgao classe": "",
        "UF orgao classe": "",
        "Regiao orgao classe": "",
        "Data exp orgao classe": "",
        "Data opcao FGTS": "",
        "Ficha registro": "",
        "Livro registro": "",
        "Folha registro": "",
        "Numero cartao ponto": "",
        "Dias contrato experiencia": contract_days,
        "Dias prorrogacao contrato": extension_days,
        "Tipo emprego": "",
        "Numero banco FGTS": "",
        "Numero conta FGTS": "",
        "Numero banco conta corrente": "",
        "Numero conta corrente": "",
        "Horas semanais": "",
        "Cartao nacional saude": "",
        "Matricula eSocial": "",
        "Tipo admissao": "",
        "Data transferencia": "",
        "Sigla Vinc.Anterior": "",
        "Filial Vinc.Anterior": "",
        "Tipo inscricao Vinc.Anterior": "",
        "CNPJ/CPF Vinc.Anterior": "",
        "Matricula Vinc.Anterior": "",
        "Data admissao Vinc.Anterior": "",
        "CTPS digital": "Sim" if work_card_type == "Digital" else "",
        "Categoria trab. eSocial": "",
        "Trab.s/vinc.matr. eSocial": "",
        "CNPJ instituicao ensino": "",
        "Foto": "",
    }
    header = load_admission_csv_header()
    return [row_by_column.get(column, "") for column in header]


def build_admission_csv_export(ticket_id: int) -> tuple[str, bytes]:
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        ticket = conn.execute(
            """
            SELECT tickets.*, request_types.name AS category_name, users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.id = ?
            """,
            (ticket_id,),
        ).fetchone()
    if not ticket:
        raise ValueError("Ticket não encontrado.")
    if ticket["request_category"] != "Admissão":
        raise ValueError("Exportação CSV disponível apenas para solicitações de admissão.")
    header = load_admission_csv_header()
    row = build_admission_csv_row(ticket)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    writer.writerow(row)
    employee_name = re.sub(r"[^A-Za-z0-9]+", "-", str(ticket["title"] or f"ticket-{ticket_id}")).strip("-").lower() or f"ticket-{ticket_id}"
    filename = f"admissao-{ticket_id:04d}-{employee_name}.csv"
    return filename, buffer.getvalue().encode("utf-8-sig")


def user_role_label(role: str) -> str:
    return {
        "dp": "Agente DP",
        "company": "Empresa",
        "beta": "Beta",
        "alfa": "Alfa",
        "ambos": "Ambos",
        "gama": "Gama",
    }.get(role, "Empresa")


def user_allowed_category_names(role: str) -> tuple[str, ...]:
    if role == "beta":
        return ("Beta",)
    if role == "alfa":
        return ("Alfa",)
    if role == "gama":
        return ("Gama",)
    if role == "dp":
        return ("Beta", "Alfa", "Gama")
    return ("Beta", "Alfa")


def user_ticket_category_scope(user: sqlite3.Row | dict[str, Any] | None) -> tuple[str, ...] | None:
    """Origens cujas solicitacoes o usuario pode ver. None = sem restricao (Agente DP)."""
    if not user or user["role"] == "dp":
        return None
    return user_allowed_category_names(user["role"])


def visible_companies_for_user(user: sqlite3.Row | dict[str, Any] | None) -> tuple[str, ...]:
    if not user:
        return ()
    role = user["role"]
    if role == "beta":
        return ("Beta", "Beta Filial")
    if role == "alfa":
        return ("Alfa Matriz", "Alfa SPE 1", "Alfa SPE 2")
    if role == "gama":
        return GAMA_COMPANIES
    return ()


_RICH_TEXT_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "span", "br"}
_RICH_TEXT_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _sanitize_rich_text_style(style: str) -> str:
    props = []
    for decl in style.split(";"):
        decl = decl.strip()
        if not decl or ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if prop in ("background-color", "color") and _RICH_TEXT_COLOR_RE.match(value):
            props.append(f"{prop}: {value}")
    return "; ".join(props)


class _RichTextSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit_start(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.out.append("<br>")

    def _emit_start(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("div", "p"):
            if self.out:
                self.out.append("<br>")
            return
        if tag not in _RICH_TEXT_ALLOWED_TAGS:
            return
        if tag == "br":
            self.out.append("<br>")
            return
        if tag == "span":
            style_value = ""
            for name, value in attrs:
                if name == "style" and value:
                    style_value = _sanitize_rich_text_style(value)
            if style_value:
                self.out.append(f'<span style="{html.escape(style_value, quote=True)}">')
            else:
                self.out.append("<span>")
            return
        self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag not in _RICH_TEXT_ALLOWED_TAGS or tag == "br":
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data))

    def get_html(self) -> str:
        return "".join(self.out)


def sanitize_rich_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    parser = _RichTextSanitizer()
    parser.feed(raw_html)
    parser.close()
    return parser.get_html()


class _RichTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")
        elif tag in ("div", "p"):
            if self.parts:
                self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def get_text(self) -> str:
        return "".join(self.parts)


def rich_text_to_plain(raw_html: str) -> str:
    if not raw_html:
        return ""
    parser = _RichTextExtractor()
    parser.feed(raw_html)
    parser.close()
    return parser.get_text()


def ticket_status_label(status: str) -> str:
    return TICKET_STATUS_LABELS.get(status, status)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def format_datetime(value: str | None) -> str:
    if not value:
        return "-"
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TIMEZONE).strftime("%d/%m/%Y %H:%M")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded or "$" not in encoded:
        return False
    salt, current_hash = encoded.split("$", 1)
    candidate = hash_password(password, salt).split("$", 1)[1]
    return secrets.compare_digest(candidate, current_hash)


def normalize_tab(tab: str) -> str:
    return tab if tab in VALID_TABS else "abertos"


def list_categories() -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute("SELECT * FROM request_types ORDER BY name").fetchall()


def list_category_owners() -> dict[int, set[int]]:
    """category_id -> ids dos agentes DP responsaveis pela folha da empresa."""
    with get_db() as conn:
        rows = conn.execute("SELECT category_id, user_id FROM category_owners").fetchall()
    owners: dict[int, set[int]] = {}
    for row in rows:
        owners.setdefault(row["category_id"], set()).add(row["user_id"])
    return owners


def set_category_owner(category_id: int, user_id: int | None) -> tuple[bool, str]:
    """Define o UNICO responsavel pela folha da origem (None remove)."""
    with get_db() as conn:
        category = conn.execute("SELECT id, name FROM request_types WHERE id = ?", (category_id,)).fetchone()
        if not category:
            return False, "Empresa não encontrada."
        if user_id is not None:
            agent = conn.execute(
                "SELECT id FROM users WHERE id = ? AND role = 'dp' AND is_active = 1", (user_id,)
            ).fetchone()
            if not agent:
                return False, "Selecione um Agente DP ativo."
        conn.execute("DELETE FROM category_owners WHERE category_id = ?", (category_id,))
        if user_id is not None:
            conn.execute("INSERT INTO category_owners (category_id, user_id) VALUES (?, ?)", (category_id, user_id))
    return True, f"Responsável de {category['name']} atualizado."


def list_ti_users(active_only: bool = False) -> list[sqlite3.Row]:
    query = "SELECT * FROM users"
    params: list[Any] = []
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY name"
    with get_db() as conn:
        return conn.execute(query, params).fetchall()


def get_user_by_email(email: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()


def has_users() -> bool:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0


def validate_ticket_form(
    form: dict[str, str],
    categories: list[sqlite3.Row],
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    admission_normalized = normalize_admission_form(form)
    vacation_normalized = normalize_vacation_form(form)
    dependents = extract_dependents_from_form(form)
    category_by_id = {str(row["id"]): row for row in categories}
    normalized = {
        "requester_name": form.get("requester_name", "").strip(),
        "category_id": form.get("category_id", "").strip(),
        "company_name": form.get("company_name", "").strip(),
        "request_category": normalize_request_category(form.get("request_category", "")),
        "rescission_type": form.get("rescission_type", "").strip(),
        "rescission_registration": form.get("rescission_registration", "").strip(),
        "rescission_employee_name": form.get("rescission_employee_name", "").strip(),
        "rescission_date": form.get("rescission_date", "").strip(),
        "title": form.get("title", "").strip(),
        "description": form.get("description", "").strip(),
        "admission_payload": "",
        **admission_normalized,
        **vacation_normalized,
    }
    errors: dict[str, str] = {}
    rescission_events: list[dict[str, str]] = []
    if not normalized["requester_name"]:
        errors["requester_name"] = "Informe o nome do solicitante."
    elif len(normalized["requester_name"]) < 3:
        errors["requester_name"] = "O nome deve ter pelo menos 3 caracteres."
    elif len(normalized["requester_name"]) > 120:
        errors["requester_name"] = "O nome deve ter no máximo 120 caracteres."

    selected_category = category_by_id.get(normalized["category_id"])
    if not normalized["category_id"]:
        errors["category_id"] = "Selecione a origem da solicitação."
    elif not selected_category:
        errors["category_id"] = "A origem selecionada é inválida."

    allowed_companies = company_options_for_category(selected_category["name"] if selected_category else None)
    if not normalized["company_name"]:
        errors["company_name"] = "Selecione a empresa."
    elif normalized["company_name"] not in allowed_companies:
        errors["company_name"] = "A empresa selecionada é inválida para essa origem."

    if not normalized["request_category"]:
        errors["request_category"] = "Selecione a categoria da solicitação."
    elif normalized["request_category"] not in REQUEST_CATEGORY_OPTIONS:
        errors["request_category"] = "A categoria selecionada é inválida."
    if normalized["request_category"] == "Rescisão":
        if not normalized["rescission_type"]:
            errors["rescission_type"] = "Selecione o tipo de rescisão."
        elif normalized["rescission_type"] not in RESCISSION_TYPE_OPTIONS:
            errors["rescission_type"] = "O tipo de rescisão selecionado é inválido."
        rescission_events = extract_rescission_events(form)
        for index, event in enumerate(rescission_events):
            normalized[f"rescission_event_{index}_name"] = event["name"]
            normalized[f"rescission_event_{index}_value"] = event["value"]
        errors.update(validate_rescission_fields(normalized, rescission_events))

    has_structured_admission_data = any(value for value in admission_normalized.values()) or bool(dependents)
    has_structured_vacation_data = any(value for value in vacation_normalized.values())
    if normalized["request_category"] == "Admissão" and has_structured_admission_data:
        errors.update(validate_admission_form(normalized))
        errors.update(validate_dependents(dependents))
        normalized["title"], normalized["description"] = build_admission_ticket_payload(normalized, dependents)
        normalized["admission_payload"] = serialize_admission_payload(build_admission_payload(normalized, dependents))
    elif normalized["request_category"] == "Férias":
        errors.update(validate_vacation_form(normalized))
        if has_structured_vacation_data:
            normalized["title"], normalized["description"] = build_vacation_ticket_payload(normalized)
            normalized["admission_payload"] = serialize_admission_payload(build_vacation_payload(normalized))
    else:
        if normalized["request_category"] != "Rescisão":
            if not normalized["title"]:
                errors["title"] = "Informe o título do chamado."
            elif len(normalized["title"]) < 5:
                errors["title"] = "O título deve ter pelo menos 5 caracteres."
            elif len(normalized["title"]) > 160:
                errors["title"] = "O título deve ter no máximo 160 caracteres."

        if not normalized["description"]:
            errors["description"] = "Descreva a solicitação."
        elif len(normalized["description"]) < 10:
            errors["description"] = "A descrição deve ter pelo menos 10 caracteres."
        elif len(normalized["description"]) > 4000:
            errors["description"] = "A descrição deve ter no máximo 4000 caracteres."

    named_attachments = [a for a in (attachments or []) if a.get("filename")]
    if len(named_attachments) > ATTACHMENT_MAX_COUNT:
        errors["attachment"] = f"Envie no máximo {ATTACHMENT_MAX_COUNT} anexos por vez."
    else:
        for attachment in named_attachments:
            if not attachment.get("content"):
                errors["attachment"] = "Não foi possível ler o anexo enviado."
                break
            if int(attachment.get("size_bytes", 0)) > ATTACHMENT_MAX_BYTES:
                errors["attachment"] = f"O anexo \"{Path(attachment['filename']).name}\" excede o tamanho máximo permitido."
                break

    # so monta com tudo valido: com erro, o formulario volta com os campos como foram digitados
    if normalized["request_category"] == "Rescisão" and not errors:
        normalized["title"], normalized["description"] = build_rescission_ticket_payload(normalized, rescission_events)

    return normalized, errors


def validate_user_form(form: dict[str, str], creating: bool = True) -> tuple[dict[str, str], dict[str, str]]:
    role = form.get("role", "").strip() or "dp"
    normalized = {
        "name": form.get("name", "").strip(),
        "email": form.get("email", "").strip().lower(),
        "department": form.get("department", "").strip()
        or (
            "DP"
            if role == "dp"
            else "Beta"
            if role == "beta"
            else "Alfa"
            if role == "alfa"
            else "Beta/Alfa"
            if role == "ambos"
            else "Gama"
            if role == "gama"
            else "Empresa"
        ),
        "role": role,
        "password": form.get("password", ""),
        "is_active": "1" if form.get("is_active", "1") == "1" else "0",
    }
    errors: dict[str, str] = {}
    if not normalized["name"]:
        errors["name"] = "Informe o nome do usuário."
    elif len(normalized["name"]) < 3:
        errors["name"] = "O nome deve ter pelo menos 3 caracteres."
    elif len(normalized["name"]) > 120:
        errors["name"] = "O nome deve ter no máximo 120 caracteres."

    if not normalized["email"]:
        errors["email"] = "Informe o e-mail do usuário."
    elif not EMAIL_RE.fullmatch(normalized["email"]):
        errors["email"] = "Informe um e-mail válido."

    if len(normalized["department"]) > 80:
        errors["department"] = "O setor deve ter no máximo 80 caracteres."
    if normalized["role"] not in VALID_USER_ROLES:
        errors["role"] = "Selecione um perfil válido."

    if creating or normalized["password"]:
        password = normalized["password"]
        if len(password) < 8:
            errors["password"] = "A senha deve ter pelo menos 8 caracteres."
        elif password.lower() == password or password.upper() == password or not any(char.isdigit() for char in password):
            errors["password"] = "Use pelo menos 8 caracteres, com letras e números."

    return normalized, errors


def record_ticket_event(
    conn: sqlite3.Connection,
    ticket_id: int,
    event_type: str,
    message: str,
    actor_user_id: int | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO ticket_events (ticket_id, actor_user_id, event_type, message, old_status, new_status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ticket_id, actor_user_id, event_type, message[:2000], old_status, new_status),
    )


def ensure_ticket_submission_store(conn: sqlite3.Connection) -> None:
    if DB_BACKEND != "sqlite":
        return
    ticket_columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute("PRAGMA table_info(tickets)").fetchall()
    }
    if "updated_at" not in ticket_columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN updated_at TEXT")
        conn.execute("UPDATE tickets SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)")
    if "public_token" not in ticket_columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN public_token TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """
            UPDATE tickets
            SET public_token = lower(hex(randomblob(12)))
            WHERE COALESCE(public_token, '') = ''
            """
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_public_token ON tickets(public_token)")
    if "company_name" not in ticket_columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN company_name TEXT NOT NULL DEFAULT ''")
    if "request_category" not in ticket_columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN request_category TEXT NOT NULL DEFAULT ''")
    if "created_by_user_id" not in ticket_columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN created_by_user_id INTEGER REFERENCES users(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_created_by_user_id ON tickets(created_by_user_id)")
    if "admission_payload" not in ticket_columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN admission_payload TEXT NOT NULL DEFAULT ''")
    if "admission_search_text" not in ticket_columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN admission_search_text TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_admission_search ON tickets(admission_search_text)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ticket_id INTEGER NOT NULL,
          actor_user_id INTEGER,
          event_type TEXT NOT NULL,
          message TEXT NOT NULL,
          old_status TEXT,
          new_status TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
          FOREIGN KEY (actor_user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket_created
        ON ticket_events(ticket_id, created_at DESC)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_attachments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ticket_id INTEGER NOT NULL,
          original_name TEXT NOT NULL,
          stored_name TEXT NOT NULL UNIQUE,
          content_type TEXT,
          size_bytes INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ticket_attachments_ticket_created
        ON ticket_attachments(ticket_id, created_at DESC)
        """
    )
    missing_structured_tickets = conn.execute(
        """
        SELECT id, request_category, description, admission_payload
        FROM tickets
        WHERE request_category IN ('Admissão', 'Férias')
          AND COALESCE(admission_payload, '') = ''
        """
    ).fetchall()
    for row in missing_structured_tickets:
        payload = admission_payload_from_ticket_row(row) if row["request_category"] == "Admissão" else vacation_payload_from_ticket_row(row)
        if not payload:
            continue
        search_text = build_admission_search_text(payload) if row["request_category"] == "Admissão" else build_vacation_search_text(payload)
        conn.execute(
            """
            UPDATE tickets
            SET admission_payload = ?, admission_search_text = ?
            WHERE id = ?
            """,
            (
                serialize_admission_payload(payload),
                search_text,
                row["id"],
            ),
        )


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("Nao foi possivel remover arquivo de anexo %s", path, exc_info=True)


def list_orphan_attachment_files() -> list[Path]:
    ATTACHMENTS_PATH.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        referenced = {
            row["stored_name"]
            for row in conn.execute("SELECT stored_name FROM ticket_attachments").fetchall()
        }
    orphan_paths: list[Path] = []
    for path in ATTACHMENTS_PATH.iterdir():
        if not path.is_file():
            continue
        if path.name == ".gitkeep":
            continue
        if path.name.startswith(".upload-") or path.name not in referenced:
            orphan_paths.append(path)
    return sorted(orphan_paths)


def cleanup_orphan_attachment_files() -> list[Path]:
    orphan_paths = list_orphan_attachment_files()
    for path in orphan_paths:
        _safe_unlink(path)
    return orphan_paths


def store_ticket_attachment(
    conn: sqlite3.Connection,
    ticket_id: int,
    attachment: dict[str, Any],
    uploaded_by_user_id: int | None = None,
    client_visible: bool = True,
) -> Path:
    original_name = Path(attachment["filename"]).name
    suffix = Path(original_name).suffix
    stored_name = f"{secrets.token_hex(16)}{suffix}"
    temp_path = ATTACHMENTS_PATH / f".upload-{stored_name}"
    final_path = ATTACHMENTS_PATH / stored_name
    temp_path.write_bytes(attachment["content"])
    try:
        conn.execute(
            """
            INSERT INTO ticket_attachments (ticket_id, original_name, stored_name, content_type, size_bytes, uploaded_by_user_id, client_visible)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                original_name,
                stored_name,
                attachment.get("content_type", "").strip(),
                int(attachment.get("size_bytes", len(attachment["content"]))),
                uploaded_by_user_id,
                1 if client_visible else 0,
            ),
        )
        temp_path.replace(final_path)
    except Exception:
        _safe_unlink(temp_path)
        _safe_unlink(final_path)
        raise
    return final_path


def create_ticket(
    form: dict[str, str],
    attachments: list[dict[str, Any]] | None = None,
    current_user: sqlite3.Row | dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, str], int | None]:
    categories = list_categories()
    normalized, errors = validate_ticket_form(form, categories, attachments)
    if errors:
        return normalized, errors, None
    public_token = secrets.token_urlsafe(18)
    admission_payload = deserialize_admission_payload(normalized.get("admission_payload", ""))
    search_text = ""
    if normalized["request_category"] == "Admissão":
        search_text = build_admission_search_text(admission_payload)
    elif normalized["request_category"] == "Férias":
        search_text = build_vacation_search_text(admission_payload)
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        created_files: list[Path] = []
        try:
            cursor = conn.execute(
                """
                INSERT INTO tickets (
                  requester_name,
                  requester_email,
                  requester_department,
                  category_id,
                  company_name,
                  request_category,
                  title,
                  description,
                  admission_payload,
                  admission_search_text,
                  priority,
                  created_by_user_id,
                  public_token
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["requester_name"],
                    (current_user["email"] if current_user else ""),
                    "",
                    normalized["category_id"],
                    normalized["company_name"],
                    normalized["request_category"],
                    normalized["title"],
                    normalized["description"],
                    serialize_admission_payload(admission_payload),
                    search_text,
                    DEFAULT_TICKET_PRIORITY,
                    (current_user["id"] if current_user else None),
                    public_token,
                ),
            )
            ticket_id = int(cursor.lastrowid)
            record_ticket_event(conn, ticket_id, "abertura", "Solicitação aberta pelo solicitante.")
            valid_attachments = [a for a in (attachments or []) if a.get("filename") and a.get("content")]
            for attachment in valid_attachments:
                created_files.append(store_ticket_attachment(conn, ticket_id, attachment))
            if valid_attachments:
                names = ", ".join(Path(a["filename"]).name for a in valid_attachments)
                record_ticket_event(conn, ticket_id, "anexo", f"Anexo(s) enviado(s): {names}")
        except Exception:
            for path in created_files:
                _safe_unlink(path)
            raise
    return normalized, {}, ticket_id


def build_user_ticket_owner_clause(user: sqlite3.Row | dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    if user["role"] == "dp":
        return ("1 = 1", ())
    return (
        "(tickets.created_by_user_id = ? OR (tickets.created_by_user_id IS NULL AND LOWER(tickets.requester_email) = ?))",
        (user["id"], str(user["email"]).lower()),
    )


def get_user_ticket_by_id(ticket_id: int, user: sqlite3.Row | dict[str, Any]) -> sqlite3.Row | None:
    owner_clause, owner_params = build_user_ticket_owner_clause(user)
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        return conn.execute(
            f"""
            SELECT tickets.*, request_types.name AS category_name, users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.id = ? AND {owner_clause}
            """,
            (ticket_id, *owner_params),
        ).fetchone()


def list_user_tickets(user: sqlite3.Row | dict[str, Any]) -> list[sqlite3.Row]:
    owner_clause, owner_params = build_user_ticket_owner_clause(user)
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        return conn.execute(
            f"""
            SELECT tickets.*, request_types.name AS category_name, users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE {owner_clause}
            ORDER BY tickets.created_at DESC, tickets.id DESC
            """,
            owner_params,
        ).fetchall()


def list_ticket_attachments(ticket_id: int, include_hidden: bool = True) -> list[sqlite3.Row]:
    """include_hidden=False: visao do cliente (sem os anexos enviados so pela tarefa do SharePoint)."""
    hidden_clause = "" if include_hidden else "AND client_visible = 1"
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        return conn.execute(
            f"""
            SELECT * FROM ticket_attachments
            WHERE ticket_id = ? {hidden_clause}
            ORDER BY created_at DESC, id DESC
            """,
            (ticket_id,),
        ).fetchall()


def get_ticket_attachment(attachment_id: int) -> tuple[sqlite3.Row | None, Path | None]:
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        row = conn.execute(
            "SELECT * FROM ticket_attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
    if not row:
        return None, None
    return row, ATTACHMENTS_PATH / row["stored_name"]


# ---------------------------------------------------------------------------
# Tarefa no portal de tarefas do SharePoint ao concluir (origens com configuracao,
# ex.: Gama). Beta e Alfa nao tem configuracao e nao sao afetadas.
# ---------------------------------------------------------------------------
SHAREPOINT_MONTHLY_PROCESS_IDS = (9,)  # Rotina Mensal Folha: 1x por mes, a automacao ja cria
SHAREPOINT_REQUEST_STATUSES = ("pendente", "enviando", "criada", "erro", "aviso")
SHAREPOINT_STALE_MINUTES = 15
SHAREPOINT_DESCRIPTION_LIMIT = 3000
MONTH_NAMES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def get_origin_sharepoint_config(conn: sqlite3.Connection, category_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM origin_sharepoint_config WHERE category_id = ? AND enabled = 1",
        (category_id,),
    ).fetchone()


def sharepoint_configured_category_ids() -> set[int]:
    with get_db() as conn:
        return {row["category_id"] for row in conn.execute(
            "SELECT category_id FROM origin_sharepoint_config WHERE enabled = 1"
        ).fetchall()}


def build_sharepoint_task_description(ticket: sqlite3.Row) -> str:
    title_line = f"PortalDP #{ticket['id']} — {ticket['request_category'] or 'Solicitação'}"
    lines = [title_line]
    if (ticket["company_name"] or "").strip():
        lines.append(ticket["company_name"].strip())
    lines.append(f"Solicitante: {ticket['requester_name']}")
    lines.append(f"Título: {ticket['title']}")
    body = rich_text_to_plain(ticket["description"] or "").strip()
    text = "\n".join(lines) + ("\n\n" + body if body else "")
    if len(text) > SHAREPOINT_DESCRIPTION_LIMIT:
        suffix = f"\n[...] Texto completo no PortalDP #{ticket['id']}."
        text = text[: SHAREPOINT_DESCRIPTION_LIMIT - len(suffix)].rstrip() + suffix
    return text


def create_sharepoint_task_request(
    conn: sqlite3.Connection,
    ticket: sqlite3.Row,
    config: sqlite3.Row,
    actor_user_id: int,
    stored_paths: list[Path],
) -> int:
    attachment_ids: list[int] = []
    for path in stored_paths:
        row = conn.execute("SELECT id FROM ticket_attachments WHERE stored_name = ?", (path.name,)).fetchone()
        if row:
            attachment_ids.append(row["id"])
    try:
        process_map = json.loads(config["process_map"])
    except (TypeError, ValueError):
        process_map = {}
    process_id = process_map.get(ticket["request_category"])
    now_local = utc_now().astimezone(DISPLAY_TIMEZONE)
    prazo = (now_local.date() + timedelta(days=int(config["prazo_dias"]))).isoformat()
    payload = {
        "ticket_id": ticket["id"],
        "request_category": ticket["request_category"],
        "process_id": process_id,
        "area_id": 3,
        "cliente_id": config["portal_cliente_id"],
        "client_site_url": config["client_site_url"],
        "revisao_assignee": config["revisao_assignee"],
        "competencia": f"{MONTH_NAMES_PT[now_local.month]} {now_local.year}",
        "prazo": prazo,
        "descricao": build_sharepoint_task_description(ticket),
        "check_existing": process_id in SHAREPOINT_MONTHLY_PROCESS_IDS,
    }
    status, message = "pendente", None
    if not process_id:
        status = "erro"
        message = f"Sem processo configurado no portal para a categoria \"{ticket['request_category']}\"."
    cursor = conn.execute(
        """
        INSERT INTO sharepoint_task_requests (ticket_id, status, payload, attachment_ids, message, created_by_user_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ticket["id"], status, json.dumps(payload, ensure_ascii=False), json.dumps(attachment_ids), message, actor_user_id),
    )
    return int(cursor.lastrowid)


def _sharepoint_request_view(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        payload = {}
    try:
        attachment_ids = json.loads(row["attachment_ids"])
    except (TypeError, ValueError):
        attachment_ids = []
    attachments = []
    if attachment_ids:
        placeholders = ",".join("?" for _ in attachment_ids)
        with get_db() as conn:
            attachments = [
                {"id": a["id"], "name": a["original_name"], "size": a["size_bytes"], "content_type": a["content_type"] or ""}
                for a in conn.execute(
                    f"SELECT id, original_name, size_bytes, content_type FROM ticket_attachments WHERE id IN ({placeholders}) ORDER BY id",
                    attachment_ids,
                ).fetchall()
            ]
    return {
        "id": row["id"], "ticket_id": row["ticket_id"], "status": row["status"],
        "task_id": row["task_id"], "attempts": row["attempts"], "message": row["message"],
        "payload": payload, "attachments": attachments,
    }


def list_pending_sharepoint_requests(limit: int = 20) -> list[dict[str, Any]]:
    cutoff = isoformat(utc_now() - timedelta(minutes=SHAREPOINT_STALE_MINUTES))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sharepoint_task_requests
            WHERE status = 'pendente' OR (status = 'enviando' AND updated_at < ?)
            ORDER BY id LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
    return [_sharepoint_request_view(row) for row in rows]


def claim_sharepoint_request(request_id: int) -> dict[str, Any] | None:
    cutoff = isoformat(utc_now() - timedelta(minutes=SHAREPOINT_STALE_MINUTES))
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE sharepoint_task_requests
            SET status = 'enviando', attempts = attempts + 1, updated_at = ?
            WHERE id = ? AND (status = 'pendente' OR (status = 'enviando' AND updated_at < ?))
            """,
            (isoformat(utc_now()), request_id, cutoff),
        )
        if cursor.rowcount != 1:
            return None
        row = conn.execute("SELECT * FROM sharepoint_task_requests WHERE id = ?", (request_id,)).fetchone()
    return _sharepoint_request_view(row)


def finish_sharepoint_request(request_id: int, status: str, task_id: int | None, message: str | None) -> tuple[bool, str]:
    if status not in SHAREPOINT_REQUEST_STATUSES or status == "pendente":
        return False, "Status inválido."
    with get_db() as conn:
        row = conn.execute("SELECT id, task_id FROM sharepoint_task_requests WHERE id = ?", (request_id,)).fetchone()
        if not row:
            return False, "Pedido não encontrado."
        conn.execute(
            "UPDATE sharepoint_task_requests SET status = ?, task_id = ?, message = ?, updated_at = ? WHERE id = ?",
            (status, task_id if task_id is not None else row["task_id"], (message or "")[:1000], isoformat(utc_now()), request_id),
        )
    return True, "ok"


def get_sharepoint_request_for_ticket(ticket_id: int) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM sharepoint_task_requests WHERE ticket_id = ? ORDER BY id DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()


def requeue_sharepoint_request(ticket_id: int) -> tuple[bool, str]:
    request = get_sharepoint_request_for_ticket(ticket_id)
    if not request:
        return False, "Este chamado não tem envio ao portal de tarefas."
    if request["status"] not in ("erro", "aviso"):
        return False, "O envio só pode ser refeito quando está com erro ou aviso."
    with get_db() as conn:
        conn.execute(
            "UPDATE sharepoint_task_requests SET status = 'pendente', message = NULL, updated_at = ? WHERE id = ?",
            (isoformat(utc_now()), request["id"]),
        )
    return True, "Envio reagendado para o portal de tarefas."


def sharepoint_request_has_attachment(request_id: int, attachment_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute("SELECT attachment_ids FROM sharepoint_task_requests WHERE id = ?", (request_id,)).fetchone()
    if not row:
        return False
    try:
        return attachment_id in json.loads(row["attachment_ids"])
    except (TypeError, ValueError):
        return False


def can_download_attachment(attachment: sqlite3.Row, user: sqlite3.Row | None, token: str) -> bool:
    """Anexo oculto: so DP. Com o link publico (token) qualquer um baixa. Logado sem token: DP, ou usuario da
    mesma origem do chamado (origens com tarefa no SharePoint, como a Gama: so o solicitante)."""
    with get_db() as conn:
        ticket = conn.execute(
            """
            SELECT tickets.category_id, tickets.created_by_user_id, tickets.requester_email, tickets.public_token,
                   request_types.name AS category_name
            FROM tickets JOIN request_types ON request_types.id = tickets.category_id
            WHERE tickets.id = ?
            """,
            (attachment["ticket_id"],),
        ).fetchone()
        if not ticket:
            return False
        is_dp = bool(user and user["role"] == "dp")
        if not attachment["client_visible"]:
            return is_dp  # anexo enviado so pela tarefa: nem com token nem logado (nao-DP)
        has_task_rules = bool(get_origin_sharepoint_config(conn, ticket["category_id"]))
    if is_dp:
        return True
    if token and token == ticket["public_token"]:
        return True
    if not user:
        return False
    if has_task_rules:
        same_creator = ticket["created_by_user_id"] is not None and ticket["created_by_user_id"] == user["id"]
        same_email = bool(ticket["requester_email"]) and str(ticket["requester_email"]).lower() == str(user["email"]).lower()
        return same_creator or same_email
    return ticket["category_name"] in user_allowed_category_names(user["role"])


# --- Chave Pix do funcionario na resposta do Agente DP (so Beta/Alfa; Folha mensal, Ferias, Rescisao) ---
PIX_ORIGIN_NAMES = ("beta", "alfa")
PIX_REQUEST_CATEGORIES = ("Folha mensal", "Férias", "Rescisão")
PIX_MAX_ENTRIES = 20
PIX_FORM_SCAN_LIMIT = 50
PIX_NAME_MAX = 120
PIX_KEY_MAX = 200
PIX_PREFIX_MATCH_MIN = 25  # a planilha original cortou nomes em 32 caracteres
PIX_KEY_TYPES = ("CPF", "CNPJ", "Telefone", "E-mail", "Chave aleatória")
_PIX_RANDOM_KEY_RE = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")


def _cpf_is_valid(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
        if (total * 10) % 11 % 10 != int(digits[size]):
            return False
    return True


def _cnpj_is_valid(digits: str) -> bool:
    if len(digits) != 14 or digits == digits[0] * 14:
        return False
    weights = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for size, w in ((12, weights), (13, [6] + weights)):
        rest = sum(int(digits[i]) * w[i] for i in range(size)) % 11
        if (0 if rest < 2 else 11 - rest) != int(digits[size]):
            return False
    return True


def detect_pix_key_type(key: str) -> str:
    """Sugere o tipo da chave. Vazio quando nao da para ter certeza (o agente escolhe)."""
    value = (key or "").strip()
    if not value:
        return ""
    if "@" in value:
        return "E-mail"
    if _PIX_RANDOM_KEY_RE.fullmatch(value):
        return "Chave aleatória"
    digits = re.sub(r"\D", "", value)
    if re.sub(r"[\s()./+-]", "", value) != digits:
        return ""
    if _cnpj_is_valid(digits):
        return "CNPJ"
    if _cpf_is_valid(digits):
        return "CPF"
    if len(digits) == 11 and digits[2] == "9":  # celular com DDD
        return "Telefone"
    if len(digits) == 13 and digits.startswith("55") and digits[4] == "9":  # +55 DDD celular
        return "Telefone"
    return ""


def pix_enabled_for(category_name: str | None, request_category: str | None) -> bool:
    return (category_name or "").strip().lower() in PIX_ORIGIN_NAMES and (request_category or "") in PIX_REQUEST_CATEGORIES


def pix_name_key(name: str) -> str:
    """Chave de comparacao de nomes: sem acento, maiusculo, espacos colapsados."""
    return " ".join(_strip_accents(name or "").upper().split())


def _pix_clean_text(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def search_pix_keys(category_id: int, query: str, limit: int = 15) -> list[dict[str, Any]]:
    tokens = pix_name_key(query).split()
    if not tokens:
        return []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, employee_name, name_key, pix_key, key_type FROM pix_keys WHERE category_id = ? ORDER BY employee_name",
            (category_id,),
        ).fetchall()
    matches = [row for row in rows if all(token in row["name_key"] for token in tokens)]
    return [
        {"id": row["id"], "name": row["employee_name"], "key": row["pix_key"] or "",
         "type": row["key_type"] or detect_pix_key_type(row["pix_key"])}
        for row in matches[:limit]
    ]


def find_pix_key_by_name(conn: sqlite3.Connection, category_id: int, name: str) -> sqlite3.Row | None:
    """Acha o cadastro pelo nome: igual, ou cadastro cortado (prefixo do nome completo) quando unico."""
    key = pix_name_key(name)
    if not key:
        return None
    row = conn.execute(
        "SELECT * FROM pix_keys WHERE category_id = ? AND name_key = ?", (category_id, key)
    ).fetchone()
    if row:
        return row
    candidates = [
        r for r in conn.execute("SELECT * FROM pix_keys WHERE category_id = ?", (category_id,)).fetchall()
        if len(r["name_key"]) >= PIX_PREFIX_MATCH_MIN and key.startswith(r["name_key"])
    ]
    return candidates[0] if len(candidates) == 1 else None


def pix_suggestion_for_ticket(ticket: sqlite3.Row | dict[str, Any]) -> dict[str, Any] | None:
    """Em Ferias o chamado traz o nome do funcionario: sugere o cadastro (ou um novo com esse nome)."""
    if _row_value(ticket, "request_category") != "Férias":
        return None
    payload = vacation_payload_from_ticket_row(ticket)
    name = str((payload or {}).get("vacation", {}).get("vacation_employee_name", "")).strip()
    if not name:
        match = re.search(r"^Nome do funcionário:\s*(.+)$", _row_value(ticket, "description") or "", re.MULTILINE)
        name = match.group(1).strip() if match else ""
    if not name:
        return None
    with get_db() as conn:
        row = find_pix_key_by_name(conn, ticket["category_id"], name)
    if row:
        return {"id": row["id"], "name": row["employee_name"], "key": row["pix_key"] or "",
                "type": row["key_type"] or detect_pix_key_type(row["pix_key"])}
    return {"id": 0, "name": _pix_clean_text(name, PIX_NAME_MAX).upper(), "key": "", "type": ""}


def parse_pix_form_entries(form: dict[str, str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index in range(PIX_FORM_SCAN_LIMIT):
        if not any(f"pix_{field}_{index}" in form for field in ("id", "name", "key")):
            continue
        try:
            entry_id = int(form.get(f"pix_id_{index}", "0") or 0)
        except ValueError:
            entry_id = 0
        entries.append({
            "id": entry_id,
            "name": _pix_clean_text(form.get(f"pix_name_{index}", ""), PIX_NAME_MAX),
            "key": _pix_clean_text(form.get(f"pix_key_{index}", ""), PIX_KEY_MAX),
            "type": (form.get(f"pix_type_{index}", "") or "").strip(),
        })
    return entries


def resolve_pix_entries(
    conn: sqlite3.Connection, ticket: sqlite3.Row | dict[str, Any], form: dict[str, str]
) -> tuple[list[dict[str, Any]], str]:
    """Valida o que o agente marcou. Nao grava nada. Devolve (entradas, erro)."""
    raw_entries = parse_pix_form_entries(form)
    if not raw_entries:
        return [], "Selecione pelo menos um funcionário para enviar a chave Pix."
    if len(raw_entries) > PIX_MAX_ENTRIES:
        return [], f"Envie a chave Pix de no máximo {PIX_MAX_ENTRIES} funcionários por resposta."
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        row = None
        if raw["id"] > 0:
            row = conn.execute(
                "SELECT * FROM pix_keys WHERE id = ? AND category_id = ?", (raw["id"], ticket["category_id"])
            ).fetchone()
            if not row:
                return [], "Funcionário da lista de Pix não encontrado para esta origem."
            name = row["employee_name"]
        else:
            name = raw["name"].upper()
            if not name:
                return [], "Informe o nome do funcionário cadastrado para enviar a chave Pix."
            row = conn.execute(
                "SELECT * FROM pix_keys WHERE category_id = ? AND name_key = ?",
                (ticket["category_id"], pix_name_key(name)),
            ).fetchone()
            if row:
                name = row["employee_name"]
        if not raw["key"]:
            return [], f"Informe a chave Pix de {name}."
        if not raw["type"]:
            return [], f"Informe o tipo da chave Pix de {name} (CPF, Telefone, E-mail...)."
        if raw["type"] not in PIX_KEY_TYPES:
            return [], f"Tipo de chave Pix inválido para {name}."
        dedupe_key = pix_name_key(name)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        resolved.append({
            "row_id": row["id"] if row else None,
            "name": name,
            "name_key": dedupe_key,
            "key": raw["key"],
            "type": raw["type"],
            "old_key": (row["pix_key"] or "") if row else "",
            "old_type": (row["key_type"] or "") if row else "",
        })
    return resolved, ""


def build_pix_message(entries: list[dict[str, Any]]) -> tuple[str, str]:
    """Bloco ao final da resposta: 'Chave Pix para pagamento:' e uma linha 'NOME — TIPO: chave' por funcionario."""
    header = "Chave Pix para pagamento:"
    lines = [f"{entry['name']} — {entry['type']}: {entry['key']}" for entry in entries]
    html_block = header + "".join("<br>" + html.escape(line) for line in lines)
    text_block = "\n".join([header] + lines)
    return html_block, text_block


def apply_pix_entries(
    conn: sqlite3.Connection, ticket: sqlite3.Row | dict[str, Any], entries: list[dict[str, Any]], actor_user_id: int
) -> None:
    """Grava cadastro novo, chave alterada ou tipo informado/alterado, com historico (quem/quando/valor antigo)."""
    now = isoformat(utc_now())
    for entry in entries:
        if entry["row_id"] is None:
            cursor = conn.execute(
                "INSERT INTO pix_keys (category_id, employee_name, name_key, pix_key, key_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticket["category_id"], entry["name"], entry["name_key"], entry["key"], entry["type"], now, now),
            )
            pix_key_id = cursor.lastrowid
        elif entry["key"] != _pix_clean_text(entry["old_key"], PIX_KEY_MAX) or entry["type"] != entry["old_type"]:
            conn.execute(
                "UPDATE pix_keys SET pix_key = ?, key_type = ?, updated_at = ? WHERE id = ?",
                (entry["key"], entry["type"], now, entry["row_id"]),
            )
            pix_key_id = entry["row_id"]
        else:
            continue
        conn.execute(
            "INSERT INTO pix_key_history (pix_key_id, old_key, new_key, old_type, new_type, changed_by_user_id, ticket_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pix_key_id, entry["old_key"], entry["key"], entry["old_type"], entry["type"], actor_user_id, ticket["id"], now),
        )


# --- Gestao de Pix (/dp/pix): lista, cadastro, alteracao e exclusao pelo Agente DP ---
def pix_origin_categories() -> list[sqlite3.Row]:
    return [row for row in list_categories() if row["name"].strip().lower() in PIX_ORIGIN_NAMES]


def list_pix_keys(category_id: str = "", query: str = "") -> list[sqlite3.Row]:
    clauses = [f"LOWER(request_types.name) IN ({','.join('?' for _ in PIX_ORIGIN_NAMES)})"]
    params: list[Any] = list(PIX_ORIGIN_NAMES)
    if category_id.isdigit():
        clauses.append("pix_keys.category_id = ?")
        params.append(int(category_id))
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT pix_keys.*, request_types.name AS category_name
            FROM pix_keys
            JOIN request_types ON request_types.id = pix_keys.category_id
            WHERE {' AND '.join(clauses)}
            ORDER BY request_types.name, pix_keys.employee_name
            """,
            params,
        ).fetchall()
    tokens = pix_name_key(query).split()
    return [row for row in rows if all(token in row["name_key"] for token in tokens)]


def _insert_pix_history(
    conn: sqlite3.Connection, pix_key_id: int, old_key: str, new_key: str, old_type: str, new_type: str, actor_user_id: int
) -> None:
    conn.execute(
        "INSERT INTO pix_key_history (pix_key_id, old_key, new_key, old_type, new_type, changed_by_user_id, ticket_id, created_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
        (pix_key_id, old_key, new_key, old_type, new_type, actor_user_id, isoformat(utc_now())),
    )


def create_pix_key(form: dict[str, str], actor_user_id: int) -> tuple[bool, str]:
    """Cadastro novo: origem, nome e chave. O tipo vem da deteccao; se incerto, fica vazio."""
    name = _pix_clean_text(form.get("name", ""), PIX_NAME_MAX).upper()
    key = _pix_clean_text(form.get("key", ""), PIX_KEY_MAX)
    category_id_raw = form.get("origin_id", "").strip()
    category = next((row for row in pix_origin_categories() if str(row["id"]) == category_id_raw), None)
    if not category:
        return False, "Selecione a origem do funcionário (Beta ou Alfa)."
    if not name:
        return False, "Informe o nome do funcionário."
    if not key:
        return False, "Informe a chave Pix."
    key_type = detect_pix_key_type(key)
    name_key = pix_name_key(name)
    now = isoformat(utc_now())
    try:
        with get_db() as conn:
            if conn.execute(
                "SELECT id FROM pix_keys WHERE category_id = ? AND name_key = ?", (category["id"], name_key)
            ).fetchone():
                return False, f"{name} já está cadastrado em {category['name']}."
            cursor = conn.execute(
                "INSERT INTO pix_keys (category_id, employee_name, name_key, pix_key, key_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (category["id"], name, name_key, key, key_type, now, now),
            )
            _insert_pix_history(conn, cursor.lastrowid, "", key, "", key_type, actor_user_id)
    except DBIntegrityError:
        return False, f"{name} já está cadastrado em {category['name']}."
    return True, f"Pix de {name} cadastrado em {category['name']}."


def update_pix_key(pix_key_id: int, form: dict[str, str], actor_user_id: int) -> tuple[bool, str]:
    name = _pix_clean_text(form.get("name", ""), PIX_NAME_MAX).upper()
    key = _pix_clean_text(form.get("key", ""), PIX_KEY_MAX)
    key_type = form.get("key_type", "").strip()
    if not name:
        return False, "Informe o nome do funcionário."
    if key_type and key_type not in PIX_KEY_TYPES:
        return False, "Tipo de chave Pix inválido."
    name_key = pix_name_key(name)
    try:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM pix_keys WHERE id = ?", (pix_key_id,)).fetchone()
            if not row:
                return False, "Cadastro de Pix não encontrado."
            if not key and row["pix_key"]:
                return False, "Informe a chave Pix. Para remover o funcionário, use Excluir."
            if not key:
                key_type = ""
            elif not key_type:
                key_type = detect_pix_key_type(key)
            if conn.execute(
                "SELECT id FROM pix_keys WHERE category_id = ? AND name_key = ? AND id <> ?",
                (row["category_id"], name_key, pix_key_id),
            ).fetchone():
                return False, f"{name} já está cadastrado nesta origem."
            conn.execute(
                "UPDATE pix_keys SET employee_name = ?, name_key = ?, pix_key = ?, key_type = ?, updated_at = ? WHERE id = ?",
                (name, name_key, key, key_type, isoformat(utc_now()), pix_key_id),
            )
            if key != row["pix_key"] or key_type != row["key_type"]:
                _insert_pix_history(conn, pix_key_id, row["pix_key"], key, row["key_type"], key_type, actor_user_id)
    except DBIntegrityError:
        return False, f"{name} já está cadastrado nesta origem."
    return True, f"Pix de {name} atualizado."


def delete_pix_key(pix_key_id: int, actor_user_id: int) -> tuple[bool, str]:
    with get_db() as conn:
        row = conn.execute("SELECT id, employee_name FROM pix_keys WHERE id = ?", (pix_key_id,)).fetchone()
        if not row:
            return False, "Cadastro de Pix não encontrado."
        conn.execute("DELETE FROM pix_key_history WHERE pix_key_id = ?", (pix_key_id,))
        conn.execute("DELETE FROM pix_keys WHERE id = ?", (pix_key_id,))
    logger.info("Pix excluido: id=%s nome=%s por usuario=%s", pix_key_id, row["employee_name"], actor_user_id)
    return True, f"Pix de {row['employee_name']} excluído."


def create_user(form: dict[str, str], current_user_id: int | None = None, initial_setup: bool = False) -> tuple[dict[str, str], dict[str, str]]:
    normalized, errors = validate_user_form(form, creating=True)
    if initial_setup:
        normalized["role"] = "dp"
        normalized["department"] = "DP"
    if errors:
        return normalized, errors
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (name, email, department, password_hash, role, is_active, activated_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    normalized["name"],
                    normalized["email"],
                    normalized["department"],
                    hash_password(normalized["password"]),
                    normalized["role"],
                    isoformat(utc_now()),
                    isoformat(utc_now()),
                ),
            )
    except DBIntegrityError:
        errors["email"] = "Já existe usuário com esse e-mail."
    return normalized, errors


def update_user(user_id: int, form: dict[str, str], current_user_id: int) -> tuple[bool, str]:
    normalized, errors = validate_user_form(form, creating=False)
    if errors:
        return False, next(iter(errors.values()))
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return False, "Usuário não encontrado."
        active_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
        active_dp_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1 AND role = 'dp'").fetchone()[0]
        if target["id"] == current_user_id and normalized["is_active"] == "0":
            return False, "Não é permitido desativar o próprio usuário."
        if target["id"] == current_user_id and normalized["role"] != "dp":
            return False, "Não é permitido remover o próprio acesso de DP."
        if target["is_active"] and normalized["is_active"] == "0" and active_count <= 1:
            return False, "Não é permitido desativar o único usuário ativo."
        if target["is_active"] and target["role"] == "dp" and (normalized["is_active"] == "0" or normalized["role"] != "dp") and active_dp_count <= 1:
            return False, "Não é permitido remover o último usuário ativo do DP."
        try:
            if normalized["password"]:
                conn.execute(
                    """
                    UPDATE users
                    SET name = ?, email = ?, department = ?, role = ?, is_active = ?, password_hash = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized["name"],
                        normalized["email"],
                        normalized["department"],
                        normalized["role"],
                        normalized["is_active"],
                        hash_password(normalized["password"]),
                        isoformat(utc_now()),
                        user_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET name = ?, email = ?, department = ?, role = ?, is_active = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized["name"],
                        normalized["email"],
                        normalized["department"],
                        normalized["role"],
                        normalized["is_active"],
                        isoformat(utc_now()),
                        user_id,
                    ),
                )
        except DBIntegrityError:
            return False, "Já existe usuário com esse e-mail."
    return True, "Usuário atualizado com sucesso."


def validate_invite_form(form: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    normalized = {
        "name": form.get("name", "").strip(),
        "email": form.get("email", "").strip().lower(),
        "role": form.get("role", "").strip(),
    }
    errors: dict[str, str] = {}
    if not normalized["name"]:
        errors["name"] = "Informe o nome do usuário."
    elif len(normalized["name"]) < 3:
        errors["name"] = "O nome deve ter pelo menos 3 caracteres."
    elif len(normalized["name"]) > 120:
        errors["name"] = "O nome deve ter no máximo 120 caracteres."
    if not normalized["email"]:
        errors["email"] = "Informe o e-mail do usuário."
    elif not EMAIL_RE.fullmatch(normalized["email"]):
        errors["email"] = "Informe um e-mail válido."
    if normalized["role"] not in VALID_USER_ROLES:
        errors["role"] = "Selecione um perfil válido."
    return normalized, errors


def create_user_invite(form: dict[str, str], invited_by_user_id: int) -> tuple[dict[str, str], dict[str, str], int | None]:
    normalized, errors = validate_invite_form(form)
    if errors:
        return normalized, errors, None
    invite_token = secrets.token_urlsafe(24)
    expires_at = isoformat(utc_now() + timedelta(days=7))
    department = "DP" if normalized["role"] == "dp" else user_role_label(normalized["role"])
    with get_db() as conn:
        existing_user = conn.execute("SELECT * FROM users WHERE email = ?", (normalized["email"],)).fetchone()
        if existing_user and existing_user["is_active"]:
            return normalized, {"email": "Já existe um usuário ativo com esse e-mail."}, None
        if existing_user:
            user_id = existing_user["id"]
            conn.execute(
                """
                UPDATE users
                SET name = ?, role = ?, department = ?, is_active = 0, updated_at = ?
                WHERE id = ?
                """,
                (normalized["name"], normalized["role"], department, isoformat(utc_now()), user_id),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO users (name, email, department, password_hash, role, is_active, activated_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, NULL, ?)
                """,
                (
                    normalized["name"],
                    normalized["email"],
                    department,
                    hash_password(secrets.token_urlsafe(16)),
                    normalized["role"],
                    isoformat(utc_now()),
                ),
            )
            user_id = int(cursor.lastrowid)
        cursor = conn.execute(
            """
            INSERT INTO user_invites (user_id, email, role, invite_token, invited_by_user_id, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, normalized["email"], normalized["role"], invite_token, invited_by_user_id, expires_at),
        )
        invite_id = int(cursor.lastrowid)
    return normalized, {}, invite_id


def get_user_invite(invite_token: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT user_invites.*, users.name AS user_name
            FROM user_invites
            JOIN users ON users.id = user_invites.user_id
            WHERE user_invites.invite_token = ?
              AND user_invites.accepted_at IS NULL
              AND user_invites.expires_at > ?
            """,
            (invite_token, isoformat(utc_now())),
        ).fetchone()


def accept_user_invite(invite_token: str, form: dict[str, str]) -> tuple[bool, str]:
    password = form.get("password", "")
    confirm_password = form.get("confirm_password", "")
    if len(password) < 8:
        return False, "A senha deve ter pelo menos 8 caracteres."
    if password.lower() == password or password.upper() == password or not any(char.isdigit() for char in password):
        return False, "Use pelo menos 8 caracteres, com letras e números."
    if password != confirm_password:
        return False, "A confirmação de senha não confere."

    with get_db() as conn:
        invite = conn.execute(
            """
            SELECT * FROM user_invites
            WHERE invite_token = ? AND accepted_at IS NULL AND expires_at > ?
            """,
            (invite_token, isoformat(utc_now())),
        ).fetchone()
        if not invite:
            return False, "Convite inválido ou expirado."
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, role = ?, department = ?, is_active = 1, activated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                hash_password(password),
                invite["role"],
                "DP" if invite["role"] == "dp" else user_role_label(invite["role"]),
                isoformat(utc_now()),
                isoformat(utc_now()),
                invite["user_id"],
            ),
        )
        conn.execute(
            "UPDATE user_invites SET accepted_at = ? WHERE id = ?",
            (isoformat(utc_now()), invite["id"]),
        )
    return True, "Cadastro concluído com sucesso."


def ensure_password_reset_store(conn: sqlite3.Connection) -> None:
    if DB_BACKEND == "postgres":
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_resets (
              id SERIAL PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              email TEXT NOT NULL,
              reset_token TEXT NOT NULL UNIQUE,
              requested_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
              expires_at TEXT NOT NULL,
              used_at TEXT
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_resets (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              email TEXT NOT NULL,
              reset_token TEXT NOT NULL UNIQUE,
              requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              expires_at TEXT NOT NULL,
              used_at TEXT,
              FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_password_resets_token ON password_resets(reset_token)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_password_resets_user_active ON password_resets(user_id, used_at)")


def create_password_reset_request(email: str) -> int | None:
    normalized_email = email.lower().strip()
    if not EMAIL_RE.fullmatch(normalized_email):
        return None
    reset_token = secrets.token_urlsafe(32)
    expires_at = isoformat(utc_now() + timedelta(hours=2))
    now = isoformat(utc_now())
    with get_db() as conn:
        ensure_password_reset_store(conn)
        user = conn.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1",
            (normalized_email,),
        ).fetchone()
        if not user:
            return None
        conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
            (now, user["id"]),
        )
        cursor = conn.execute(
            """
            INSERT INTO password_resets (user_id, email, reset_token, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (user["id"], normalized_email, reset_token, expires_at),
        )
        return int(cursor.lastrowid)


def get_password_reset(reset_token: str) -> sqlite3.Row | None:
    with get_db() as conn:
        ensure_password_reset_store(conn)
        return conn.execute(
            """
            SELECT password_resets.*, users.name AS user_name
            FROM password_resets
            JOIN users ON users.id = password_resets.user_id
            WHERE password_resets.reset_token = ?
              AND password_resets.used_at IS NULL
              AND password_resets.expires_at > ?
              AND users.is_active = 1
            """,
            (reset_token, isoformat(utc_now())),
        ).fetchone()


def reset_user_password(reset_token: str, form: dict[str, str]) -> tuple[bool, str]:
    password = form.get("password", "")
    confirm_password = form.get("confirm_password", "")
    if len(password) < 8:
        return False, "A senha deve ter pelo menos 8 caracteres."
    if password.lower() == password or password.upper() == password or not any(char.isdigit() for char in password):
        return False, "Use pelo menos 8 caracteres, com letras e números."
    if password != confirm_password:
        return False, "A confirmação de senha não confere."

    now = isoformat(utc_now())
    with get_db() as conn:
        ensure_password_reset_store(conn)
        reset = conn.execute(
            """
            SELECT * FROM password_resets
            WHERE reset_token = ? AND used_at IS NULL AND expires_at > ?
            """,
            (reset_token, now),
        ).fetchone()
        if not reset:
            return False, "Link inválido ou expirado."
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ? AND is_active = 1",
            (hash_password(password), now, reset["user_id"]),
        )
        conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE id = ?",
            (now, reset["id"]),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (reset["user_id"],))
    return True, "Senha redefinida com sucesso."


def send_password_reset_email(reset_id: int) -> str:
    with get_db() as conn:
        ensure_password_reset_store(conn)
        reset = conn.execute(
            """
            SELECT password_resets.*, users.name AS user_name
            FROM password_resets
            JOIN users ON users.id = password_resets.user_id
            WHERE password_resets.id = ?
            """,
            (reset_id,),
        ).fetchone()
    if not reset:
        return "skipped"

    reset_link = f"{APP_BASE_URL}/redefinir-senha/{reset['reset_token']}"
    message = EmailMessage()
    message["Subject"] = "Redefinição de senha do Portal DP"
    message["From"] = MAIL_FROM
    message["To"] = reset["email"]
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    message.set_content(
        "\n".join(
            [
                f"Olá, {reset['user_name'] or reset['email']}.",
                "",
                "Recebemos uma solicitação para redefinir sua senha no Portal DP.",
                "Use o link abaixo para criar uma nova senha:",
                reset_link,
                "",
                "Esse link expira em 2 horas.",
                "Se você não solicitou a redefinição, ignore esta mensagem.",
            ]
        )
    )
    slug = f"password-reset-{reset_id}"
    return deliver_email(message, slug)


def cleanup_expired_sessions() -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (isoformat(utc_now()),))


def ensure_session_store(conn: sqlite3.Connection) -> None:
    if DB_BACKEND != "sqlite":
        return
    columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN updated_at TEXT")
        conn.execute(
            "UPDATE sessions SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
        )


def get_session_user(session_token: str | None) -> sqlite3.Row | None:
    if not session_token:
        return None
    cleanup_expired_sessions()
    with get_db() as conn:
        return conn.execute(
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND sessions.expires_at > ? AND users.is_active = 1
            """,
            (session_token, isoformat(utc_now())),
        ).fetchone()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = isoformat(utc_now() + timedelta(days=SESSION_DAYS))
    with get_db() as conn:
        ensure_session_store(conn)
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO sessions (user_id, token, expires_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, token, expires_at, isoformat(utc_now())),
        )
    return token


def delete_session(session_token: str | None) -> None:
    if not session_token:
        return
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (session_token,))


def authenticate_user(email: str, password: str) -> sqlite3.Row | None:
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    if not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
        return None
    return user


def is_dp_user(user: sqlite3.Row | None) -> bool:
    return bool(user and user["role"] == "dp")


def ensure_login_attempts_store(conn: sqlite3.Connection) -> None:
    if DB_BACKEND != "sqlite":
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS login_attempts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ip_address TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_created_at
        ON login_attempts(ip_address, created_at DESC)
        """
    )


def register_login_attempt(ip: str, success: bool) -> None:
    cutoff = isoformat(utc_now() - timedelta(seconds=LOGIN_RATE_LIMIT_WINDOW))
    with get_db() as conn:
        ensure_login_attempts_store(conn)
        conn.execute("DELETE FROM login_attempts WHERE created_at <= ?", (cutoff,))
        if not success:
            conn.execute(
                "INSERT INTO login_attempts (ip_address, created_at) VALUES (?, ?)",
                (ip, isoformat(utc_now())),
            )


def is_login_rate_limited(ip: str) -> bool:
    cutoff = isoformat(utc_now() - timedelta(seconds=LOGIN_RATE_LIMIT_WINDOW))
    with get_db() as conn:
        ensure_login_attempts_store(conn)
        conn.execute("DELETE FROM login_attempts WHERE created_at <= ?", (cutoff,))
        count = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE ip_address = ? AND created_at > ?",
            (ip, cutoff),
        ).fetchone()[0]
    return count >= LOGIN_RATE_LIMIT_ATTEMPTS


def get_ticket_filters(form: dict[str, str]) -> dict[str, str]:
    return {
        "category_id": form.get("category_id", "").strip(),
        "assigned_to": form.get("assigned_to", "").strip(),
        "folha_owner": form.get("folha_owner", "").strip(),
        "q": form.get("q", "").strip(),
        "sort": form.get("sort", "recentes").strip() if form.get("sort", "recentes").strip() in ALLOWED_SORTS else "recentes",
    }


def build_dashboard_query(
    active_tab: str, filters: dict[str, str], allowed_categories: tuple[str, ...] | None = None
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if allowed_categories is not None:
        # usuario de uma origem so ve solicitacoes da(s) sua(s) origem(ns)
        clauses.append(f"request_types.name IN ({','.join('?' for _ in allowed_categories)})")
        params.extend(allowed_categories)
    if active_tab == "abertos":
        clauses.append("tickets.status = 'Aberto'")
    elif active_tab == "andamento":
        clauses.append("tickets.status IN ('Em analise', 'Em atendimento', 'Aguardando retorno')")
    elif active_tab == "concluidos":
        clauses.append("tickets.status = 'Concluido'")
    else:
        clauses.append("1 = 1")

    if filters["category_id"]:
        clauses.append("tickets.category_id = ?")
        params.append(filters["category_id"])
    if filters["assigned_to"] == "none":
        clauses.append("tickets.assigned_to_user_id IS NULL")
    elif filters["assigned_to"]:
        clauses.append("tickets.assigned_to_user_id = ?")
        params.append(filters["assigned_to"])
    if filters.get("folha_owner"):
        # Responsavel folha: empresas (origens) atribuidas ao agente em Gestao de empresas
        clauses.append("tickets.category_id IN (SELECT category_id FROM category_owners WHERE user_id = ?)")
        params.append(filters["folha_owner"])
    # a busca textual (filters["q"]) e aplicada em Python apos o fetch,
    # em _filter_tickets_by_query, para permitir busca por palavras-chave
    # soltas e sem acento

    if filters["sort"] == "antigos":
        order_by = "tickets.created_at ASC"
    else:
        order_by = "tickets.created_at DESC"
    return " AND ".join(clauses), params + [order_by]


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def _search_haystack(row: sqlite3.Row) -> str:
    parts = [
        _row_value(row, "requester_name", ""),
        _row_value(row, "title", ""),
        str(_row_value(row, "id", "")),
        _row_value(row, "admission_search_text", ""),
        _row_value(row, "category_name", ""),
        _row_value(row, "request_category", ""),
        _row_value(row, "company_name", ""),
    ]
    return _strip_accents(" ".join(p for p in parts if p)).lower()


def _filter_tickets_by_query(rows: list[sqlite3.Row], q: str) -> list[sqlite3.Row]:
    tokens = [
        _strip_accents(tok).lower()
        for tok in q.split()
        if any(ch.isalnum() for ch in tok)
    ]
    if not tokens:
        return rows
    result = []
    for row in rows:
        haystack = _search_haystack(row)
        if all(tok in haystack for tok in tokens):
            result.append(row)
    return result


def get_dashboard_data(
    active_tab: str, filters: dict[str, str], allowed_categories: tuple[str, ...] | None = None
) -> tuple[list[sqlite3.Row], sqlite3.Row, dict[int, list[sqlite3.Row]]]:
    active_tab = normalize_tab(active_tab)
    where_clause, params = build_dashboard_query(active_tab, filters, allowed_categories)
    order_by = params.pop()
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        tickets = conn.execute(
            f"""
            SELECT
              tickets.*,
              request_types.name AS category_name,
              users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE {where_clause}
            ORDER BY {order_by}
            """,
            params,
        ).fetchall()
        if filters.get("q"):
            tickets = _filter_tickets_by_query(tickets, filters["q"])
        summary_scope = ""
        summary_params: tuple[Any, ...] = ()
        if allowed_categories is not None:
            summary_scope = f"WHERE category_id IN (SELECT id FROM request_types WHERE name IN ({','.join('?' for _ in allowed_categories)}))"
            summary_params = tuple(allowed_categories)
        summary = conn.execute(
            f"""
            SELECT
              SUM(CASE WHEN status = 'Aberto' THEN 1 ELSE 0 END) AS open_count,
              SUM(CASE WHEN status IN ('Em analise', 'Em atendimento', 'Aguardando retorno') THEN 1 ELSE 0 END) AS progress_count,
              SUM(CASE WHEN status = 'Concluido' THEN 1 ELSE 0 END) AS done_count,
              COUNT(*) AS total_count
            FROM tickets
            {summary_scope}
            """,
            summary_params,
        ).fetchone()
        events_by_ticket: dict[int, list[sqlite3.Row]] = {}
        if tickets:
            placeholders = ",".join("?" for _ in tickets)
            event_rows = conn.execute(
                f"""
                SELECT ticket_events.*, users.name AS actor_name
                FROM ticket_events
                LEFT JOIN users ON users.id = ticket_events.actor_user_id
                WHERE ticket_id IN ({placeholders})
                ORDER BY created_at DESC, id DESC
                """,
                [row["id"] for row in tickets],
            ).fetchall()
            for event in event_rows:
                events_by_ticket.setdefault(event["ticket_id"], []).append(event)
    return tickets, summary, events_by_ticket


def get_public_dashboard_snapshot(
    limit_per_column: int = 6,
    user: sqlite3.Row | dict[str, Any] | None = None,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    company_filter = visible_companies_for_user(user)
    visibility_clause = ""
    visibility_params: list[Any] = []
    if company_filter:
        placeholders = ",".join("?" for _ in company_filter)
        visibility_clause = f" AND tickets.company_name IN ({placeholders})"
        visibility_params.extend(company_filter)
    category_scope = user_ticket_category_scope(user)
    if category_scope is not None:
        visibility_clause += f" AND request_types.name IN ({','.join('?' for _ in category_scope)})"
        visibility_params.extend(category_scope)
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        in_progress = conn.execute(
            f"""
            SELECT
              tickets.id,
              tickets.public_token,
              tickets.title,
              tickets.description,
              tickets.status,
              tickets.updated_at,
              tickets.company_name,
              request_types.name AS category_name,
              users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.status IN ('Em analise', 'Em atendimento', 'Aguardando retorno')
              {visibility_clause}
            ORDER BY tickets.updated_at DESC, tickets.id DESC
            LIMIT ?
            """,
            (*visibility_params, limit_per_column),
        ).fetchall()
        completed = conn.execute(
            f"""
            SELECT
              tickets.id,
              tickets.public_token,
              tickets.title,
              tickets.description,
              tickets.status,
              tickets.updated_at,
              tickets.company_name,
              request_types.name AS category_name,
              users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.status = 'Concluido'
              {visibility_clause}
            ORDER BY tickets.updated_at DESC, tickets.id DESC
            LIMIT ?
            """,
            (*visibility_params, limit_per_column),
        ).fetchall()
    return in_progress, completed


def queue_email_to_outbox(message: EmailMessage, slug: str) -> Path:
    MAIL_OUTBOX_PATH.mkdir(parents=True, exist_ok=True)
    file_name = f"{utc_now().strftime('%Y%m%d-%H%M%S')}-{slug}.eml"
    file_path = MAIL_OUTBOX_PATH / file_name
    file_path.write_bytes(message.as_bytes())
    return file_path


def deliver_email(message: EmailMessage, slug: str) -> str:
    if not SMTP_HOST:
        queue_email_to_outbox(message, slug)
        return "outbox"

    try:
        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as client:
                if SMTP_USERNAME:
                    client.login(SMTP_USERNAME, SMTP_PASSWORD)
                client.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as client:
                client.ehlo()
                if SMTP_USE_TLS:
                    client.starttls()
                    client.ehlo()
                if SMTP_USERNAME:
                    client.login(SMTP_USERNAME, SMTP_PASSWORD)
                client.send_message(message)
        return "smtp"
    except Exception:
        logger.exception("Falha ao enviar e-mail. Gravando cópia no outbox.")
        queue_email_to_outbox(message, slug)
        return "outbox"


def send_ticket_confirmation(ticket_id: int) -> str:
    with get_db() as conn:
        ticket = conn.execute(
            """
            SELECT
              tickets.id,
              tickets.requester_name,
              tickets.requester_email,
              tickets.company_name,
              tickets.title,
              tickets.description,
              tickets.public_token,
              request_types.name AS category_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            WHERE tickets.id = ?
            """,
            (ticket_id,),
        ).fetchone()
    if not ticket or not ticket["requester_email"]:
        return "skipped"

    public_link = f"{APP_BASE_URL}/acompanhar/{ticket['public_token']}"
    message = EmailMessage()
    message["Subject"] = f"Confirmação da solicitação #{ticket['id']}"
    message["From"] = MAIL_FROM
    message["To"] = ticket["requester_email"]
    message["Cc"] = ", ".join(TICKET_CC_ALWAYS)
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    message.set_content(
        "\n".join(
            [
                f"Olá, {ticket['requester_name']}.",
                "",
                f"Sua solicitação #{ticket['id']} foi registrada com sucesso.",
                f"Grupo: {ticket['category_name']}",
                f"Empresa: {ticket['company_name']}",
                f"Título: {ticket['title']}",
                f"Resumo: {ticket['description']}",
                "",
                "Acompanhe o andamento pelo link abaixo:",
                public_link,
                "",
                "Esta é uma mensagem automática do Portal DP.",
            ]
        )
    )
    slug = f"ticket-confirmation-{ticket['id']}"
    return deliver_email(message, slug)


def send_ticket_opened_notification(ticket_id: int) -> str:
    if not TICKET_OPENED_NOTIFY_EMAILS:
        return "skipped"

    with get_db() as conn:
        ticket = conn.execute(
            """
            SELECT
              tickets.id,
              tickets.requester_name,
              tickets.requester_email,
              tickets.company_name,
              tickets.title,
              tickets.description,
              tickets.priority,
              tickets.public_token,
              request_types.name AS category_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            WHERE tickets.id = ?
            """,
            (ticket_id,),
        ).fetchone()
    if not ticket:
        return "skipped"

    internal_link = f"{APP_BASE_URL}/dp"
    message = EmailMessage()
    message["Subject"] = f"Nova solicitação aberta #{ticket['id']} - {ticket['category_name']}"
    message["From"] = MAIL_FROM
    message["To"] = ", ".join(TICKET_OPENED_NOTIFY_EMAILS)
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    message.set_content(
        "\n".join(
            [
                "Uma nova solicitação foi aberta no Portal DP.",
                "",
                f"Número: #{ticket['id']}",
                f"Grupo: {ticket['category_name']}",
                f"Empresa: {ticket['company_name']}",
                f"Solicitante: {ticket['requester_name']} ({ticket['requester_email'] or 'sem e-mail'})",
                f"Prioridade: {ticket['priority']}",
                f"Título: {ticket['title']}",
                f"Resumo: {ticket['description']}",
                "",
                "Acesse a fila interna para atender:",
                internal_link,
                "",
                "Esta é uma mensagem automática do Portal DP.",
            ]
        )
    )
    slug = f"ticket-opened-notify-{ticket['id']}"
    return deliver_email(message, slug)


def send_user_invite_email(invite_id: int) -> str:
    with get_db() as conn:
        invite = conn.execute(
            """
            SELECT user_invites.*, users.name AS inviter_name, invite_user.name AS user_name
            FROM user_invites
            LEFT JOIN users ON users.id = user_invites.invited_by_user_id
            LEFT JOIN users AS invite_user ON invite_user.id = user_invites.user_id
            WHERE user_invites.id = ?
            """,
            (invite_id,),
        ).fetchone()
    if not invite:
        return "skipped"

    invite_link = f"{APP_BASE_URL}/convites/{invite['invite_token']}"
    message = EmailMessage()
    message["Subject"] = "Convite para acesso ao Portal DP"
    message["From"] = MAIL_FROM
    message["To"] = invite["email"]
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    role_label = user_role_label(invite["role"])
    message.set_content(
        "\n".join(
            [
                f"Olá, {invite['user_name'] or invite['email']}.",
                "",
                f"Você recebeu um convite para acessar o Portal DP como {role_label}.",
                f"Convite enviado por: {invite['inviter_name'] or 'Departamento Pessoal'}.",
                "",
                "Use o link abaixo para cadastrar sua senha:",
                invite_link,
                "",
                "Se você não esperava este convite, ignore esta mensagem.",
            ]
        )
    )
    slug = f"user-invite-{invite_id}"
    return deliver_email(message, slug)


def send_ticket_update_notification(ticket_id: int, changes: list[str], extra_recipients: list[str] | None = None) -> str:
    if not changes:
        return "skipped"

    with get_db() as conn:
        ticket = conn.execute(
            """
            SELECT
              tickets.id,
              tickets.requester_name,
              tickets.requester_email,
              tickets.company_name,
              tickets.title,
              tickets.description,
              tickets.status,
              tickets.public_token,
              users.name AS assigned_name,
              request_types.name AS category_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.id = ?
            """,
            (ticket_id,),
        ).fetchone()
    if not ticket or not ticket["requester_email"]:
        return "skipped"

    public_link = f"{APP_BASE_URL}/acompanhar/{ticket['public_token']}"
    message = EmailMessage()
    message["Subject"] = f"Atualização da solicitação #{ticket['id']}"
    message["From"] = MAIL_FROM
    message["To"] = ticket["requester_email"]
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    if extra_recipients:
        message["Cc"] = ", ".join(extra_recipients)
    message.set_content(
        "\n".join(
            [
                f"Olá, {ticket['requester_name']}.",
                "",
                f"Sua solicitação #{ticket['id']} recebeu uma atualização.",
                "",
                "Dados da solicitação:",
                f"Grupo: {ticket['category_name']}",
                f"Empresa: {ticket['company_name']}",
                f"Status atual: {ticket_status_label(ticket['status'])}",
                f"Título: {ticket['title']}",
                f"Responsável atual: {ticket['assigned_name'] or 'Equipe DP'}",
                "",
                "Alterações realizadas:",
                *[f"- {change}" for change in changes],
                "",
                "Acompanhe o andamento pelo link abaixo:",
                public_link,
                "",
                "Esta é uma mensagem automática do Portal DP.",
            ]
        )
    )
    slug = f"ticket-update-{ticket['id']}"
    return deliver_email(message, slug)


def update_ticket(
    ticket_id: int,
    form: dict[str, str],
    actor_user_id: int,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    status = form.get("status", "").strip()
    comment_html = sanitize_rich_text(form.get("comment", "").strip())
    comment_text = rich_text_to_plain(comment_html).strip()
    assign_to_me = form.get("assign_to_me") == "1"
    assign_selected = form.get("assign_selected") == "1"
    assign_to_user_id_raw = form.get("assign_to_user_id", "").strip()
    valid_attachments = [a for a in (attachments or []) if a.get("filename") and a.get("content")]
    has_attachment = bool(valid_attachments)
    pix_requested = form.get("pix_send") == "1"
    if status not in VALID_TICKET_STATUSES:
        return False, "Status inválido para atualização do chamado."
    if status == "Aguardando retorno" and not comment_text and not pix_requested:
        return False, "Inclua uma resposta ao solicitante ao colocar o chamado em aguardando retorno."
    named_attachments = [a for a in (attachments or []) if a.get("filename")]
    if len(named_attachments) > ATTACHMENT_MAX_COUNT:
        return False, f"Envie no máximo {ATTACHMENT_MAX_COUNT} anexos por vez."
    for attachment in named_attachments:
        if not attachment.get("content"):
            return False, "Não foi possível ler o anexo enviado."
        if int(attachment.get("size_bytes", 0)) > ATTACHMENT_MAX_BYTES:
            return False, f"O anexo \"{Path(attachment['filename']).name}\" excede o tamanho máximo permitido."
    with get_db() as conn:
        ticket = conn.execute(
            """
            SELECT tickets.*, request_types.name AS category_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            WHERE tickets.id = ?
            """,
            (ticket_id,),
        ).fetchone()
        if not ticket:
            return False, "Chamado não encontrado."
        if ticket["status"] == "Concluido" and status == "Concluido" and (comment_text or has_attachment or pix_requested):
            status = "Em atendimento"
        # origem com tarefa no SharePoint: anexos enviados AO CONCLUIR vao so pela tarefa (ocultos ao cliente)
        sharepoint_config = get_origin_sharepoint_config(conn, ticket["category_id"])
        hide_attachments = bool(
            sharepoint_config and has_attachment and status == "Concluido" and ticket["status"] != "Concluido"
        )
        # chave Pix (so Beta/Alfa em Folha mensal/Ferias/Rescisao): valida agora, grava so se a resposta for salva
        pix_entries: list[dict[str, Any]] = []
        if pix_requested:
            if not pix_enabled_for(ticket["category_name"], ticket["request_category"]):
                return False, "O envio de chave Pix não se aplica a esta solicitação."
            pix_entries, pix_error = resolve_pix_entries(conn, ticket, form)
            if pix_error:
                return False, pix_error
            pix_html, pix_text = build_pix_message(pix_entries)
            comment_html = f"{comment_html}<br><br>{pix_html}" if comment_text else pix_html
            comment_text = f"{comment_text}\n\n{pix_text}" if comment_text else pix_text
        extra_recipients = ticket_cc_emails_from_form(ticket["category_name"], form)
        assigned_to_user_id = ticket["assigned_to_user_id"]
        change_messages: list[str] = []
        assigned_user_name: str | None = None
        if assign_to_me:
            assigned_to_user_id = actor_user_id
        elif assign_selected:
            if not assign_to_user_id_raw:
                return False, "Selecione um agente DP para designar o atendimento."
            try:
                assigned_to_user_id = int(assign_to_user_id_raw)
            except ValueError:
                return False, "Usuário inválido para designação."
            assigned_user = conn.execute(
                "SELECT id, name FROM users WHERE id = ? AND role = 'dp' AND is_active = 1",
                (assigned_to_user_id,),
            ).fetchone()
            if not assigned_user:
                return False, "Selecione um agente DP válido para designar o atendimento."
            assigned_user_name = assigned_user["name"]
        changes_detected = (
            ticket["status"] != status
            or ticket["assigned_to_user_id"] != assigned_to_user_id
            or bool(comment_text)
            or has_attachment
        )
        if not changes_detected:
            return False, "Nenhuma alteração foi aplicada ao chamado."

        actor_name = conn.execute("SELECT name FROM users WHERE id = ?", (actor_user_id,)).fetchone()
        conn.execute(
            """
            UPDATE tickets
            SET status = ?, assigned_to_user_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, assigned_to_user_id, isoformat(utc_now()), ticket_id),
        )
        if ticket["status"] != status:
            change_messages.append(
                f"Status alterado de {ticket_status_label(ticket['status'])} para {ticket_status_label(status)}."
            )
            record_ticket_event(
                conn,
                ticket_id,
                "status",
                f"Status alterado de {ticket_status_label(ticket['status'])} para {ticket_status_label(status)}.",
                actor_user_id,
                ticket["status"],
                status,
            )
        if ticket["assigned_to_user_id"] != assigned_to_user_id:
            owner_name = assigned_user_name or (actor_name["name"] if actor_name else "Equipe DP")
            change_messages.append(f"Solicitação assumida por {owner_name}.")
            record_ticket_event(
                conn,
                ticket_id,
                "atribuicao",
                f"Solicitação assumida por {owner_name}.",
                actor_user_id,
            )
        created_files: list[Path] = []
        try:
            if comment_text or has_attachment:
                default_note = "Equipe DP enviou anexos." if len(valid_attachments) > 1 else "Equipe DP enviou um anexo."
                if hide_attachments:
                    default_note = "Equipe DP enviou documentos."
                event_message_html = comment_html if comment_text else default_note
                event_message_text = comment_text if comment_text else default_note
                if has_attachment and not hide_attachments:
                    names_text = ", ".join(Path(a["filename"]).name for a in valid_attachments)
                    names_html = ", ".join(html.escape(Path(a["filename"]).name) for a in valid_attachments)
                    event_message_html = f"{event_message_html} Anexo(s): {names_html}"
                    event_message_text = f"{event_message_text} Anexo(s): {names_text}"
                last_comment = conn.execute(
                    """
                    SELECT actor_user_id, message FROM ticket_events
                    WHERE ticket_id = ? AND event_type = 'comentario'
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (ticket_id,),
                ).fetchone()
                if (
                    last_comment
                    and last_comment["actor_user_id"] == actor_user_id
                    and last_comment["message"] == event_message_html[:2000]
                ):
                    return False, "Nenhuma alteração aplicada: esta resposta já havia sido enviada."
                if pix_entries:
                    apply_pix_entries(conn, ticket, pix_entries, actor_user_id)
                if has_attachment:
                    for attachment in valid_attachments:
                        created_files.append(
                            store_ticket_attachment(
                                conn, ticket_id, attachment,
                                uploaded_by_user_id=actor_user_id,
                                client_visible=not hide_attachments,
                            )
                        )
                change_messages.append(f"Resposta da equipe: {event_message_text}")
                record_ticket_event(conn, ticket_id, "comentario", event_message_html, actor_user_id)
                if hide_attachments:
                    create_sharepoint_task_request(conn, ticket, sharepoint_config, actor_user_id, created_files)
        except Exception:
            for path in created_files:
                _safe_unlink(path)
            raise
    send_ticket_update_notification(ticket_id, change_messages, extra_recipients)
    return True, "Chamado atualizado." + (" O envio dos documentos pela tarefa do portal foi agendado." if hide_attachments else "")


def delete_ticket(ticket_id: int) -> tuple[bool, str]:
    attachment_paths: list[Path] = []
    with get_db() as conn:
        ticket = conn.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not ticket:
            return False, "Chamado não encontrado."
        attachment_paths = [
            ATTACHMENTS_PATH / row["stored_name"]
            for row in conn.execute(
                "SELECT stored_name FROM ticket_attachments WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchall()
        ]
        conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    for path in attachment_paths:
        _safe_unlink(path)
    return True, "Chamado excluído."


def get_public_ticket(public_token: str) -> sqlite3.Row | None:
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        return conn.execute(
            """
            SELECT tickets.*, request_types.name AS category_name, users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.public_token = ?
            """,
            (public_token,),
        ).fetchone()


def get_public_ticket_token_by_id(ticket_id: int) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT public_token FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return row["public_token"] if row else None


def get_public_ticket_events(ticket_id: int) -> list[sqlite3.Row]:
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        return conn.execute(
            """
            SELECT ticket_events.created_at, ticket_events.event_type,
                   ticket_events.message, users.name AS actor_name
            FROM ticket_events
            LEFT JOIN users ON users.id = ticket_events.actor_user_id
            WHERE ticket_events.ticket_id = ?
              AND ticket_events.event_type IN ('abertura', 'status', 'comentario', 'solicitante')
            ORDER BY ticket_events.created_at DESC, ticket_events.id DESC
            """,
            (ticket_id,),
        ).fetchall()


TICKET_CLIENT_REPLY_NOTIFY_EMAIL = "dp@example.com"


def send_ticket_client_reply_notification(ticket_id: int) -> str:
    with get_db() as conn:
        ticket = conn.execute(
            """
            SELECT
              tickets.id,
              tickets.requester_name,
              tickets.requester_email,
              tickets.company_name,
              tickets.title,
              tickets.status,
              users.name AS assigned_name,
              request_types.name AS category_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.id = ?
            """,
            (ticket_id,),
        ).fetchone()
    if not ticket:
        return "skipped"

    internal_link = f"{APP_BASE_URL}/dp"
    message = EmailMessage()
    message["Subject"] = f"Nova resposta do solicitante - Solicitação #{ticket['id']}"
    message["From"] = MAIL_FROM
    message["To"] = TICKET_CLIENT_REPLY_NOTIFY_EMAIL
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    message.set_content(
        "\n".join(
            [
                "O solicitante respondeu a uma solicitação no Portal DP.",
                "",
                f"Número: #{ticket['id']}",
                f"Grupo: {ticket['category_name']}",
                f"Empresa: {ticket['company_name']}",
                f"Solicitante: {ticket['requester_name']} ({ticket['requester_email'] or 'sem e-mail'})",
                f"Status atual: {ticket_status_label(ticket['status'])}",
                f"Título: {ticket['title']}",
                f"Responsável atual: {ticket['assigned_name'] or 'Equipe DP'}",
                "",
                "Acesse a fila interna para responder:",
                internal_link,
                "",
                "Esta é uma mensagem automática do Portal DP.",
            ]
        )
    )
    slug = f"ticket-client-reply-{ticket['id']}"
    return deliver_email(message, slug)


def add_requester_comment(
    public_token: str,
    user: sqlite3.Row | dict[str, Any] | None,
    comment: str,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    if not user:
        return False, "Entre no portal para responder a esta solicitação."
    comment_html = sanitize_rich_text(comment.strip())
    comment_text = rich_text_to_plain(comment_html).strip()
    valid_attachments = [a for a in (attachments or []) if a.get("filename") and a.get("content")]
    has_attachment = bool(valid_attachments)
    if not comment_text and not has_attachment:
        return False, "Escreva um comentário ou envie um anexo antes de enviar."
    if len(comment_text) > 2000:
        return False, "O comentário deve ter no máximo 2000 caracteres."
    named_attachments = [a for a in (attachments or []) if a.get("filename")]
    if len(named_attachments) > ATTACHMENT_MAX_COUNT:
        return False, f"Envie no máximo {ATTACHMENT_MAX_COUNT} anexos por vez."
    for attachment in named_attachments:
        if not attachment.get("content"):
            return False, "Não foi possível ler o anexo enviado."
        if int(attachment.get("size_bytes", 0)) > ATTACHMENT_MAX_BYTES:
            return False, f"O anexo \"{Path(attachment['filename']).name}\" excede o tamanho máximo permitido."
    with get_db() as conn:
        ensure_ticket_submission_store(conn)
        ticket = conn.execute("SELECT id, status FROM tickets WHERE public_token = ?", (public_token,)).fetchone()
        if not ticket:
            return False, "Você não tem permissão para comentar nesta solicitação."
        if ticket["status"] == "Cancelado":
            return False, "Esta solicitação foi cancelada e não aceita novas respostas pelo acompanhamento público. O Departamento Pessoal pode responder pelo painel interno."
        message = f"Solicitante: {comment_html}" if comment_text else ("Solicitante enviou anexos." if len(valid_attachments) > 1 else "Solicitante enviou um anexo.")
        actor_user_id = user["id"] if user else None
        created_files: list[Path] = []
        try:
            if has_attachment:
                names = ", ".join(html.escape(Path(a["filename"]).name) for a in valid_attachments)
                message = f"{message} Anexo(s): {names}"
            last_comment = conn.execute(
                """
                SELECT actor_user_id, message FROM ticket_events
                WHERE ticket_id = ? AND event_type = 'solicitante'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (ticket["id"],),
            ).fetchone()
            if (
                last_comment
                and last_comment["actor_user_id"] == actor_user_id
                and last_comment["message"] == message[:2000]
            ):
                return False, "Esta mensagem já foi enviada."
            if has_attachment:
                for attachment in valid_attachments:
                    created_files.append(store_ticket_attachment(conn, ticket["id"], attachment))
            record_ticket_event(conn, ticket["id"], "solicitante", message, actor_user_id)
            if ticket["status"] == "Concluido":
                record_ticket_event(
                    conn,
                    ticket["id"],
                    "status",
                    f"Status alterado de {ticket_status_label(ticket['status'])} para {ticket_status_label('Em atendimento')} (reaberto por novo comentário do solicitante).",
                    actor_user_id,
                    ticket["status"],
                    "Em atendimento",
                )
                conn.execute(
                    "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
                    ("Em atendimento", isoformat(utc_now()), ticket["id"]),
                )
            else:
                conn.execute(
                    "UPDATE tickets SET updated_at = ? WHERE id = ?",
                    (isoformat(utc_now()), ticket["id"]),
                )
        except Exception:
            for path in created_files:
                _safe_unlink(path)
            raise
    send_ticket_client_reply_notification(ticket["id"])
    return True, "Comentário enviado."


def make_backup(test: bool = False) -> str:
    if DB_BACKEND != "sqlite":
        logger.info("Backup interno ignorado: backend postgres")
        return "skipped:postgres"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = TEST_BACKUP_DIR if test else BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{DB_PATH.stem}-{stamp}.db"
    with get_db() as source_conn:
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    return str(target)


def input_value(form_data: dict[str, str], field: str) -> str:
    return html.escape(form_data.get(field, ""))


def field_error(errors: dict[str, str], field: str) -> str:
    message = errors.get(field, "")
    if not message:
        return ""
    return f'<small class="field-error">{html.escape(message)}</small>'


def build_cookie(name: str, value: str, *, max_age: int | None = None, secure: bool = False, http_only: bool = True) -> str:
    parts = [f"{name}={value}", "Path=/", "SameSite=Lax"]
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    return "; ".join(parts)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def parse_client_ip(handler: Any) -> str:
    remote_addr = handler.client_address[0] if handler.client_address else ""
    forwarded = handler.headers.get("X-Forwarded-For", "")
    if forwarded and remote_addr in TRUSTED_PROXY_IPS:
        forwarded_ip = forwarded.split(",")[0].strip()
        if forwarded_ip:
            return forwarded_ip
    if remote_addr:
        return remote_addr
    return "unknown"
