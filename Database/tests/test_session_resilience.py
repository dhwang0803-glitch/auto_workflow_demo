"""PLAN_07 — resilience + query logging at the engine layer.

Integration tests against a live Postgres (same `DATABASE_URL` gating as
`test_postgres_repositories.py`). Three cases:
  1. slow query emits a WARNING through the `auto_workflow_database` logger
  2. `statement_timeout` cancels a long query server-side
  3. pool exhaustion fails fast and recovers after release

Env vars are patched per-test via `monkeypatch` so each case builds its own
engine with the targeted configuration.
"""
from __future__ import annotations

import asyncio
import logging
import os

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, TimeoutError as SATimeoutError

from auto_workflow_database.repositories._session import build_engine

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — resilience tests require live Postgres",
)


async def test_slow_query_logs_warning(monkeypatch, caplog):
    # Lower the slow-query threshold so a 1.2s sleep trips it reliably.
    monkeypatch.setenv("DB_SLOW_QUERY_MS", "500")
    # Reload the module-level constant the listener closes over.
    import importlib
    import auto_workflow_database.repositories._session as session_mod
    importlib.reload(session_mod)

    engine = session_mod.build_engine(DATABASE_URL)
    try:
        with caplog.at_level(logging.WARNING, logger="auto_workflow_database"):
            async with engine.begin() as conn:
                await conn.execute(text("SELECT pg_sleep(1.2)"))
        assert any(
            "slow query" in rec.message and rec.levelname == "WARNING"
            for rec in caplog.records
        ), f"expected slow query warning, got: {[r.message for r in caplog.records]}"
    finally:
        await engine.dispose()


async def test_statement_timeout_aborts_long_query(monkeypatch, caplog):
    # 500 ms server-side cutoff — pg_sleep(3) must be canceled.
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "500")
    import importlib
    import auto_workflow_database.repositories._session as session_mod
    importlib.reload(session_mod)

    engine = session_mod.build_engine(DATABASE_URL)
    try:
        with caplog.at_level(logging.ERROR, logger="auto_workflow_database"):
            with pytest.raises(DBAPIError) as exc_info:
                async with engine.begin() as conn:
                    await conn.execute(text("SELECT pg_sleep(3)"))
        # asyncpg surfaces this as QueryCanceledError inside DBAPIError.
        assert "canceling statement" in str(exc_info.value).lower() or \
            "QueryCanceledError" in type(exc_info.value.orig).__name__
        assert any(
            rec.levelname == "ERROR" and "db error" in rec.message
            for rec in caplog.records
        ), "handle_error listener should have logged the cancellation"
    finally:
        await engine.dispose()


async def test_pool_timeout_fast_fail_and_recovery(monkeypatch):
    # Tiny pool so we can force exhaustion with two connections held open.
    monkeypatch.setenv("DB_POOL_SIZE", "2")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DB_POOL_TIMEOUT_S", "1")
    import importlib
    import auto_workflow_database.repositories._session as session_mod
    importlib.reload(session_mod)

    engine = session_mod.build_engine(DATABASE_URL)
    try:
        c1 = await engine.connect()
        c2 = await engine.connect()
        try:
            with pytest.raises((SATimeoutError, asyncio.TimeoutError)):
                # Third checkout must time out in ~1 second, not hang.
                await asyncio.wait_for(engine.connect(), timeout=5)
        finally:
            await c1.close()
            await c2.close()

        # Pool should now accept a new checkout immediately.
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()
