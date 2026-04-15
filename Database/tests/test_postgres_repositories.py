"""Postgres Repository integration tests — PLAN_02 §6.

Requires a live DB reachable via `DATABASE_URL` (async DSN). Each test uses
a fresh user/workflow pair so ordering doesn't matter. Cleanup is best-effort
via fixtures — Postgres `ON DELETE CASCADE` on `users.id` handles the rest.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from sqlalchemy import text

from Database.src.models.core import User as UserORM
from Database.src.repositories._session import build_engine, build_sessionmaker
from Database.src.repositories.base import (
    Execution,
    NodeDefinition,
    Workflow,
)
from Database.src.repositories.execution_repository import (
    PostgresExecutionRepository,
)
from Database.src.repositories.node_catalog import PostgresNodeCatalog
from Database.src.repositories.webhook_registry import PostgresWebhookRegistry
from Database.src.repositories.workflow_repository import (
    PostgresWorkflowRepository,
)

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — Postgres integration tests require live DB",
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
        u = UserORM(
            email=f"{uuid4()}@test.local",
            plan_tier="light",
        )
        s.add(u)
        await s.flush()
        return u


async def test_workflow_repo_crud(sm):
    user = await _seed_user(sm)
    repo = PostgresWorkflowRepository(sm)

    wf = Workflow(
        id=uuid4(),
        owner_id=user.id,
        name="wf1",
        settings={"execution_mode": "serverless"},
        graph={"nodes": [], "edges": []},
    )
    await repo.save(wf)
    fetched = await repo.get(wf.id)
    assert fetched is not None and fetched.name == "wf1"

    wf.name = "wf1-renamed"
    await repo.save(wf)
    fetched = await repo.get(wf.id)
    assert fetched.name == "wf1-renamed"

    listed = await repo.list_by_owner(user.id)
    assert [w.id for w in listed] == [wf.id]

    await repo.delete(wf.id)
    assert await repo.get(wf.id) is None


async def test_execution_repo_state_machine(sm):
    user = await _seed_user(sm)
    wf_repo = PostgresWorkflowRepository(sm)
    ex_repo = PostgresExecutionRepository(sm)

    wf = Workflow(
        id=uuid4(),
        owner_id=user.id,
        name="wf",
        settings={},
        graph={"nodes": [], "edges": []},
    )
    await wf_repo.save(wf)

    ex = Execution(
        id=uuid4(),
        workflow_id=wf.id,
        status="queued",
        execution_mode="serverless",
    )
    await ex_repo.create(ex)
    await ex_repo.update_status(ex.id, "running")
    await ex_repo.update_status(ex.id, "paused", paused_at_node="approval_1")

    paused = await ex_repo.get(ex.id)
    assert paused.status == "paused" and paused.paused_at_node == "approval_1"

    pending = await ex_repo.list_pending_approvals(user.id)
    assert [e.id for e in pending] == [ex.id]

    await ex_repo.update_status(ex.id, "resumed")
    resumed = await ex_repo.get(ex.id)
    assert resumed.paused_at_node is None

    await ex_repo.append_node_result(
        ex.id,
        "llm_1",
        {"ok": True},
        token_usage={"prompt": 10, "completion": 5},
        cost_usd=0.01,
    )
    await ex_repo.update_status(ex.id, "success")
    await ex_repo.finalize(ex.id, duration_ms=1234)

    final = await ex_repo.get(ex.id)
    assert final.status == "success"
    assert final.node_results["llm_1"] == {"ok": True}
    assert final.token_usage == {"prompt": 10, "completion": 5}
    assert final.duration_ms == 1234
    assert final.cost_usd == pytest.approx(0.01)


async def test_webhook_registry_roundtrip(sm):
    user = await _seed_user(sm)
    wf_repo = PostgresWorkflowRepository(sm)
    wh_repo = PostgresWebhookRegistry(sm)

    wf = Workflow(
        id=uuid4(),
        owner_id=user.id,
        name="wf",
        settings={},
        graph={"nodes": [], "edges": []},
    )
    await wf_repo.save(wf)

    binding = await wh_repo.register(wf.id, secret="hmac-secret")
    assert binding.path.startswith("/webhooks/")

    resolved = await wh_repo.resolve(binding.path)
    assert resolved is not None and resolved.workflow_id == wf.id
    assert resolved.secret == "hmac-secret"

    await wh_repo.unregister(binding.path)
    assert await wh_repo.resolve(binding.path) is None


async def test_node_catalog_upsert_idempotent(sm):
    repo = PostgresNodeCatalog(sm)
    v1 = NodeDefinition(type="test.plan02", version="1.0.0", schema={"a": 1})
    await repo.upsert_many([v1])
    await repo.upsert_many([v1])  # idempotent

    v1_updated = NodeDefinition(
        type="test.plan02", version="1.0.0", schema={"a": 2}
    )
    await repo.upsert_many([v1_updated])

    all_nodes = await repo.list_all()
    match = [n for n in all_nodes if n.type == "test.plan02"]
    assert len(match) == 1
    assert match[0].schema == {"a": 2}

    # Cleanup so repeated runs don't accumulate.
    async with sm() as s, s.begin():
        await s.execute(
            text("DELETE FROM nodes WHERE type = 'test.plan02'")
        )
