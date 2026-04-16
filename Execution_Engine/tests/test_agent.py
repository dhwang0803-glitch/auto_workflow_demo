"""PLAN_04 — Agent daemon tests using asyncio.Queue as fake WebSocket."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from auto_workflow_database.repositories.base import Execution

from src.agent.command_handler import handle_execute
from src.agent.ws_repo import WebSocketExecutionRepository
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
        raise RuntimeError("agent boom")


class FakeWebSocket:
    """asyncio.Queue-backed fake that records sent messages."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


@pytest.fixture
def reg():
    r = NodeRegistry()
    r.register(AddNode)
    r.register(FailNode)
    return r


@pytest.fixture
def fake_ws():
    return FakeWebSocket()


def _make_execution() -> Execution:
    return Execution(
        id=uuid4(),
        workflow_id=uuid4(),
        status="queued",
        execution_mode="agent",
    )


async def test_ws_repo_sends_status_update(fake_ws):
    ex = _make_execution()
    repo = WebSocketExecutionRepository(fake_ws, ex)
    await repo.update_status(ex.id, "running")

    assert len(fake_ws.sent) == 1
    msg = fake_ws.sent[0]
    assert msg["type"] == "status_update"
    assert msg["status"] == "running"
    assert msg["execution_id"] == str(ex.id)


async def test_ws_repo_sends_node_result(fake_ws):
    ex = _make_execution()
    repo = WebSocketExecutionRepository(fake_ws, ex)
    await repo.append_node_result(ex.id, "node_a", {"value": 42})

    assert len(fake_ws.sent) == 1
    msg = fake_ws.sent[0]
    assert msg["type"] == "node_result"
    assert msg["node_id"] == "node_a"
    assert msg["result"]["value"] == 42


async def test_ws_repo_sends_execution_result(fake_ws):
    ex = _make_execution()
    repo = WebSocketExecutionRepository(fake_ws, ex)
    ex.node_results["a"] = {"value": 10}
    await repo.finalize(ex.id, duration_ms=150)

    assert len(fake_ws.sent) == 1
    msg = fake_ws.sent[0]
    assert msg["type"] == "execution_result"
    assert msg["duration_ms"] == 150
    assert msg["node_results"]["a"]["value"] == 10


async def test_execute_command_runs_workflow(fake_ws, reg):
    eid = str(uuid4())
    wid = str(uuid4())
    msg = {
        "type": "execute",
        "execution_id": eid,
        "workflow_id": wid,
        "graph": {
            "nodes": [{"id": "a", "type": "add", "config": {"amount": 7}}],
            "edges": [],
        },
    }
    await handle_execute(fake_ws, msg, reg)

    types = [m["type"] for m in fake_ws.sent]
    assert "status_update" in types
    assert "execution_result" in types
    success_msgs = [m for m in fake_ws.sent if m.get("status") == "success"]
    assert len(success_msgs) == 1


async def test_execute_failure_reports_error(fake_ws, reg):
    eid = str(uuid4())
    msg = {
        "type": "execute",
        "execution_id": eid,
        "workflow_id": str(uuid4()),
        "graph": {
            "nodes": [{"id": "a", "type": "fail", "config": {}}],
            "edges": [],
        },
    }
    await handle_execute(fake_ws, msg, reg)

    failed_msgs = [m for m in fake_ws.sent if m.get("status") == "failed"]
    assert len(failed_msgs) == 1
    assert "agent boom" in failed_msgs[0]["error"]["message"]
