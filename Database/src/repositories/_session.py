"""Async engine + sessionmaker factory.

`API_Server` builds one of these at startup and injects the sessionmaker
into every Postgres repository. Kept tiny on purpose — Repository classes
are not responsible for engine lifecycle.
"""
from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)


def build_engine(dsn: str | None = None) -> AsyncEngine:
    dsn = dsn or os.environ["DATABASE_URL"]
    return create_async_engine(dsn, future=True, pool_pre_ping=True)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
