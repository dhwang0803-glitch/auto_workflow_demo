"""PLAN_03 — Celery dispatcher tests via _execute() with InMemory fakes."""
from __future__ import annotations

from uuid import uuid4

import pytest

from auto_workflow_database.repositories.base import Execution, Workflow
from tests.fakes import InMemoryExecutionRepository, InMemoryWorkflowRepository

from src.nodes.base import BaseNode
from src.nodes.registry import NodeRegistry


class AddNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "add"

    async def execute(self, input_data: dict, config: dict) -> dict:
        return {"value": input_data.get("value", 0) + config.get("amount", 1)}


class FailNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "fail"

    async def execute(self, input_data: dict, config: dict) -> dict:
        raise RuntimeError("boom")


@pytest.fixture
def reg():
    r = NodeRegistry()
    r.register(AddNode)
    r.register(FailNode)
    return r


@pytest.fixture
def exec_repo():
    return InMemoryExecutionRepository()


@pytest.fixture
def wf_repo():
    return InMemoryWorkflowRepository()


def _make_workflow(graph: dict) -> Workflow:
    return Workflow(
        id=uuid4(),
        owner_id=uuid4(),
        name="test-wf",
        settings={},
        graph=graph,
    )


def _make_execution(workflow_id) -> Execution:
    return Execution(
        id=uuid4(),
        workflow_id=workflow_id,
        status="queued",
        execution_mode="serverless",
    )


async def test_dispatch_runs_workflow_to_success(reg, exec_repo, wf_repo):
    from src.dispatcher.serverless import _execute

    graph = {"nodes": [{"id": "a", "type": "add", "config": {"amount": 5}}], "edges": []}
    wf = _make_workflow(graph)
    await wf_repo.save(wf)
    ex = _make_execution(wf.id)
    await exec_repo.create(ex)

    await _execute(
        str(ex.id),
        exec_repo=exec_repo,
        wf_repo=wf_repo,
        node_registry=reg,
    )

    result = await exec_repo.get(ex.id)
    assert result.status == "success"
    assert result.node_results["a"]["value"] == 5


async def test_dispatch_missing_execution_does_not_raise(reg, exec_repo, wf_repo):
    from src.dispatcher.serverless import _execute

    await _execute(
        str(uuid4()),
        exec_repo=exec_repo,
        wf_repo=wf_repo,
        node_registry=reg,
    )


async def test_dispatch_missing_workflow_marks_failed(reg, exec_repo, wf_repo):
    from src.dispatcher.serverless import _execute

    ex = _make_execution(uuid4())
    await exec_repo.create(ex)

    await _execute(
        str(ex.id),
        exec_repo=exec_repo,
        wf_repo=wf_repo,
        node_registry=reg,
    )

    result = await exec_repo.get(ex.id)
    assert result.status == "failed"
    assert "workflow not found" in result.error["message"]


async def test_dispatch_node_failure_marks_failed(reg, exec_repo, wf_repo):
    from src.dispatcher.serverless import _execute

    graph = {"nodes": [{"id": "a", "type": "fail", "config": {}}], "edges": []}
    wf = _make_workflow(graph)
    await wf_repo.save(wf)
    ex = _make_execution(wf.id)
    await exec_repo.create(ex)

    await _execute(
        str(ex.id),
        exec_repo=exec_repo,
        wf_repo=wf_repo,
        node_registry=reg,
    )

    result = await exec_repo.get(ex.id)
    assert result.status == "failed"
    assert "boom" in result.error["message"]
