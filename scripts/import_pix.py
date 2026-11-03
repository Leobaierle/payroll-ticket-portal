#!/usr/bin/env python3
"""Importa chaves Pix de funcionarios para a tabela pix_keys de uma origem (Beta ou Alfa).

Uso (com o ambiente do portal carregado):
    python3 scripts/import_pix.py <arquivo.csv> --origem Beta [--dry-run]

O CSV tem duas colunas (nome, chave), com ou sem cabecalho "Nome,PIX". Os valores entram COMO ESTAO
(so aparas nas pontas): nenhuma chave e corrigida ou completada. Funcionario ja cadastrado na origem
e ignorado, nunca sobrescrito (correcoes feitas pelo DP no portal nao se perdem).

O arquivo de dados NAO deve ser versionado: contem CPF/telefone de funcionarios.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_db import ensure_schema, get_db  # noqa: E402
from app_services import isoformat, pix_name_key, utc_now  # noqa: E402


def read_rows(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for index, record in enumerate(csv.reader(handle)):
            if not record or not (record[0] or "").strip():
                continue
            name = record[0].strip()
            key = (record[1] if len(record) > 1 else "").strip()
            if index == 0 and name.lower() == "nome":
                continue
            rows.append((name, key))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--origem", required=True, help="Nome da origem (ex.: Beta, Alfa)")
    parser.add_argument("--dry-run", action="store_true", help="Mostra o que faria, sem gravar")
    args = parser.parse_args()

    ensure_schema()
    rows = read_rows(args.arquivo)
    inserted = skipped_existing = skipped_dup = 0
    seen: set[str] = set()
    with get_db() as conn:
        origin = conn.execute(
            "SELECT id, name FROM request_types WHERE LOWER(name) = LOWER(?)", (args.origem,)
        ).fetchone()
        if not origin:
            print(f"Origem nao encontrada: {args.origem}", file=sys.stderr)
            return 1
        now = isoformat(utc_now())
        for name, key in rows:
            name_key = pix_name_key(name)
            if name_key in seen:
                skipped_dup += 1
                continue
            seen.add(name_key)
            exists = conn.execute(
                "SELECT id FROM pix_keys WHERE category_id = ? AND name_key = ?", (origin["id"], name_key)
            ).fetchone()
            if exists:
                skipped_existing += 1
                continue
            inserted += 1
            if not args.dry_run:
                conn.execute(
                    "INSERT INTO pix_keys (category_id, employee_name, name_key, pix_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (origin["id"], name, name_key, key, now, now),
                )
    modo = "DRY-RUN (nada gravado)" if args.dry_run else "gravado"
    print(f"Origem {origin['name']}: lidos={len(rows)} novos={inserted} ja_existentes={skipped_existing} duplicados_no_arquivo={skipped_dup} [{modo}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
