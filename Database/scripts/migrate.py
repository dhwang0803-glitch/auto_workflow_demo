"""Minimal forward-only migration runner.

Applies every `migrations/YYYYMMDD_*.sql` exactly once, tracked in a
`schema_migrations` table. Intentionally tiny — Alembic can come later once
the ORM starts generating autogen diffs.

Usage:
    python Database/scripts/migrate.py           # apply pending
    python Database/scripts/migrate.py --status  # list applied vs pending

Env: `DATABASE_URL_SYNC` (psycopg DSN). Falls back to `DATABASE_URL` with the
`+asyncpg` driver stripped.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "migrations"
SCHEMAS_DIR = ROOT / "schemas"

CREATE_TRACKING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename     text        PRIMARY KEY,
    applied_at   timestamptz NOT NULL DEFAULT now()
);
"""


def _dsn() -> str:
    dsn = os.getenv("DATABASE_URL_SYNC")
    if dsn:
        return dsn
    async_dsn = os.getenv("DATABASE_URL")
    if not async_dsn:
        sys.exit("DATABASE_URL_SYNC or DATABASE_URL must be set")
    return async_dsn.replace("+asyncpg", "").replace("+psycopg", "")


def _load_sql(path: Path) -> str:
    """Inline `\\i schemas/NNN.sql` directives so the driver can execute the file."""
    out: list[str] = []
    include = re.compile(r"^\s*\\i\s+(\S+)\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = include.match(line)
        if m:
            target = (ROOT / m.group(1)).resolve()
            out.append(target.read_text(encoding="utf-8"))
        else:
            out.append(line)
    return "\n".join(out)


def _discover() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(CREATE_TRACKING)
        cur.execute("SELECT filename FROM schema_migrations")
        return {r[0] for r in cur.fetchall()}


def cmd_status() -> None:
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        applied = _applied(conn)
    for path in _discover():
        mark = "✓" if path.name in applied else "·"
        print(f"{mark} {path.name}")


def cmd_apply() -> None:
    dsn = _dsn()
    with psycopg.connect(dsn, autocommit=False) as conn:
        applied = _applied(conn)
        conn.commit()
        pending = [p for p in _discover() if p.name not in applied]
        if not pending:
            print("no pending migrations")
            return
        for path in pending:
            print(f"applying {path.name} ...", flush=True)
            sql = _load_sql(path)
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)",
                    (path.name,),
                )
            conn.commit()
            print(f"  ok {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        cmd_status()
    else:
        cmd_apply()


if __name__ == "__main__":
    main()
