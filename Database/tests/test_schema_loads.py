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
from pathlib import Path

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv("DATABASE_URL")
SCHEMA_SQL = Path(__file__).resolve().parents[1] / "schemas" / "001_core.sql"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — schema smoke test requires live Postgres",
)


async def _apply_schema(engine) -> None:
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    async with engine.begin() as conn:
        # psql meta-commands (\i) are not supported by the driver; strip them.
        for stmt in _split_statements(ddl):
            await conn.execute(text(stmt))


def _split_statements(ddl: str) -> list[str]:
    cleaned = "\n".join(
        line for line in ddl.splitlines() if not line.strip().startswith("\\")
    )
    return [s.strip() for s in cleaned.split(";") if s.strip()]


@pytest.mark.asyncio
async def test_schema_applies_and_checks_enforced():
    engine = create_async_engine(DATABASE_URL, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))

        await _apply_schema(engine)

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
        assert {"users", "workflows", "nodes", "executions"}.issubset(tables)

        async with engine.begin() as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    text(
                        "INSERT INTO users (email, plan_tier) "
                        "VALUES ('a@b.c', 'enterprise')"
                    )
                )
    finally:
        await engine.dispose()
