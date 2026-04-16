"""PLAN_02 — DAG executor tests using InMemory fakes."""
from __future__ import annotations

from uuid import uuid4

import pytest

from auto_workflow_database.repositories.base import Execution
from tests.fakes import InMemoryExecutionRepository

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
        raise RuntimeError("intentional failure")


@pytest.fixture
def reg():
    r = NodeRegistry()
    r.register(AddNode)
    r.register(FailNode)
    return r


@pytest.fixture
def repo():
    return InMemoryExecutionRepository()


def _make_execution(wf_id=None):
    return Execution(
        id=uuid4(),
        workflow_id=wf_id or uuid4(),
        status="queued",
        execution_mode="serverless",
    )


async def test_single_node_success(reg, repo):
    from src.runtime.executor import run_workflow
    ex = _make_execution()
    await repo.create(ex)
    graph = {"nodes": [{"id": "a", "type": "add", "config": {"amount": 5}}], "edges": []}
    await run_workflow(graph, ex, repo, reg)
    result = await repo.get(ex.id)
    assert result.status == "success"
    assert result.node_results["a"]["value"] == 5
    assert result.duration_ms is not None


async def test_chain_passes_output_forward(reg, repo):
    from src.runtime.executor import run_workflow
    ex = _make_execution()
    await repo.create(ex)
    graph = {
        "nodes": [
            {"id": "a", "type": "add", "config": {"amount": 10}},
            {"id": "b", "type": "add", "config": {"amount": 3}},
        ],
        "edges": [{"source": "a", "target": "b"}],
    }
    await run_workflow(graph, ex, repo, reg)
    result = await repo.get(ex.id)
    assert result.status == "success"
    assert result.node_results["a"]["value"] == 10
    assert result.node_results["b"]["value"] == 13


async def test_diamond_parallel(reg, repo):
    """a → b, a → c, b → d, c → d. b and c should run in parallel."""
    from src.runtime.executor import run_workflow
    ex = _make_execution()
    await repo.create(ex)
    graph = {
        "nodes": [
            {"id": "a", "type": "add", "config": {"amount": 1}},
            {"id": "b", "type": "add", "config": {"amount": 10}},
            {"id": "c", "type": "add", "config": {"amount": 100}},
            {"id": "d", "type": "add", "config": {"amount": 0}},
        ],
        "edges": [
            {"source": "a", "target": "b"},
            {"source": "a", "target": "c"},
            {"source": "b", "target": "d"},
            {"source": "c", "target": "d"},
        ],
    }
    await run_workflow(graph, ex, repo, reg)
    result = await repo.get(ex.id)
    assert result.status == "success"
    assert result.node_results["b"]["value"] == 11
    assert result.node_results["c"]["value"] == 101


async def test_node_failure_marks_execution_failed(reg, repo):
    from src.runtime.executor import run_workflow
    ex = _make_execution()
    await repo.create(ex)
    graph = {"nodes": [{"id": "a", "type": "fail", "config": {}}], "edges": []}
    await run_workflow(graph, ex, repo, reg)
    result = await repo.get(ex.id)
    assert result.status == "failed"
    assert "intentional failure" in result.error["message"]


async def test_empty_graph_succeeds(reg, repo):
    from src.runtime.executor import run_workflow
    ex = _make_execution()
    await repo.create(ex)
    graph = {"nodes": [], "edges": []}
    await run_workflow(graph, ex, repo, reg)
    result = await repo.get(ex.id)
    assert result.status == "success"
