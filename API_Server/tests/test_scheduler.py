"""PLAN_04 — activate/deactivate E2E tests."""
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
        json={"name": "sched-test-wf", "settings": {}, "graph": SAMPLE_GRAPH},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def test_activate_cron_happy(authed_client):
    wf_id = await _create_workflow(authed_client)
    r = await authed_client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"trigger_type": "cron", "cron": "0 9 * * MON-FRI"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["settings"]["trigger"]["trigger_type"] == "cron"
    assert body["settings"]["trigger"]["cron"] == "0 9 * * MON-FRI"


async def test_activate_interval_happy(authed_client):
    wf_id = await _create_workflow(authed_client)
    r = await authed_client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"trigger_type": "interval", "interval_seconds": 300},
    )
    assert r.status_code == 200
    assert r.json()["settings"]["trigger"]["interval_seconds"] == 300


async def test_activate_not_owned_404(authed_client):
    from uuid import uuid4
    r = await authed_client.post(
        f"/api/v1/workflows/{uuid4()}/activate",
        json={"trigger_type": "interval", "interval_seconds": 60},
    )
    assert r.status_code == 404


async def test_activate_inactive_409(authed_client):
    wf_id = await _create_workflow(authed_client)
    await authed_client.delete(f"/api/v1/workflows/{wf_id}")
    r = await authed_client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"trigger_type": "interval", "interval_seconds": 60},
    )
    assert r.status_code == 409


async def test_activate_invalid_cron_422(authed_client):
    wf_id = await _create_workflow(authed_client)
    r = await authed_client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"trigger_type": "cron", "cron": "not a cron"},
    )
    assert r.status_code == 422


async def test_deactivate_happy(authed_client):
    wf_id = await _create_workflow(authed_client)
    await authed_client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"trigger_type": "interval", "interval_seconds": 300},
    )
    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/deactivate")
    assert r.status_code == 200
    assert "trigger" not in r.json()["settings"]


async def test_deactivate_already_inactive_is_idempotent(authed_client):
    wf_id = await _create_workflow(authed_client)
    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/deactivate")
    assert r.status_code == 200
    assert "trigger" not in r.json()["settings"]


async def test_activate_replaces_existing_trigger(authed_client):
    wf_id = await _create_workflow(authed_client)
    await authed_client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"trigger_type": "interval", "interval_seconds": 300},
    )
    r = await authed_client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"trigger_type": "cron", "cron": "0 12 * * *"},
    )
    assert r.status_code == 200
    assert r.json()["settings"]["trigger"]["trigger_type"] == "cron"
