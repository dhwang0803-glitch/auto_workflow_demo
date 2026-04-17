"""PLAN_07 — execute_workflow credential_ref validation.

Validates ownership + existence of every credential_ref the graph
references before queueing an execution. Plaintext is NOT injected at
this layer — Worker (Execution_Engine PLAN_08) resolves just-in-time.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — requires live Postgres",
)


def _graph_with_credential_ref(credential_id: str) -> dict:
    return {
        "nodes": [
            {
                "id": "n1",
                "type": "email_send",
                "config": {
                    "smtp_host": "smtp.example.com",
                    "smtp_port": 587,
                    "from": "bot@example.com",
                    "to": ["alice@example.com"],
                    "subject": "hi",
                    "body": "...",
                    "credential_ref": {
                        "credential_id": credential_id,
                        "inject": {"user": "smtp_user", "password": "smtp_password"},
                    },
                },
            }
        ],
        "edges": [],
    }


async def _create_workflow(client, graph: dict) -> str:
    r = await client.post(
        "/api/v1/workflows",
        json={"name": "cred-test-wf", "settings": {}, "graph": graph},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def _create_credential(client) -> str:
    r = await client.post(
        "/api/v1/credentials",
        json={
            "name": "smtp-1",
            "type": "smtp",
            "plaintext": {"user": "u", "password": "p"},
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


async def test_execute_with_valid_credential_ref_queued(authed_client):
    cid = await _create_credential(authed_client)
    wf_id = await _create_workflow(
        authed_client, _graph_with_credential_ref(cid)
    )
    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    assert r.status_code == 202
    assert r.json()["status"] == "queued"


async def test_execute_with_nonexistent_credential_ref_404(authed_client):
    wf_id = await _create_workflow(
        authed_client, _graph_with_credential_ref(str(uuid4()))
    )
    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    assert r.status_code == 404


async def test_execute_with_other_users_credential_ref_404(client, email_sender):
    """User A's credential referenced from user B's workflow → 404."""
    from urllib.parse import parse_qs, urlparse

    async def _register_verify_login(email: str, password: str) -> str:
        reg = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": password}
        )
        assert reg.status_code == 201
        link = next(l for (to, l) in email_sender.sent if to == email)
        token = parse_qs(urlparse(link).query)["token"][0]
        v = await client.get("/api/v1/auth/verify", params={"token": token})
        assert v.status_code == 200
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": email, "password": password},
        )
        assert login.status_code == 200
        return login.json()["access_token"]

    access_a = await _register_verify_login("a@example.com", "password-123")
    client.headers["Authorization"] = f"Bearer {access_a}"
    cred_a = await _create_credential(client)

    access_b = await _register_verify_login("b@example.com", "password-123")
    client.headers["Authorization"] = f"Bearer {access_b}"
    wf_id = await _create_workflow(client, _graph_with_credential_ref(cred_a))
    r = await client.post(f"/api/v1/workflows/{wf_id}/execute")
    assert r.status_code == 404


async def test_execute_with_no_credential_refs_still_works(authed_client):
    """Regression: graphs without credential_ref skip validation cleanly."""
    graph = {
        "nodes": [{"id": "n1", "type": "http_request", "config": {"url": "x"}}],
        "edges": [],
    }
    wf_id = await _create_workflow(authed_client, graph)
    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    assert r.status_code == 202
