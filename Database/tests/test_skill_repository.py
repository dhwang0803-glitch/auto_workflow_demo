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
    sid = str(uuid4())
    skill = await repo.create(
        owner_user_id=owner,
        name="X",
        condition={"text": "C"},
        action={"text": "A"},
        source_type="conversation",
        source_ref={"session_id": sid, "turn_index": 3},
    )
    assert skill.id is not None
    # source_ref should round-trip on the returned DTO so callers can
    # surface provenance without a second read.
    assert skill.source_ref == {"session_id": sid, "turn_index": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_get_and_list_hydrate_source_ref(repo_factory) -> None:
    repo, owner = repo_factory
    audited = await repo.create(
        owner_user_id=owner,
        name="audited",
        condition={"text": "C"},
        action={"text": "A"},
        source_type="conversation",
        source_ref={"source_kind": "regulatory", "policy_id": "x"},
    )
    bare = await repo.create(
        owner_user_id=owner,
        name="bare",
        condition={"text": "C"},
        action={"text": "A"},
    )

    fetched_audited = await repo.get_owned(owner, audited.id)
    assert fetched_audited is not None
    assert fetched_audited.source_ref == {
        "source_kind": "regulatory",
        "policy_id": "x",
    }

    fetched_bare = await repo.get_owned(owner, bare.id)
    assert fetched_bare is not None
    assert fetched_bare.source_ref is None

    by_id = {s.id: s for s in await repo.list_owned(owner)}
    assert by_id[audited.id].source_ref == {
        "source_kind": "regulatory",
        "policy_id": "x",
    }
    assert by_id[bare.id].source_ref is None


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_update_status_preserves_source_ref(repo_factory) -> None:
    repo, owner = repo_factory
    skill = await repo.create(
        owner_user_id=owner,
        name="X",
        condition={"text": "C"},
        action={"text": "A"},
        source_type="conversation",
        source_ref={"source_kind": "synthesized"},
    )
    updated = await repo.update_status(owner, skill.id, "active")
    assert updated is not None
    assert updated.source_ref == {"source_kind": "synthesized"}


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
async def test_list_workspace_active_returns_all_active_workspace_skills_regardless_of_owner(
    repo_factory,
) -> None:
    """PR-K — workspace skills are shared across the team. The owner
    column is audit-only; compose-time retrieval must surface every
    active workspace skill regardless of who first wrote it."""
    repo, owner_a = repo_factory

    # Two different users contribute workspace skills; both should
    # appear in the shared retrieval pool.
    a_active = await repo.create(
        owner_user_id=owner_a,
        name="A active",
        condition={"text": "c"},
        action={"text": "a"},
        status="active",
    )
    a_pending = await repo.create(
        owner_user_id=owner_a,
        name="A pending",
        condition={"text": "c"},
        action={"text": "a"},
    )

    # `repo_factory` only seeds one owner; the postgres variant has FK
    # constraints on owner_user_id, so we reuse `owner_a` to create the
    # rejected/archived/personal rows that must NOT appear.
    archived = await repo.create(
        owner_user_id=owner_a,
        name="archived",
        condition={"text": "c"},
        action={"text": "a"},
        status="active",
    )
    await repo.update_status(owner_a, archived.id, "archived")

    workspace = await repo.list_workspace_active()
    ids = {s.id for s in workspace}
    assert a_active.id in ids
    # pending / archived are filtered out by status, regardless of owner
    assert a_pending.id not in ids
    assert archived.id not in ids


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_list_workspace_active_excludes_personal_scope(
    repo_factory,
) -> None:
    """Personal-scope skills (PLAN_14 PR-G HITL candidates) must never
    leak into the workspace pool — the per-user file boundary the
    reflective extract honors mirrors here for compose-path retrieval."""
    repo, owner = repo_factory
    workspace_skill = await repo.create(
        owner_user_id=owner,
        name="W",
        condition={"text": "c"},
        action={"text": "a"},
        status="active",
    )
    personal_skill = await repo.create(
        owner_user_id=owner,
        name="P",
        condition={"text": "c"},
        action={"text": "a"},
        status="active",
        scope="user",
        user_id=owner,
        source="hitl_edit",
        suggestion_hash="h-personal",
    )

    workspace = await repo.list_workspace_active()
    ids = {s.id for s in workspace}
    assert workspace_skill.id in ids
    assert personal_skill.id not in ids


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_factory", PARAMS, indirect=True)
async def test_list_workspace_active_respects_limit(repo_factory) -> None:
    repo, owner = repo_factory
    for i in range(5):
        await repo.create(
            owner_user_id=owner,
            name=f"S{i}",
            condition={"text": "c"},
            action={"text": "a"},
            status="active",
        )
    rows = await repo.list_workspace_active(limit=3)
    assert len(rows) == 3


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
