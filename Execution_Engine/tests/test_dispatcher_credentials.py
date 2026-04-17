"""PLAN_08 — dispatcher E2E credential resolution tests."""
from __future__ import annotations

from uuid import uuid4

import pytest

from auto_workflow_database.repositories.base import Execution, Workflow
from tests.fakes import (
    InMemoryCredentialStore,
    InMemoryExecutionRepository,
    InMemoryWorkflowRepository,
)

from src.nodes.base import BaseNode
from src.nodes.registry import NodeRegistry


class RecordingNode(BaseNode):
    """Captures the config it was called with so tests can assert injection."""

    last_config: dict | None = None

    @property
    def node_type(self) -> str:
        return "recording"

    async def execute(self, input_data: dict, config: dict) -> dict:
        # Copy so later mutations don't affect the captured snapshot.
        RecordingNode.last_config = dict(config)
        return {"ok": True}


@pytest.fixture(autouse=True)
def _reset_recording():
    RecordingNode.last_config = None
    yield


@pytest.fixture
def reg():
    r = NodeRegistry()
    r.register(RecordingNode)
    return r


@pytest.fixture
def exec_repo():
    return InMemoryExecutionRepository()


@pytest.fixture
def wf_repo():
    return InMemoryWorkflowRepository()


@pytest.fixture
def store():
    return InMemoryCredentialStore()


def _make_workflow(owner: UUID, graph: dict) -> Workflow:  # type: ignore[name-defined]
    return Workflow(
        id=uuid4(),
        owner_id=owner,
        name="cred-wf",
        settings={},
        graph=graph,
    )


def _make_execution(workflow_id):
    return Execution(
        id=uuid4(),
        workflow_id=workflow_id,
        status="queued",
        execution_mode="serverless",
    )


async def test_dispatch_resolves_and_runs(reg, exec_repo, wf_repo, store):
    from src.dispatcher.serverless import _execute

    owner = uuid4()
    cid = await store.store(owner, "smtp", {"user": "u", "password": "p"}, credential_type="smtp")
    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "recording",
                "config": {
                    "smtp_host": "smtp.example.com",
                    "credential_ref": {
                        "credential_id": str(cid),
                        "inject": {"user": "smtp_user", "password": "smtp_password"},
                    },
                },
            }
        ],
        "edges": [],
    }
    wf = _make_workflow(owner, graph)
    await wf_repo.save(wf)
    ex = _make_execution(wf.id)
    await exec_repo.create(ex)

    await _execute(
        str(ex.id),
        exec_repo=exec_repo,
        wf_repo=wf_repo,
        node_registry=reg,
        credential_store=store,
    )

    result = await exec_repo.get(ex.id)
    assert result.status == "success"
    assert RecordingNode.last_config["smtp_user"] == "u"
    assert RecordingNode.last_config["smtp_password"] == "p"
    assert "credential_ref" not in RecordingNode.last_config


async def test_dispatch_without_store_fails_when_refs_present(reg, exec_repo, wf_repo):
    from src.dispatcher.serverless import _execute

    owner = uuid4()
    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "recording",
                "config": {
                    "credential_ref": {
                        "credential_id": str(uuid4()),
                        "inject": {"x": "y"},
                    }
                },
            }
        ],
        "edges": [],
    }
    wf = _make_workflow(owner, graph)
    await wf_repo.save(wf)
    ex = _make_execution(wf.id)
    await exec_repo.create(ex)

    await _execute(
        str(ex.id),
        exec_repo=exec_repo,
        wf_repo=wf_repo,
        node_registry=reg,
        credential_store=None,
    )

    result = await exec_repo.get(ex.id)
    assert result.status == "failed"
    assert "credential store not configured" in result.error["message"]


async def test_dispatch_without_store_works_without_refs(reg, exec_repo, wf_repo):
    """Regression: graphs with no credential_refs run fine even without a store."""
    from src.dispatcher.serverless import _execute

    owner = uuid4()
    graph = {"nodes": [{"id": "n1", "type": "recording", "config": {"k": "v"}}], "edges": []}
    wf = _make_workflow(owner, graph)
    await wf_repo.save(wf)
    ex = _make_execution(wf.id)
    await exec_repo.create(ex)

    await _execute(
        str(ex.id),
        exec_repo=exec_repo,
        wf_repo=wf_repo,
        node_registry=reg,
        credential_store=None,
    )

    result = await exec_repo.get(ex.id)
    assert result.status == "success"


async def test_dispatch_resolve_failure_marks_failed(reg, exec_repo, wf_repo, store):
    """Race: credential deleted between API validation and Worker pickup."""
    from src.dispatcher.serverless import _execute

    owner = uuid4()
    # Graph references an id we never stored → bulk_retrieve raises KeyError.
    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "recording",
                "config": {
                    "credential_ref": {
                        "credential_id": str(uuid4()),
                        "inject": {"x": "y"},
                    }
                },
            }
        ],
        "edges": [],
    }
    wf = _make_workflow(owner, graph)
    await wf_repo.save(wf)
    ex = _make_execution(wf.id)
    await exec_repo.create(ex)

    await _execute(
        str(ex.id),
        exec_repo=exec_repo,
        wf_repo=wf_repo,
        node_registry=reg,
        credential_store=store,
    )

    result = await exec_repo.get(ex.id)
    assert result.status == "failed"
    assert "credential resolution failed" in result.error["message"]
    # Generic message — no credential id leaked.
    assert str(graph["nodes"][0]["config"]["credential_ref"]["credential_id"]) not in result.error["message"]
