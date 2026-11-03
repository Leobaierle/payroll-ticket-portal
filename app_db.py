from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from app_config import (
    ATTACHMENTS_PATH,
    BACKUP_DIR,
    DATABASE_URL,
    DB_BACKEND,
    DB_PATH,
    LOG_DIR,
    MIGRATIONS_DIR,
    MIGRATIONS_PG_DIR,
    PG_POOL_MAX,
    PG_POOL_MIN,
    TEST_BACKUP_DIR,
)


LOG_PATH = LOG_DIR / "app.log"
logger = logging.getLogger("helpdesk")


class IntegrityError(Exception):
    """Backend-agnostic integrity error wrapper levantada pelo shim Postgres."""


DBIntegrityError: tuple[type[BaseException], ...] = (sqlite3.IntegrityError, IntegrityError)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


def ensure_directories() -> None:
    if DB_BACKEND == "sqlite":
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    TEST_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ATTACHMENTS_PATH.mkdir(parents=True, exist_ok=True)


_pg_pool = None
_psycopg = None


def _ensure_pg_imports() -> None:
    global _psycopg
    if _psycopg is not None:
        return
    import psycopg  # type: ignore

    _psycopg = psycopg


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        _ensure_pg_imports()
        from psycopg_pool import ConnectionPool  # type: ignore

        if not DATABASE_URL:
            raise RuntimeError("DB_BACKEND=postgres exige DATABASE_URL no ambiente")
        _pg_pool = ConnectionPool(
            DATABASE_URL,
            min_size=PG_POOL_MIN,
            max_size=PG_POOL_MAX,
            open=True,
        )
    return _pg_pool


def _release_pg_connection(raw_conn, *, broken: bool) -> None:
    pool = _get_pg_pool()
    if broken or getattr(raw_conn, "closed", False):
        try:
            raw_conn.close()
        except Exception:
            pass
        return
    pool.putconn(raw_conn)


def _borrow_pg_connection():
    pool = _get_pg_pool()
    from psycopg.rows import dict_row  # type: ignore

    raw = pool.getconn()
    try:
        if raw.autocommit:
            raw.autocommit = False
        raw.row_factory = dict_row
        raw.execute("SELECT 1")
        return raw
    except Exception:
        _release_pg_connection(raw, broken=True)
        replacement = pool.getconn()
        if replacement.autocommit:
            replacement.autocommit = False
        replacement.row_factory = dict_row
        replacement.execute("SELECT 1")
        return replacement


def _translate_placeholders(sql: str) -> str:
    out: list[str] = []
    in_single = False
    in_double = False
    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


class _RowDictWrapper(dict):
    __slots__ = ("_keys",)

    def __init__(self, mapping: dict[str, Any]):
        super().__init__(mapping)
        self._keys = tuple(mapping.keys())

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, int):
            return super().__getitem__(self._keys[key])
        return super().__getitem__(key)

    def keys(self):  # type: ignore[override]
        return self._keys


class _PgCursorShim:
    def __init__(self, raw_cursor, lastrowid: int | None = None) -> None:
        self._cur = raw_cursor
        self._lastrowid = lastrowid

    def fetchone(self):
        row = self._cur.fetchone()
        return _RowDictWrapper(row) if row else None

    def fetchall(self):
        return [_RowDictWrapper(row) for row in self._cur.fetchall()]

    def __iter__(self):
        for row in self._cur:
            yield _RowDictWrapper(row)

    @property
    def lastrowid(self) -> int | None:
        return self._lastrowid

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class _PgConnShim:
    def __init__(self, raw_conn) -> None:
        self._conn = raw_conn

    def execute(self, sql: str, params: Any = ()) -> _PgCursorShim:
        translated = _translate_placeholders(sql)
        cur = self._conn.cursor()
        try:
            stripped = translated.lstrip()
            upper = stripped.upper()
            if upper.startswith("INSERT") and "RETURNING" not in upper:
                returning_sql = translated.rstrip(";\n\t ") + " RETURNING id"
                try:
                    cur.execute(returning_sql, params)
                    row = cur.fetchone()
                    last = row.get("id") if isinstance(row, dict) and row else None
                    return _PgCursorShim(cur, lastrowid=last)
                except _psycopg.errors.UndefinedColumn:  # type: ignore[attr-defined]
                    self._conn.rollback()
                    cur = self._conn.cursor()
                    cur.execute(translated, params)
                    return _PgCursorShim(cur)
            cur.execute(translated, params)
            return _PgCursorShim(cur)
        except _psycopg.errors.IntegrityError as exc:  # type: ignore[attr-defined]
            raise IntegrityError(str(exc)) from exc

    def executescript(self, sql: str) -> None:
        self._conn.execute(sql)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        broken = bool(getattr(self._conn, "closed", False))
        if exc_type is None:
            try:
                self._conn.commit()
            except Exception:
                broken = True
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                raise
        else:
            try:
                self._conn.rollback()
            except Exception:
                broken = True
        _release_pg_connection(self._conn, broken=broken)


def get_db():
    if DB_BACKEND == "postgres":
        raw = _borrow_pg_connection()
        return _PgConnShim(raw)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema() -> None:
    ensure_directories()
    if DB_BACKEND == "postgres":
        from psycopg.rows import dict_row  # type: ignore

        pool = _get_pg_pool()
        with pool.connection() as conn:
            conn.row_factory = dict_row
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version TEXT PRIMARY KEY,
                  applied_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"')
                )
                """
            )
            applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for migration_path in sorted(Path(MIGRATIONS_PG_DIR).glob("*.sql")):
                version = migration_path.name
                if version in applied:
                    continue
                conn.execute(migration_path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            conn.commit()
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for migration_path in sorted(Path(MIGRATIONS_DIR).glob("*.sql")):
            version = migration_path.name
            if version in applied:
                continue
            conn.executescript(migration_path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
