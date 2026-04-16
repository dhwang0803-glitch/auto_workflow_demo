"""PLAN_08 — AgentRepository integration tests."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from auto_workflow_database.models.core import User as UserORM
from auto_workflow_database.repositories._session import build_engine, build_sessionmaker
from auto_workflow_database.repositories.base import Agent
from auto_workflow_database.repositories.agent_repository import PostgresAgentRepository

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
        u = UserORM(email=f"{uuid4()}@test.local", plan_tier="heavy")
        s.add(u)
        await s.flush()
        return u


async def test_register_and_get(sm):
    user = await _seed_user(sm)
    repo = PostgresAgentRepository(sm)
    agent = Agent(id=uuid4(), owner_id=user.id, public_key="ssh-rsa AAAA...")
    await repo.register(agent)
    got = await repo.get(agent.id)
    assert got is not None
    assert got.owner_id == user.id
    assert got.public_key == "ssh-rsa AAAA..."
    assert got.registered_at is not None


async def test_get_nonexistent_returns_none(sm):
    repo = PostgresAgentRepository(sm)
    assert await repo.get(uuid4()) is None


async def test_update_heartbeat(sm):
    user = await _seed_user(sm)
    repo = PostgresAgentRepository(sm)
    agent = Agent(id=uuid4(), owner_id=user.id, public_key="key")
    await repo.register(agent)
    assert (await repo.get(agent.id)).last_heartbeat is None
    await repo.update_heartbeat(agent.id)
    assert (await repo.get(agent.id)).last_heartbeat is not None


async def test_list_by_owner(sm):
    user = await _seed_user(sm)
    repo = PostgresAgentRepository(sm)
    for _ in range(3):
        await repo.register(Agent(id=uuid4(), owner_id=user.id, public_key="k"))
    agents = await repo.list_by_owner(user.id)
    assert len(agents) == 3
    assert all(a.owner_id == user.id for a in agents)
