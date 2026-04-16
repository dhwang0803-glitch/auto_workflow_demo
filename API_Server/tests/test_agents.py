"""PLAN_06 — agent registration + WebSocket E2E tests."""
from __future__ import annotations

import os

import jwt as pyjwt
import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from httpx import ASGITransport, AsyncClient
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — requires live Postgres",
)

RSA_PUB_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0Z3VS5JJcds3xfn/ygWe
FsWlldLxyWbQXlFEGSkn1hI8AVhV2vIcnEB4G8X2ikkRDJW04GomuOQNg0m79Fla
hAgYqHA1NgrFHBOAzRnXMDjLQsBet9mJnOGuLPuMxO3YNyWJCDBy8/RCyBgMb+Gk
HQJOoBfsVDYJFwBjMlYBlSiRkrAAowErgP3CQEv0sDPqRHXNvfGKLMR3ynRajg/I
P2aGkLGxAqp4E14TJFyjFAX+sPl0WBQ+rUilMi6Lg7VpIM3LpdfvWXRlLqNbfwOl
tDiNJkGBk+3CR2W68hXFRKoBP+DK4FjG3hC7kKGyTFxPDEhmKj6aHM35TSQDijFi
pQIDAQAB
-----END PUBLIC KEY-----"""


async def test_register_agent_happy(authed_client):
    r = await authed_client.post(
        "/api/v1/agents/register",
        json={"public_key": RSA_PUB_KEY, "gpu_info": {"gpu": "A100"}},
    )
    assert r.status_code == 201
    body = r.json()
    assert "agent_id" in body
    assert "agent_token" in body
    payload = pyjwt.decode(body["agent_token"], options={"verify_signature": False})
    assert payload["sub"].startswith("agent:")
    assert payload["purpose"] == "agent"


async def test_register_agent_not_authenticated_401(client):
    r = await client.post(
        "/api/v1/agents/register",
        json={"public_key": RSA_PUB_KEY},
    )
    assert r.status_code == 401


async def test_ws_heartbeat(authed_client, app):
    reg = await authed_client.post(
        "/api/v1/agents/register",
        json={"public_key": RSA_PUB_KEY},
    )
    token = reg.json()["agent_token"]
    async with AsyncClient(
        transport=ASGIWebSocketTransport(app=app),
        base_url="http://testserver",
    ) as ws_client:
        async with aconnect_ws(
            f"/api/v1/agents/ws?token={token}", ws_client
        ) as ws:
            await ws.send_json({"type": "heartbeat"})
            resp = await ws.receive_json()
            assert resp["type"] == "heartbeat_ack"


async def test_ws_invalid_token_rejected(app):
    async with AsyncClient(
        transport=ASGIWebSocketTransport(app=app),
        base_url="http://testserver",
    ) as ws_client:
        try:
            async with aconnect_ws(
                "/api/v1/agents/ws?token=bad-token", ws_client
            ) as ws:
                await ws.receive_json()
                pytest.fail("should have been rejected")
        except Exception:
            pass
