"""SkillRepository tests — PLAN_12 W2-7.

Two layers of coverage:
- in-memory fake (`InMemorySkillRepository`) for fast contract tests that
  every implementation must satisfy
- Postgres integration tests (skip without DATABASE_URL) for the real path

The contract tests are run twice — once against the fake, once against
Postgres — using parametrize. New SkillRepository impls (e.g. tenant-
sharded, future MCP-exported) just plug into the same parametrize matrix.
"""
from __future__ import annotations

import os
from typing import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from auto_workflow_database.models.core import User as UserORM
from auto_workflow_database.models.skills import SkillSource as SkillSourceORM
from auto_workflow_database.repositories._session import (
    build_engine,
    build_sessionmaker,
)
from auto_workflow_database.repositories.base import SkillRepository
from auto_workflow_database.repositories.skill_repository import (
    PostgresSkillRepository,
)
from sqlalchemy import select

from tests.fakes import InMemorySkillRepository

DATABASE_URL = os.getenv("DATABASE_URL")


# --- shared fixtures (parametrized over both impls) -----------------------


@pytest_asyncio.fixture
async def pg_sm() -> AsyncIterator:
    if not DATABASE_URL:
        yield None
        return
    engine = build_engine(DATABASE_URL)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_user(pg_sm) -> AsyncIterator:
    if pg_sm is None:
        yield None
        return
    async with pg_sm() as s, s.begin():
        u = UserORM(email=f"{uuid4()}@test.local", plan_tier="light")
        s.add(u)
        await s.flush()
        user_id = u.id
    yield user_id


@pytest_asyncio.fixture
async def repo_factory(request, pg_sm):
    """Return a (repo, owner_user_id) pair per the parametrized impl name."""
    impl = request.param
    if impl == "memory":
        return InMemorySkillRepository(), uuid4()
    if impl == "postgres":
        if pg_sm is None:
            pytest.skip("DATABASE_URL not set")
        # Each Postgres test gets its own user_id so cleanup happens via
        # users.id ON DELETE CASCADE without coordination between tests.
        async with pg_sm() as s, s.begin():
            u = UserORM(email=f"{uuid4()}@test.local", plan_tier="light")
            s.add(u)
            await s.flush()
            owner = u.id
        return PostgresSkillRepository(pg_sm), owner
    raise ValueError(f"unknown repo impl {impl!r}")


PARAMS = ["memory", "postgres"]


# --- contract tests -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_create_returns_dto_with_defaults(repo_factory) -> None:
    repo, owner = repo_factory
    skill = await repo.create(
        owner_user_id=owner,
        name="Refund threshold",
        condition={"text": "Customer asks for refund > $500"},
        action={"text": "Forward to manager"},
    )
    assert skill.owner_user_id == owner
    assert skill.status == "pending_review"
    assert skill.scope == "workspace"
    assert skill.created_at is not None
    assert skill.updated_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_create_with_source_persists_audit_row(repo_factory) -> None:
    repo, owner = repo_factory
    skill = await repo.create(
        owner_user_id=owner,
        name="X",
        condition={"text": "C"},
        action={"text": "A"},
        source_type="conversation",
        source_ref={"session_id": str(uuid4()), "turn_index": 3},
    )
    assert skill.id is not None
    # Implementation-specific source check follows the parametrize fork.


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_create_rejects_partial_source(repo_factory) -> None:
    repo, owner = repo_factory
    with pytest.raises(ValueError, match="source_type and source_ref"):
        await repo.create(
            owner_user_id=owner,
            name="X",
            condition={"text": "C"},
            action={"text": "A"},
            source_type="conversation",
            source_ref=None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_get_owned_returns_none_for_other_owner(repo_factory) -> None:
    repo, owner = repo_factory
    skill = await repo.create(
        owner_user_id=owner,
        name="X",
        condition={"text": "C"},
        action={"text": "A"},
    )
    other_owner = uuid4()
    assert await repo.get_owned(other_owner, skill.id) is None
    assert await repo.get_owned(owner, skill.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_list_owned_filters_by_status(repo_factory) -> None:
    repo, owner = repo_factory
    pending = await repo.create(
        owner_user_id=owner,
        name="A",
        condition={"text": "c"},
        action={"text": "a"},
    )
    active = await repo.create(
        owner_user_id=owner,
        name="B",
        condition={"text": "c"},
        action={"text": "a"},
        status="active",
    )

    all_skills = await repo.list_owned(owner)
    assert {s.id for s in all_skills} == {pending.id, active.id}

    only_pending = await repo.list_owned(owner, status="pending_review")
    assert [s.id for s in only_pending] == [pending.id]

    only_active = await repo.list_owned(owner, status="active")
    assert [s.id for s in only_active] == [active.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_list_owned_isolates_owners(repo_factory) -> None:
    repo, owner = repo_factory
    await repo.create(
        owner_user_id=owner,
        name="mine",
        condition={"text": "c"},
        action={"text": "a"},
    )
    other_owner = uuid4()
    assert await repo.list_owned(other_owner) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_update_status_returns_updated_dto(repo_factory) -> None:
    repo, owner = repo_factory
    skill = await repo.create(
        owner_user_id=owner,
        name="X",
        condition={"text": "C"},
        action={"text": "A"},
    )
    initial_updated_at = skill.updated_at

    updated = await repo.update_status(owner, skill.id, "active")
    assert updated is not None
    assert updated.status == "active"
    assert updated.updated_at is not None
    assert updated.updated_at >= initial_updated_at


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_update_status_returns_none_for_other_owner(repo_factory) -> None:
    repo, owner = repo_factory
    skill = await repo.create(
        owner_user_id=owner,
        name="X",
        condition={"text": "C"},
        action={"text": "A"},
    )
    other = uuid4()
    assert await repo.update_status(other, skill.id, "active") is None
    # Verify the original was not mutated (would-be transition silently
    # leaking would be a security bug).
    fetched = await repo.get_owned(owner, skill.id)
    assert fetched is not None and fetched.status == "pending_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_update_status_returns_none_for_missing(repo_factory) -> None:
    repo, owner = repo_factory
    assert await repo.update_status(owner, uuid4(), "active") is None


# --- Postgres-specific: verify skill_sources row written -----------------

pytestmark_pg = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — Postgres integration tests require live DB",
)


@pytestmark_pg
@pytest.mark.asyncio
async def test_postgres_create_writes_skill_sources_atomically(
    pg_sm, pg_user
) -> None:
    repo = PostgresSkillRepository(pg_sm)
    skill = await repo.create(
        owner_user_id=pg_user,
        name="Audited",
        condition={"text": "C"},
        action={"text": "A"},
        source_type="conversation",
        source_ref={"session_id": str(uuid4()), "turn_index": 1},
    )
    async with pg_sm() as s:
        rows = (
            await s.execute(
                select(SkillSourceORM).where(SkillSourceORM.skill_id == skill.id)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].source_type == "conversation"
    assert "session_id" in rows[0].source_ref
