#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import pwd
import grp
import stat
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_config import ATTACHMENTS_PATH, BACKUP_DIR, BASE_DIR, DB_PATH, LOG_DIR, MAIL_OUTBOX_PATH


def owner_name(uid: int) -> str:
    return pwd.getpwuid(uid).pw_name


def group_name(gid: int) -> str:
    return grp.getgrgid(gid).gr_name


def oct_mode(path: Path) -> str:
    return oct(stat.S_IMODE(path.stat().st_mode))


def check_path(path: Path, expected_mode: int, expected_user: str | None, expected_group: str | None, problems: list[str]) -> None:
    if not path.exists():
        problems.append(f"ausente: {path}")
        return
    st = path.stat()
    current_mode = stat.S_IMODE(st.st_mode)
    if current_mode != expected_mode:
        problems.append(f"modo incorreto: {path} atual={oct(current_mode)} esperado={oct(expected_mode)}")
    if expected_user and owner_name(st.st_uid) != expected_user:
        problems.append(f"owner incorreto: {path} atual={owner_name(st.st_uid)} esperado={expected_user}")
    if expected_group and group_name(st.st_gid) != expected_group:
        problems.append(f"group incorreto: {path} atual={group_name(st.st_gid)} esperado={expected_group}")


def chmod_if_needed(path: Path, expected_mode: int) -> None:
    current_mode = stat.S_IMODE(path.stat().st_mode)
    if current_mode != expected_mode:
        path.chmod(expected_mode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida permissoes esperadas do runtime do PortalDP.")
    parser.add_argument("--fix", action="store_true", help="Aplica os modos esperados aos caminhos existentes.")
    parser.add_argument("--user", default=os.getenv("APP_RUNTIME_USER", "portaldp"))
    parser.add_argument("--group", default=os.getenv("APP_RUNTIME_GROUP", "portaldp"))
    args = parser.parse_args()

    targets = [
        (BASE_DIR / ".env", 0o640),
        (DB_PATH.parent, 0o750),
        (DB_PATH, 0o640),
        (BACKUP_DIR, 0o750),
        (LOG_DIR, 0o750),
        (ATTACHMENTS_PATH, 0o750),
        (MAIL_OUTBOX_PATH, 0o750),
    ]

    if args.fix:
        for path, mode in targets:
            if path.exists():
                chmod_if_needed(path, mode)

    problems: list[str] = []
    for path, mode in targets:
        check_path(path, mode, args.user, args.group, problems)

    if problems:
        for problem in problems:
            print(problem)
        return 1

    print("Permissoes de runtime validadas com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
