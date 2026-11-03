#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_services import cleanup_orphan_attachment_files, list_orphan_attachment_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita e limpa anexos orfaos do PortalDP.")
    parser.add_argument("--delete", action="store_true", help="Remove os arquivos orfaos encontrados.")
    args = parser.parse_args()

    orphan_paths = cleanup_orphan_attachment_files() if args.delete else list_orphan_attachment_files()
    if not orphan_paths:
        print("Nenhum anexo orfao encontrado.")
        return 0

    action = "Removido" if args.delete else "Encontrado"
    for path in orphan_paths:
        print(f"{action}: {path}")
    return 0 if args.delete else 1


if __name__ == "__main__":
    raise SystemExit(main())
