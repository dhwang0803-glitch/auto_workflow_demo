"""PLAN_06 — keyset pagination for execution list by workflow.

Tests against live Postgres (DATABASE_URL gated). Covers first page, cursor
continuation, empty result, and same-timestamp tiebreaker.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from sqlalchemy import text

from auto_workflow_database.models.core import User as UserORM
from auto_workflow_database.repositories._session import build_engine, build_sessionmaker
from auto_workflow_database.repositories.base import Execution
from auto_workflow_database.repositories.execution_repository import (
    PostgresExecutionRepository,
)

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — requires live Postgres",
)


@pytest.fixture
async def sm():
    engine = build_engine(DATABASE_URL)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _seed_user(sm) -> UserORM:
    async with sm() as s, s.begin():
        u = UserORM(email=f"{uuid4()}@test.local", plan_tier="light")
        s.add(u)
        await s.flush()
        return u


async def _seed_workflow(sm, owner_id):
    async with sm() as s, s.begin():
        wf_id = uuid4()
        await s.execute(text(
            "INSERT INTO workflows (id, owner_id, name, settings, graph) "
            "VALUES (:id, :oid, :name, :settings, :graph)"
        ), {"id": str(wf_id), "oid": str(owner_id), "name": "wf",
            "settings": "{}", "graph": '{"nodes":[],"edges":[]}'})
        return wf_id


async def _seed_executions(repo, workflow_id, count):
    ids = []
    for _ in range(count):
        ex = Execution(
            id=uuid4(),
            workflow_id=workflow_id,
            status="queued",
            execution_mode="serverless",
        )
        await repo.create(ex)
        ids.append(ex.id)
        await asyncio.sleep(0.01)
    return ids


async def test_first_page(sm):
    user = await _seed_user(sm)
    wf_id = await _seed_workflow(sm, user.id)
    repo = PostgresExecutionRepository(sm)
    await _seed_executions(repo, wf_id, 5)

    page = await repo.list_by_workflow(wf_id, limit=3)
    assert len(page) == 3
    assert all(e.workflow_id == wf_id for e in page)
    assert page[0].created_at >= page[1].created_at >= page[2].created_at


async def test_cursor_continuation(sm):
    user = await _seed_user(sm)
    wf_id = await _seed_workflow(sm, user.id)
    repo = PostgresExecutionRepository(sm)
    await _seed_executions(repo, wf_id, 5)

    page1 = await repo.list_by_workflow(wf_id, limit=3)
    last = page1[-1]
    cursor = (last.created_at, last.id)
    page2 = await repo.list_by_workflow(wf_id, limit=3, cursor=cursor)

    assert len(page2) == 2
    all_ids = [e.id for e in page1] + [e.id for e in page2]
    assert len(set(all_ids)) == 5


async def test_empty_result(sm):
    repo = PostgresExecutionRepository(sm)
    page = await repo.list_by_workflow(uuid4(), limit=10)
    assert page == []


async def test_tiebreaker_same_created_at(sm):
    user = await _seed_user(sm)
    wf_id = await _seed_workflow(sm, user.id)
    repo = PostgresExecutionRepository(sm)
    now = datetime.now()
    ids = []
    for _ in range(3):
        ex = Execution(
            id=uuid4(),
            workflow_id=wf_id,
            status="queued",
            execution_mode="serverless",
            created_at=now,
        )
        await repo.create(ex)
        ids.append(ex.id)

    page = await repo.list_by_workflow(wf_id, limit=10)
    assert len(page) == 3
    returned_ids = [e.id for e in page]
    assert returned_ids == sorted(returned_ids, reverse=True)
