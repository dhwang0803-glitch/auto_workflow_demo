"""PLAN_08 — Agent execute WS 메시지의 credential_payloads 서비스 레벨 검증.

E2E WS 테스트는 httpx_ws + authed_client 조합에서 hang 이 발생하므로 서비스
레벨에서 MockWebSocket 을 주입하여 `workflow_service.execute_workflow` 가 실제
어떤 payload 를 `send_json` 으로 밀어넣는지 확인한다. 실 WS 전달 경로는
`test_agents.py::test_ws_heartbeat` 가 별개로 보장.
"""
from __future__ import annotations

import base64
import os
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841
cryptography = pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — requires live Postgres",
)


class _SpyWebSocket:
    """agent_connections 의 WebSocket 자리에 꽂아 send_json 호출을 기록."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def _make_rsa_keypair() -> tuple[bytes, bytes]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


async def _setup(app, authed_client):
    """Register agent + credential, return ids + spy ws attached to agent_connections."""
    _, pub_pem = _make_rsa_keypair()
    reg = await authed_client.post(
        "/api/v1/agents/register",
        json={"public_key": pub_pem.decode(), "gpu_info": {}},
    )
    assert reg.status_code == 201
    agent_id = reg.json()["agent_id"]

    cred = await authed_client.post(
        "/api/v1/credentials",
        json={
            "name": "db-1",
            "type": "postgres_dsn",
            "plaintext": {"dsn": "postgresql://u:p@h:5432/db"},
        },
    )
    assert cred.status_code == 201
    cred_id = cred.json()["id"]

    spy = _SpyWebSocket()
    app.state.agent_connections[uuid4().__class__(agent_id)] = spy
    return cred_id, spy


def _graph_with_ref(credential_id: str, node_id: str = "q1") -> dict:
    return {
        "nodes": [
            {
                "id": node_id,
                "type": "db_query",
                "config": {
                    "query": "SELECT 1",
                    "credential_ref": {
                        "credential_id": credential_id,
                        "inject": {"dsn": "connection_url"},
                    },
                },
            }
        ],
        "edges": [],
    }


async def _create_agent_workflow(authed_client, graph: dict) -> str:
    r = await authed_client.post(
        "/api/v1/workflows",
        json={
            "name": "agent-wf",
            "settings": {"execution_mode": "agent"},
            "graph": graph,
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


async def test_execute_agent_includes_credential_payloads(authed_client, app):
    cred_id, spy = await _setup(app, authed_client)
    wf_id = await _create_agent_workflow(authed_client, _graph_with_ref(cred_id))

    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    assert r.status_code == 202

    # Spy must have received exactly one execute message
    assert len(spy.sent) == 1
    msg = spy.sent[0]
    assert msg["type"] == "execute"
    payloads = msg["credential_payloads"]
    assert len(payloads) == 1
    p = payloads[0]
    assert p["credential_id"] == cred_id
    assert len(base64.b64decode(p["wrapped_key"])) > 0
    assert len(base64.b64decode(p["nonce"])) == 12  # AES-GCM nonce
    assert len(base64.b64decode(p["ciphertext"])) > 0


async def test_execute_agent_no_refs_sends_empty_payloads(authed_client, app):
    _, spy = await _setup(app, authed_client)
    graph = {
        "nodes": [{"id": "n1", "type": "http_request", "config": {"url": "x"}}],
        "edges": [],
    }
    wf_id = await _create_agent_workflow(authed_client, graph)

    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    assert r.status_code == 202
    assert spy.sent[0]["credential_payloads"] == []


async def test_execute_agent_multiple_refs_distinct_payloads(authed_client, app):
    cred1, spy = await _setup(app, authed_client)
    # Second credential for the same user
    r = await authed_client.post(
        "/api/v1/credentials",
        json={"name": "db-2", "type": "postgres_dsn", "plaintext": {"dsn": "postgresql://b"}},
    )
    cred2 = r.json()["id"]

    graph = _graph_with_ref(cred1, node_id="q1")
    graph["nodes"].append(_graph_with_ref(cred2, node_id="q2")["nodes"][0])
    wf_id = await _create_agent_workflow(authed_client, graph)

    r = await authed_client.post(f"/api/v1/workflows/{wf_id}/execute")
    assert r.status_code == 202
    payloads = spy.sent[0]["credential_payloads"]
    assert len(payloads) == 2
    ids = {p["credential_id"] for p in payloads}
    assert ids == {cred1, cred2}
    # Ciphertexts differ (fresh nonces + different plaintexts).
    cts = {p["ciphertext"] for p in payloads}
    assert len(cts) == 2
