"""PLAN_03 — execution trigger + history E2E tests."""
from __future__ import annotations

import os

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — requires live Postgres",
)

SAMPLE_GRAPH = {
    "nodes": [{"id": "a", "type": "http", "config": {}}],
    "edges": [],
}


async def _create_workflow(client):
    r = await client.post(
        "/api/v1/workflows",
        json={"name": "exec-test-wf", "settings": {}, "graph": SAMPLE_GRAPH},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def test_execute_workflow_creates_queued_execution(authed_client):
    wf_id = await _create_workflow(authed_client)
    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["workflow_id"] == wf_id
    assert body["execution_mode"] == "serverless"
    assert body["id"] is not None


async def test_execute_workflow_not_owned_returns_404(authed_client):
    from uuid import uuid4
    r = await authed_client.post(f"/api/v1/workflows/{uuid4()}/execute")
    assert r.status_code == 404


async def test_execute_inactive_workflow_returns_409(authed_client):
    wf_id = await _create_workflow(authed_client)
    d = await authed_client.delete(f"/api/v1/workflows/{wf_id}")
    assert d.status_code == 204
    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    assert r.status_code == 409


async def test_get_execution_happy(authed_client):
    wf_id = await _create_workflow(authed_client)
    create_r = await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    ex_id = create_r.json()["id"]
    r = await authed_client.get(f"/api/v1/executions/{ex_id}")
    assert r.status_code == 200
    assert r.json()["id"] == ex_id
    assert r.json()["status"] == "queued"


async def test_get_execution_not_owned_returns_404(authed_client):
    from uuid import uuid4
    r = await authed_client.get(f"/api/v1/executions/{uuid4()}")
    assert r.status_code == 404


async def test_list_executions_returns_keyset_response(authed_client):
    wf_id = await _create_workflow(authed_client)
    await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    r = await authed_client.get(f"/api/v1/executions/by-workflow/{wf_id}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert "next_cursor" in body
    assert "has_more" in body


async def test_list_executions_cursor_pagination(authed_client):
    wf_id = await _create_workflow(authed_client)
    for _ in range(5):
        await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    r1 = await authed_client.get(
        f"/api/v1/executions/by-workflow/{wf_id}", params={"limit": 3}
    )
    body1 = r1.json()
    assert len(body1["items"]) == 3
    assert body1["has_more"] is True
    assert body1["next_cursor"] is not None

    r2 = await authed_client.get(
        f"/api/v1/executions/by-workflow/{wf_id}",
        params={"limit": 3, "cursor": body1["next_cursor"]},
    )
    body2 = r2.json()
    assert len(body2["items"]) == 2
    assert body2["has_more"] is False

    all_ids = [e["id"] for e in body1["items"]] + [e["id"] for e in body2["items"]]
    assert len(set(all_ids)) == 5


async def test_list_executions_empty(authed_client):
    wf_id = await _create_workflow(authed_client)
    r = await authed_client.get(f"/api/v1/executions/by-workflow/{wf_id}")
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["has_more"] is False
