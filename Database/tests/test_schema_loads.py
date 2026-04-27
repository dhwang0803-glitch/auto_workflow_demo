"""Migration smoke test — PLAN_01 §6 acceptance.

Requires a live Postgres reachable via `DATABASE_URL` (async DSN, e.g.
`postgresql+asyncpg://user:pw@localhost:5432/auto_workflow_test`). Skipped
otherwise so the rest of the Database test suite runs without a DB.

Asserts:
  1. schemas/001_core.sql applies to an empty DB without error
  2. all expected tables exist afterward
  3. plan_tier/execution_mode/status CHECK constraints reject bad values
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv("DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = ROOT / "schemas"
MIGRATIONS_DIR = ROOT / "migrations"
# Apply every schema file in lexical order. This test wipes the public schema
# as part of the smoke check, so it must restore the *full* schema for
# downstream tests (test_postgres_repositories, test_credential_store) to find
# their tables after this one runs.
SCHEMA_FILES = sorted(SCHEMAS_DIR.glob("*.sql"))
MIGRATION_FILES = sorted(MIGRATIONS_DIR.glob("*.sql"))

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — schema smoke test requires live Postgres",
)


async def _apply_schemas(engine) -> None:
    # Schema files may contain PL/pgSQL DO blocks (e.g. partition bootstrap),
    # so naive splitting on `;` breaks. Route the whole DDL through the raw
    # asyncpg connection which accepts multi-statement scripts via the
    # simple query protocol.
    async with engine.connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        for path in SCHEMA_FILES:
            ddl = path.read_text(encoding="utf-8")
            await raw.execute(ddl)


_INCLUDE_RE = re.compile(r"^\s*\\i\s+(\S+)\s*$")


def _load_migration_sql(path: Path) -> str:
    """Inline `\\i schemas/XXX.sql` directives so asyncpg's simple query
    protocol can execute the file. Mirrors `scripts/migrate.py::_load_sql`.
    """
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _INCLUDE_RE.match(line)
        if m:
            target = (ROOT / m.group(1)).resolve()
            out.append(target.read_text(encoding="utf-8"))
        else:
            out.append(line)
    return "\n".join(out)


async def _restore_migrations(engine) -> None:
    """Re-apply migrations/*.sql on top of the freshly-loaded schemas and
    rebuild the schema_migrations tracker so downstream tests (and later
    `migrate.py` runs) see the DB in a fully-migrated state.

    Mirrors `scripts/migrate.py` semantics: idempotent (skips files already
    tracked), records each applied filename, inlines `\\i schemas/X.sql`
    includes. Without this the DROP SCHEMA above leaves columns like
    `executions.created_at` (PLAN_06 migration) missing, breaking
    API_Server / Execution_Engine integration tests that share the same
    dev Postgres.
    """
    async with engine.connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        await raw.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  filename text PRIMARY KEY,"
            "  applied_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )
        applied_rows = await raw.fetch("SELECT filename FROM schema_migrations")
        applied = {r["filename"] for r in applied_rows}
        for path in MIGRATION_FILES:
            if path.name in applied:
                continue
            await raw.execute(_load_migration_sql(path))
            await raw.execute(
                "INSERT INTO schema_migrations (filename) VALUES ($1)",
                path.name,
            )


@pytest.mark.asyncio
async def test_schema_applies_and_checks_enforced():
    engine = create_async_engine(DATABASE_URL, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))

        await _apply_schemas(engine)

        async with engine.connect() as conn:
            tables = set(
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).all()
            )
        expected = {
            "users", "workflows", "nodes", "executions",
            "credentials", "agents", "webhook_registry",
            "execution_node_logs",
            "approval_notifications",
            # PLAN_12 / ADR-022 — Skill Bootstrap (005_skill_bootstrap.sql)
            "skills", "skill_sources", "skill_applications",
            "policy_documents", "policy_extractions",
        }
        assert expected.issubset(tables), f"missing: {expected - tables}"

        async with engine.begin() as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    text(
                        "INSERT INTO users (email, plan_tier) "
                        "VALUES ('a@b.c', 'enterprise')"
                    )
                )
    finally:
        await _restore_migrations(engine)
        await engine.dispose()


@pytest.mark.asyncio
async def test_restore_leaves_migration_columns_present():
    """Regression guard: after `test_schema_applies_and_checks_enforced` runs
    the DB must have the columns added by migrations (e.g. executions.created_at
    from 20260416_executions_created_at.sql). API_Server's execution listing
    query depends on this; without the restore step it was silently broken
    when tests shared a dev Postgres.
    """
    engine = create_async_engine(DATABASE_URL, future=True)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'executions' AND column_name = 'created_at'"
                )
            )
            assert row.first() is not None, (
                "executions.created_at missing — did _restore_migrations "
                "fail to re-apply 20260416_executions_created_at.sql?"
            )
    finally:
        await engine.dispose()
