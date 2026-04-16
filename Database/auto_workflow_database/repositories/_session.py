"""Async engine + sessionmaker factory with resilience + query logging.

PLAN_07 — all DB resilience concerns live here. Repository classes are not
responsible for pool config, timeouts, or error logging, so they never need
try/except around queries. Everything defensive is wired at engine creation
time via env-var defaults and three SQLAlchemy event listeners.
"""
from __future__ import annotations

import logging
import os
import time

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger("auto_workflow_database")
SLOW_QUERY_MS = int(os.getenv("DB_SLOW_QUERY_MS", "1000"))


def build_engine(dsn: str | None = None) -> AsyncEngine:
    dsn = dsn or os.environ["DATABASE_URL"]
    engine = create_async_engine(
        dsn,
        future=True,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT_S", "30")),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE_S", "1800")),
        connect_args={
            "server_settings": {
                "statement_timeout": os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000"),
            }
        },
    )

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):
        context._query_start = time.monotonic()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, params, context, executemany):
        elapsed_ms = int((time.monotonic() - context._query_start) * 1000)
        if elapsed_ms >= SLOW_QUERY_MS:
            logger.warning("slow query %dms: %s", elapsed_ms, statement[:200])

    @event.listens_for(engine.sync_engine, "handle_error")
    def _on_error(ctx):
        logger.error(
            "db error %s on: %s",
            type(ctx.original_exception).__name__,
            (ctx.statement or "")[:200],
            exc_info=ctx.original_exception,
        )

    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
