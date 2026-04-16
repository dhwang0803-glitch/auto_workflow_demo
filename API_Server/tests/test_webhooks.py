"""PLAN_05 — webhook registration + HMAC receive E2E tests."""
from __future__ import annotations

import hashlib
import hmac
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
        json={"name": "wh-test-wf", "settings": {}, "graph": SAMPLE_GRAPH},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def test_register_webhook_happy(authed_client):
    wf_id = await _create_workflow(authed_client)
    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/webhook")
    assert r.status_code == 201
    body = r.json()
    assert body["path"].startswith("/webhooks/")
    assert len(body["secret"]) > 0
    assert body["workflow_id"] == wf_id


async def test_register_webhook_not_owned_404(authed_client):
    from uuid import uuid4
    r = await authed_client.post(f"/api/v1/workflows/{uuid4()}/webhook")
    assert r.status_code == 404


async def test_register_webhook_inactive_409(authed_client):
    wf_id = await _create_workflow(authed_client)
    await authed_client.delete(f"/api/v1/workflows/{wf_id}")
    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/webhook")
    assert r.status_code == 409


async def test_unregister_webhook_happy(authed_client):
    wf_id = await _create_workflow(authed_client)
    await authed_client.post(f"/api/v1/workflows/{wf_id}/webhook")
    r = await authed_client.delete(f"/api/v1/workflows/{wf_id}/webhook")
    assert r.status_code == 204


async def test_unregister_webhook_idempotent(authed_client):
    wf_id = await _create_workflow(authed_client)
    r = await authed_client.delete(f"/api/v1/workflows/{wf_id}/webhook")
    assert r.status_code == 204


async def test_receive_webhook_happy(authed_client, client):
    wf_id = await _create_workflow(authed_client)
    reg = await authed_client.post(f"/api/v1/workflows/{wf_id}/webhook")
    path = reg.json()["path"]
    secret = reg.json()["secret"]
    body = b'{"event": "push"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = await client.post(
        path,
        content=body,
        headers={"X-Webhook-Signature": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 202
    assert "execution_id" in r.json()


async def test_receive_webhook_bad_signature_401(authed_client, client):
    wf_id = await _create_workflow(authed_client)
    reg = await authed_client.post(f"/api/v1/workflows/{wf_id}/webhook")
    path = reg.json()["path"]
    r = await client.post(
        path,
        content=b'{"event": "push"}',
        headers={"X-Webhook-Signature": "bad", "Content-Type": "application/json"},
    )
    assert r.status_code == 401


async def test_receive_webhook_unknown_path_404(client):
    r = await client.post(
        "/webhooks/nonexistent-path",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 404
