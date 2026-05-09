"""Personalization schema tests — PLAN_15 PR-γ (absorbed PLAN_14 PR-A).

Asserts the column / constraint / table additions in
`schemas/006_personalization.sql` work as the agent retrieval tool and
PLAN_14 reflective pipeline rely on:

- `skills.embedding` accepts vector(1024) and survives a round-trip
- `skills_user_scope_chk` rejects scope='user' with NULL user_id
- `skills_user_scope_chk` rejects scope='workspace' with non-NULL user_id
- `skills_source_chk` rejects unknown source values
- `workflow_revisions` row create + (workflow_id, revision_no) UNIQUE
- `personal_skill_reviews` row create + suggestion_hash dedup query

Skipped without DATABASE_URL — these need real Postgres + pgvector.
"""
from __future__ import annotations

import os
from typing import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from auto_workflow_database.models.core import (
    User as UserORM,
    Workflow as WorkflowORM,
)
from auto_workflow_database.models.skills import (
    PersonalSkillReview,
    Skill as SkillORM,
    WorkflowRevision,
)
from auto_workflow_database.repositories._session import (
    build_engine,
    build_sessionmaker,
)


DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — personalization schema tests need Postgres",
)


@pytest_asyncio.fixture
async def pg_sm() -> AsyncIterator:
    engine = build_engine(DATABASE_URL)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_user(pg_sm) -> AsyncIterator[UUID]:
    async with pg_sm() as s, s.begin():
        u = UserORM(email=f"{uuid4()}@test.local", plan_tier="light")
        s.add(u)
        await s.flush()
        user_id = u.id
    yield user_id


@pytest_asyncio.fixture
async def pg_workflow(pg_sm, pg_user) -> AsyncIterator[UUID]:
    async with pg_sm() as s, s.begin():
        wf = WorkflowORM(
            owner_id=pg_user,
            name="rev-fixture",
            settings={},
            graph={"nodes": [], "connections": []},
        )
        s.add(wf)
        await s.flush()
        wf_id = wf.id
    yield wf_id


@pytest.mark.asyncio
async def test_skill_embedding_round_trip(pg_sm, pg_user) -> None:
    vec = [0.01] * 1024
    async with pg_sm() as s, s.begin():
        s.add(
            SkillORM(
                owner_user_id=pg_user,
                name="vec-skill",
                condition={"type": "always"},
                action={"kind": "noop"},
                embedding=vec,
            )
        )
    async with pg_sm() as s:
        row = (
            await s.execute(
                select(SkillORM).where(SkillORM.owner_user_id == pg_user)
            )
        ).scalar_one()
    assert row.embedding is not None
    assert len(row.embedding) == 1024
    assert pytest.approx(row.embedding[0], abs=1e-6) == 0.01


@pytest.mark.asyncio
async def test_user_scope_requires_user_id(pg_sm, pg_user) -> None:
    """scope='user' without user_id violates skills_user_scope_chk."""
    async with pg_sm() as s, s.begin():
        s.add(
            SkillORM(
                owner_user_id=pg_user,
                name="bad-personal",
                condition={"type": "always"},
                action={"kind": "noop"},
                scope="user",
                # user_id deliberately omitted
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.asyncio
async def test_workspace_scope_rejects_user_id(pg_sm, pg_user) -> None:
    """scope='workspace' with non-NULL user_id violates the chk."""
    async with pg_sm() as s, s.begin():
        s.add(
            SkillORM(
                owner_user_id=pg_user,
                name="bad-workspace",
                condition={"type": "always"},
                action={"kind": "noop"},
                scope="workspace",
                user_id=pg_user,
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.asyncio
async def test_user_scope_with_user_id_accepted(pg_sm, pg_user) -> None:
    async with pg_sm() as s, s.begin():
        s.add(
            SkillORM(
                owner_user_id=pg_user,
                name="good-personal",
                condition={"type": "always"},
                action={"kind": "noop"},
                scope="user",
                user_id=pg_user,
                source="hitl_edit",
                suggestion_hash="deadbeef",
            )
        )
    async with pg_sm() as s:
        row = (
            await s.execute(
                select(SkillORM).where(SkillORM.user_id == pg_user)
            )
        ).scalar_one()
    assert row.scope == "user"
    assert row.user_id == pg_user
    assert row.source == "hitl_edit"
    assert row.suggestion_hash == "deadbeef"


@pytest.mark.asyncio
async def test_skills_source_chk_rejects_unknown(pg_sm, pg_user) -> None:
    async with pg_sm() as s, s.begin():
        s.add(
            SkillORM(
                owner_user_id=pg_user,
                name="bad-source",
                condition={"type": "always"},
                action={"kind": "noop"},
                source="bogus",
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.asyncio
async def test_workflow_revision_unique_per_workflow(
    pg_sm, pg_user, pg_workflow
) -> None:
    async with pg_sm() as s, s.begin():
        s.add_all(
            [
                WorkflowRevision(
                    workflow_id=pg_workflow,
                    revision_no=1,
                    source="ai_draft",
                    payload={"nodes": []},
                    created_by=pg_user,
                ),
                WorkflowRevision(
                    workflow_id=pg_workflow,
                    revision_no=2,
                    source="user_edit",
                    payload={"nodes": [{"id": "n1"}]},
                    created_by=pg_user,
                ),
            ]
        )

    # Re-inserting revision_no=1 for the same workflow violates UNIQUE.
    async with pg_sm() as s, s.begin():
        s.add(
            WorkflowRevision(
                workflow_id=pg_workflow,
                revision_no=1,
                source="ai_draft",
                payload={},
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.asyncio
async def test_workflow_revision_source_chk(pg_sm, pg_workflow) -> None:
    async with pg_sm() as s, s.begin():
        s.add(
            WorkflowRevision(
                workflow_id=pg_workflow,
                revision_no=99,
                source="bogus",
                payload={},
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()


@pytest.mark.asyncio
async def test_personal_skill_review_dedup_query(pg_sm, pg_user) -> None:
    """The (user_id, suggestion_hash) index supports PLAN_14's dedup query."""
    async with pg_sm() as s, s.begin():
        s.add_all(
            [
                PersonalSkillReview(
                    user_id=pg_user,
                    suggestion_hash="hash-a",
                    action="reject",
                    rejection_reason="not relevant",
                ),
                PersonalSkillReview(
                    user_id=pg_user,
                    suggestion_hash="hash-b",
                    action="accept",
                ),
            ]
        )

    async with pg_sm() as s:
        rejected = (
            await s.execute(
                select(PersonalSkillReview).where(
                    PersonalSkillReview.user_id == pg_user,
                    PersonalSkillReview.suggestion_hash == "hash-a",
                    PersonalSkillReview.action == "reject",
                )
            )
        ).scalar_one()
    assert rejected.rejection_reason == "not relevant"


@pytest.mark.asyncio
async def test_personal_skill_review_action_chk(pg_sm, pg_user) -> None:
    async with pg_sm() as s, s.begin():
        s.add(
            PersonalSkillReview(
                user_id=pg_user,
                suggestion_hash="hash-z",
                action="bogus",
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()
