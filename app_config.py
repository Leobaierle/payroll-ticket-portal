from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "db"
LOG_DIR = BASE_DIR / "logs"
MIGRATIONS_DIR = DB_DIR / "migrations"
MIGRATIONS_PG_DIR = DB_DIR / "migrations_pg"
BACKUP_DIR = DB_DIR / "backups"
TEST_BACKUP_DIR = DB_DIR / "backups_test"
OUTBOX_DIR = BASE_DIR / "outbox"
TEST_OUTBOX_DIR = BASE_DIR / "outbox_test"
ATTACHMENTS_DIR = BASE_DIR / "attachments"
TEST_ATTACHMENTS_DIR = BASE_DIR / "attachments_test"
DEFAULT_DB_PATH = DB_DIR / "helpdesk.db"


def load_env_file(path: Path) -> None:
    try:
        if not path.exists():
            return
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value

DEFAULT_SECRETS_ENV_PATH = Path("/etc/portaldp/portaldp.secrets.env")
SECRETS_ENV_PATH = Path(os.getenv("APP_SECRETS_ENV_FILE", str(DEFAULT_SECRETS_ENV_PATH)))

load_env_file(SECRETS_ENV_PATH)
load_env_file(BASE_DIR / ".env")

APP_ENV = os.getenv("APP_ENV", "development").strip().lower() or "development"
PORT = int(os.getenv("PORT", "8000"))
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").strip().lower() or "sqlite"
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/Sao_Paulo").strip() or "America/Sao_Paulo"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PG_POOL_MIN = int(os.getenv("PG_POOL_MIN", "2"))
PG_POOL_MAX = int(os.getenv("PG_POOL_MAX", "5"))
DB_PATH = Path(os.getenv("APP_DB_PATH", str(DEFAULT_DB_PATH)))
MAIL_OUTBOX_PATH = Path(os.getenv("APP_OUTBOX_PATH", str(TEST_OUTBOX_DIR if APP_ENV == "test" else OUTBOX_DIR)))
ATTACHMENTS_PATH = Path(os.getenv("APP_ATTACHMENTS_PATH", str(TEST_ATTACHMENTS_DIR if APP_ENV == "test" else ATTACHMENTS_DIR)))
USE_SECURE_COOKIE = os.getenv("USE_SECURE_COOKIE", "0").strip().lower() in {"1", "true", "yes", "on"}
ATTACHMENT_MAX_BYTES = int(os.getenv("ATTACHMENT_MAX_BYTES", str(40 * 1024 * 1024)))
ATTACHMENT_MAX_COUNT = int(os.getenv("ATTACHMENT_MAX_COUNT", "10"))

LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "5"))
LOGIN_RATE_LIMIT_WINDOW = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW", "300"))

APP_BASE_URL = (os.getenv("APP_BASE_URL", f"http://localhost:{PORT}").strip().rstrip("/")) or f"http://localhost:{PORT}"
MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@helpdesk.local").strip() or "no-reply@helpdesk.local"
MAIL_REPLY_TO = os.getenv("MAIL_REPLY_TO", "").strip()
TICKET_OPENED_NOTIFY_EMAILS = [
    item.strip()
    for item in os.getenv(
        "TICKET_OPENED_NOTIFY_EMAILS",
        "laura.dias@example.com,karen@example.com",
    ).split(",")
    if item.strip()
]
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1").strip().lower() in {"1", "true", "yes", "on"}
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "0").strip().lower() in {"1", "true", "yes", "on"}
TRUSTED_PROXY_IPS = {
    item.strip()
    for item in os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if item.strip()
}

SESSION_COOKIE = "helpdesk_session"
CSRF_COOKIE = "helpdesk_csrf"
