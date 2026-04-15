"""ADR-007 approval state-machine coverage — PLAN_01 §6.

Paths covered:
    queued → running → paused → resumed → success
    running → failed (with error)
    paused → rejected
    list_pending_approvals filters by owner
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from auto_workflow_database.repositories.base import Execution, Workflow
from Database.tests.fakes import (
    InMemoryExecutionRepository,
    InMemoryWorkflowRepository,
)


def _make_execution(workflow_id) -> Execution:
    return Execution(
        id=uuid4(),
        workflow_id=workflow_id,
        status="queued",
        execution_mode="serverless",
    )


def _make_workflow(owner_id) -> Workflow:
    return Workflow(
        id=uuid4(),
        owner_id=owner_id,
        name="wf",
        settings={"execution_mode": "serverless"},
        graph={"nodes": [], "edges": []},
    )


@pytest.mark.asyncio
async def test_happy_path_paused_resumed_success():
    wfrepo = InMemoryWorkflowRepository()
    exrepo = InMemoryExecutionRepository(wfrepo)
    wf = _make_workflow(uuid4())
    await wfrepo.save(wf)
    ex = _make_execution(wf.id)
    await exrepo.create(ex)

    await exrepo.update_status(ex.id, "running")
    await exrepo.update_status(ex.id, "paused", paused_at_node="approval_1")
    paused = await exrepo.get(ex.id)
    assert paused.status == "paused"
    assert paused.paused_at_node == "approval_1"

    await exrepo.update_status(ex.id, "resumed")
    resumed = await exrepo.get(ex.id)
    assert resumed.paused_at_node is None

    await exrepo.update_status(ex.id, "running")
    await exrepo.append_node_result(
        ex.id,
        "llm_1",
        {"ok": True},
        token_usage={"prompt": 10, "completion": 5},
        cost_usd=0.0,
    )
    await exrepo.update_status(ex.id, "success")
    await exrepo.finalize(ex.id, duration_ms=1234)

    final = await exrepo.get(ex.id)
    assert final.status == "success"
    assert final.token_usage == {"prompt": 10, "completion": 5}
    assert final.duration_ms == 1234
    assert final.node_results["llm_1"] == {"ok": True}


@pytest.mark.asyncio
async def test_running_to_failed_carries_error():
    wfrepo = InMemoryWorkflowRepository()
    exrepo = InMemoryExecutionRepository(wfrepo)
    wf = _make_workflow(uuid4())
    await wfrepo.save(wf)
    ex = _make_execution(wf.id)
    await exrepo.create(ex)

    await exrepo.update_status(ex.id, "running")
    await exrepo.update_status(
        ex.id, "failed", error={"node_id": "http_1", "message": "boom"}
    )

    failed = await exrepo.get(ex.id)
    assert failed.status == "failed"
    assert failed.error == {"node_id": "http_1", "message": "boom"}


@pytest.mark.asyncio
async def test_paused_to_rejected():
    wfrepo = InMemoryWorkflowRepository()
    exrepo = InMemoryExecutionRepository(wfrepo)
    wf = _make_workflow(uuid4())
    await wfrepo.save(wf)
    ex = _make_execution(wf.id)
    await exrepo.create(ex)

    await exrepo.update_status(ex.id, "running")
    await exrepo.update_status(ex.id, "paused", paused_at_node="approval_1")
    await exrepo.update_status(ex.id, "rejected")

    rejected = await exrepo.get(ex.id)
    assert rejected.status == "rejected"


@pytest.mark.asyncio
async def test_list_pending_approvals_scoped_to_owner():
    wfrepo = InMemoryWorkflowRepository()
    exrepo = InMemoryExecutionRepository(wfrepo)
    alice, bob = uuid4(), uuid4()
    alice_wf = _make_workflow(alice)
    bob_wf = _make_workflow(bob)
    await wfrepo.save(alice_wf)
    await wfrepo.save(bob_wf)

    alice_ex = _make_execution(alice_wf.id)
    bob_ex = _make_execution(bob_wf.id)
    await exrepo.create(alice_ex)
    await exrepo.create(bob_ex)
    await exrepo.update_status(alice_ex.id, "paused", paused_at_node="n")
    await exrepo.update_status(bob_ex.id, "paused", paused_at_node="n")

    alice_pending = await exrepo.list_pending_approvals(alice)
    assert [e.id for e in alice_pending] == [alice_ex.id]
