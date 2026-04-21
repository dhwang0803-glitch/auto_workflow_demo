"""ADR-021 §5 — execute_workflow(inline) end-to-end.

Stopgap path that runs the DAG in-process via Execution_Engine._execute
when `serverless_execution_mode="inline"`. Verified by POSTing /execute
and observing that the returned execution record already has node_results
populated (no async Celery pickup needed).

Live Postgres required — set DATABASE_URL. PLAN_21 Phase 6 removes the
inline branch; this test file goes with it.
"""
from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841
cryptography = pytest.importorskip("cryptography")

from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.services.email_sender import NoopEmailSender

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — inline execute test requires live Postgres",
)


def _make_inline_settings() -> Settings:
    return Settings(
        database_url=DATABASE_URL or "",
        jwt_secret="test-secret-do-not-use-in-prod",
        jwt_algorithm="HS256",
        jwt_access_ttl_minutes=60,
        jwt_verify_email_ttl_hours=24,
        email_sender="console",
        app_base_url="http://testserver",
        password_min_length=8,
        bcrypt_cost=4,
        workflow_limit_light=3,
        credential_master_key=Fernet.generate_key().decode("utf-8"),
        # ADR-021 §5 — inline mode branch. `celery_broker_url` must be
        # non-empty so the serverless path is entered; the value itself
        # is ignored in inline mode (no broker call is made).
        celery_broker_url="redis://inline-stopgap",
        serverless_execution_mode="inline",
    )


@pytest_asyncio.fixture
async def inline_app():
    settings = _make_inline_settings()
    app = create_app(settings, email_sender=NoopEmailSender())
    yield app


@pytest_asyncio.fixture
async def inline_client(inline_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=inline_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        async with inline_app.router.lifespan_context(inline_app):
            sm = inline_app.state.sessionmaker
            async with sm() as s, s.begin():
                await s.execute(text("TRUNCATE users CASCADE"))
            yield c
            async with sm() as s, s.begin():
                await s.execute(text("TRUNCATE users CASCADE"))


@pytest_asyncio.fixture
async def authed_inline(inline_client, inline_app):
    from urllib.parse import parse_qs, urlparse
    email = f"inline-{uuid4().hex[:8]}@example.com"
    password = "correct-horse-8"
    r = await inline_client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert r.status_code == 201

    sender = inline_app.state.email_sender
    link = next(l for (to, l) in sender.sent if to == email)
    token = parse_qs(urlparse(link).query)["token"][0]
    v = await inline_client.get("/api/v1/auth/verify", params={"token": token})
    assert v.status_code == 200

    login = await inline_client.post(
        "/api/v1/auth/login", data={"username": email, "password": password}
    )
    assert login.status_code == 200
    inline_client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    return inline_client


# ConditionNode routes input to either true_branch or false_branch. The
# inline path uses Execution_Engine's registry, so any non-Google node
# works — pick one that doesn't hit the network.
TWO_NODE_GRAPH = {
    "nodes": [
        {"id": "cond", "type": "condition", "config": {
            "field": "value", "operator": "gt", "value": 0,
        }},
        {"id": "tail", "type": "merge", "config": {}},
    ],
    "edges": [{"source": "cond", "target": "tail"}],
}


async def test_inline_execute_completes_synchronously(authed_inline):
    r = await authed_inline.post(
        "/api/v1/workflows",
        json={"name": "inline-wf", "settings": {}, "graph": TWO_NODE_GRAPH},
    )
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]

    exec_resp = await authed_inline.post(f"/api/v1/workflows/{wf_id}/execute")
    assert exec_resp.status_code == 202, exec_resp.text
    exec_id = exec_resp.json()["id"]

    # Inline path updates status before returning. The /execute endpoint
    # still returns the pre-inline snapshot ("queued"), so we poll /get
    # for the post-run state instead of sleeping.
    got = await authed_inline.get(f"/api/v1/executions/{exec_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "success", body
    assert "cond" in body.get("node_results", {})


async def test_inline_execute_records_failure_status(authed_inline):
    bad_graph = {
        "nodes": [{"id": "x", "type": "nonexistent-type", "config": {}}],
        "edges": [],
    }
    # DAG validator allows unknown node types (registry check happens at
    # execute time), so the POST succeeds but inline execution flips the
    # status to "failed".
    r = await authed_inline.post(
        "/api/v1/workflows",
        json={"name": "bad-wf", "settings": {}, "graph": bad_graph},
    )
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]

    exec_resp = await authed_inline.post(f"/api/v1/workflows/{wf_id}/execute")
    exec_id = exec_resp.json()["id"]

    got = await authed_inline.get(f"/api/v1/executions/{exec_id}")
    assert got.status_code == 200
    assert got.json()["status"] == "failed"
