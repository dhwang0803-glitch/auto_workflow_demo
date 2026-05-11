"""PostgresWorkflowRevisionRepository tests — PLAN_14 PR-Ba.

Schema invariants already covered in `test_personalization_schema.py`
(UNIQUE on (workflow_id, revision_no), CHECK on source). These tests
focus on what the repository itself adds on top of that:

- `record` assigns monotonic revision_no per workflow (caller doesn't pass one)
- `record` populates server-default fields (id, created_at) on the DTO
- `get` round-trips by id
- `list_by_workflow` orders newest first and caps `limit` at 200
- `parent_revision_id` is preserved through record/get/list

Skipped without DATABASE_URL — same Postgres fixture as the rest of the
integration suite.
"""
from __future__ import annotations

import os
from typing import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from auto_workflow_database.models.core import (
    User as UserORM,
    Workflow as WorkflowORM,
)
from auto_workflow_database.repositories._session import (
    build_engine,
    build_sessionmaker,
)
from auto_workflow_database.repositories.workflow_revision_repository import (
    PostgresWorkflowRevisionRepository,
)


DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — workflow_revision repo tests need Postgres",
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
            name=f"wf-{uuid4()}",
            settings={},
            graph={"nodes": [], "connections": []},
        )
        s.add(wf)
        await s.flush()
        wf_id = wf.id
    yield wf_id


@pytest.mark.asyncio
async def test_record_assigns_monotonic_revision_no(pg_sm, pg_user, pg_workflow):
    repo = PostgresWorkflowRevisionRepository(pg_sm)

    first = await repo.record(
        workflow_id=pg_workflow,
        source="ai_draft",
        payload={"nodes": []},
        created_by=pg_user,
    )
    second = await repo.record(
        workflow_id=pg_workflow,
        source="user_edit",
        payload={"nodes": [{"id": "n1"}]},
        parent_revision_id=first.id,
        created_by=pg_user,
    )
    third = await repo.record(
        workflow_id=pg_workflow,
        source="user_edit",
        payload={"nodes": [{"id": "n1"}, {"id": "n2"}]},
        parent_revision_id=second.id,
        created_by=pg_user,
    )

    assert first.revision_no == 1
    assert second.revision_no == 2
    assert third.revision_no == 3
    # DTOs surface server defaults so callers don't need to re-fetch.
    assert first.id is not None
    assert first.created_at is not None
    assert second.parent_revision_id == first.id
    assert third.parent_revision_id == second.id


@pytest.mark.asyncio
async def test_record_revision_no_is_per_workflow(pg_sm, pg_user):
    """Two workflows count revisions independently — no cross-workflow leak."""
    repo = PostgresWorkflowRevisionRepository(pg_sm)

    async with pg_sm() as s, s.begin():
        wf_a = WorkflowORM(
            owner_id=pg_user, name=f"wf-a-{uuid4()}", settings={}, graph={}
        )
        wf_b = WorkflowORM(
            owner_id=pg_user, name=f"wf-b-{uuid4()}", settings={}, graph={}
        )
        s.add_all([wf_a, wf_b])
        await s.flush()
        a_id, b_id = wf_a.id, wf_b.id

    a1 = await repo.record(
        workflow_id=a_id, source="ai_draft", payload={}, created_by=pg_user
    )
    b1 = await repo.record(
        workflow_id=b_id, source="ai_draft", payload={}, created_by=pg_user
    )
    a2 = await repo.record(
        workflow_id=a_id,
        source="user_edit",
        payload={"x": 1},
        parent_revision_id=a1.id,
        created_by=pg_user,
    )

    # Workflow B's first revision is still 1 even after A has two.
    assert a1.revision_no == 1
    assert b1.revision_no == 1
    assert a2.revision_no == 2


@pytest.mark.asyncio
async def test_get_round_trip(pg_sm, pg_user, pg_workflow):
    repo = PostgresWorkflowRevisionRepository(pg_sm)
    written = await repo.record(
        workflow_id=pg_workflow,
        source="ai_draft",
        payload={"nodes": [{"id": "n1", "type": "http"}]},
        created_by=pg_user,
    )

    fetched = await repo.get(written.id)
    assert fetched is not None
    assert fetched.id == written.id
    assert fetched.workflow_id == pg_workflow
    assert fetched.revision_no == 1
    assert fetched.source == "ai_draft"
    assert fetched.payload == {"nodes": [{"id": "n1", "type": "http"}]}
    assert fetched.created_by == pg_user


@pytest.mark.asyncio
async def test_get_missing_returns_none(pg_sm):
    repo = PostgresWorkflowRevisionRepository(pg_sm)
    assert await repo.get(uuid4()) is None


@pytest.mark.asyncio
async def test_list_by_workflow_newest_first(pg_sm, pg_user, pg_workflow):
    repo = PostgresWorkflowRevisionRepository(pg_sm)
    for i in range(4):
        await repo.record(
            workflow_id=pg_workflow,
            source="ai_draft" if i == 0 else "user_edit",
            payload={"i": i},
            created_by=pg_user,
        )

    rows = await repo.list_by_workflow(pg_workflow)
    assert [r.revision_no for r in rows] == [4, 3, 2, 1]
    assert rows[0].payload == {"i": 3}
    assert rows[-1].payload == {"i": 0}


@pytest.mark.asyncio
async def test_list_by_workflow_limit_cap(pg_sm, pg_user, pg_workflow):
    """limit > 200 is capped; limit < 1 floors to 1."""
    repo = PostgresWorkflowRevisionRepository(pg_sm)
    # Seed 3 — enough to exercise both the cap and the floor.
    for i in range(3):
        await repo.record(
            workflow_id=pg_workflow,
            source="user_edit",
            payload={"i": i},
            created_by=pg_user,
        )

    # limit=1000 → effective 200; we only have 3 rows so all return.
    capped = await repo.list_by_workflow(pg_workflow, limit=1000)
    assert len(capped) == 3

    # limit=0 → floored to 1.
    floored = await repo.list_by_workflow(pg_workflow, limit=0)
    assert len(floored) == 1
    assert floored[0].revision_no == 3


@pytest.mark.asyncio
async def test_list_by_workflow_offset(pg_sm, pg_user, pg_workflow):
    repo = PostgresWorkflowRevisionRepository(pg_sm)
    for i in range(5):
        await repo.record(
            workflow_id=pg_workflow,
            source="user_edit",
            payload={"i": i},
            created_by=pg_user,
        )

    page2 = await repo.list_by_workflow(pg_workflow, limit=2, offset=2)
    assert [r.revision_no for r in page2] == [3, 2]


@pytest.mark.asyncio
async def test_list_by_workflow_isolates_workflows(pg_sm, pg_user):
    """list_by_workflow never returns rows from a different workflow_id."""
    repo = PostgresWorkflowRevisionRepository(pg_sm)
    async with pg_sm() as s, s.begin():
        wf_a = WorkflowORM(
            owner_id=pg_user, name=f"wf-a-{uuid4()}", settings={}, graph={}
        )
        wf_b = WorkflowORM(
            owner_id=pg_user, name=f"wf-b-{uuid4()}", settings={}, graph={}
        )
        s.add_all([wf_a, wf_b])
        await s.flush()
        a_id, b_id = wf_a.id, wf_b.id

    await repo.record(workflow_id=a_id, source="ai_draft", payload={"a": 1})
    await repo.record(workflow_id=b_id, source="ai_draft", payload={"b": 1})
    await repo.record(workflow_id=a_id, source="user_edit", payload={"a": 2})

    a_rows = await repo.list_by_workflow(a_id)
    b_rows = await repo.list_by_workflow(b_id)
    assert {r.payload["a"] for r in a_rows} == {1, 2}
    assert all("a" not in r.payload for r in b_rows)
    assert len(b_rows) == 1
