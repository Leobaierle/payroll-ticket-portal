#!/usr/bin/env python3
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app_config import DB_BACKEND, PORT
from app_db import get_db


def check_http() -> dict[str, object]:
    url = os.getenv("APP_HEALTHCHECK_URL", f"http://127.0.0.1:{PORT}/healthz")
    timeout = float(os.getenv("APP_HEALTHCHECK_TIMEOUT", "5"))
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        return {
            "url": url,
            "status_code": response.status,
            "payload": payload,
        }


def check_db() -> dict[str, object]:
    with get_db() as conn:
        ticket_count = int(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
    return {
        "backend": DB_BACKEND,
        "ticket_count": ticket_count,
    }


def check_backup() -> dict[str, object]:
    command = os.getenv("APP_BACKUP_CHECK_COMMAND", "").strip()
    backup_glob = os.getenv("APP_BACKUP_CHECK_GLOB", "").strip()
    max_age_hours = float(os.getenv("APP_BACKUP_MAX_AGE_HOURS", "30"))

    if command:
        completed = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        return {
            "mode": "command",
            "command": command,
            "output": completed.stdout.strip(),
        }

    if backup_glob:
        candidates = [path for path in glob.glob(backup_glob) if os.path.isfile(path)]
        if not candidates:
            raise RuntimeError(f"Nenhum backup encontrado para o glob: {backup_glob}")
        latest = max(candidates, key=os.path.getmtime)
        age_hours = (time.time() - os.path.getmtime(latest)) / 3600
        if age_hours > max_age_hours:
            raise RuntimeError(f"Backup mais recente com idade de {age_hours:.1f}h excede o limite de {max_age_hours:.1f}h")
        return {
            "mode": "glob",
            "path": latest,
            "age_hours": round(age_hours, 2),
        }

    return {
        "mode": "skipped",
        "reason": "APP_BACKUP_CHECK_COMMAND e APP_BACKUP_CHECK_GLOB nao definidos",
    }


def main() -> int:
    try:
        http_info = check_http()
        db_info = check_db()
        backup_info = check_backup()
    except (urllib.error.URLError, json.JSONDecodeError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"healthcheck: error: {exc}")
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "http": http_info,
                "db": db_info,
                "backup": backup_info,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
