"""PLAN_10 — Agent daemon credential 복호화 + 주입 테스트."""
from __future__ import annotations

import base64
import json
from uuid import uuid4

import pytest

from auto_workflow_database.crypto.hybrid import hybrid_encrypt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.agent.command_handler import handle_execute
from src.agent.credential_client import (
    PreDecryptedCredentialStore,
    decrypt_payloads,
)
from src.nodes.base import BaseNode
from src.nodes.registry import NodeRegistry


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


def _payload_for(public_pem: bytes, credential_id, plaintext: dict) -> dict:
    envelope = hybrid_encrypt(json.dumps(plaintext).encode("utf-8"), public_pem)
    return {
        "credential_id": str(credential_id),
        "wrapped_key": base64.b64encode(envelope.wrapped_key).decode(),
        "nonce": base64.b64encode(envelope.nonce).decode(),
        "ciphertext": base64.b64encode(envelope.ciphertext).decode(),
    }


# ------------------------------------------------------------- unit: decrypt


def test_decrypt_payloads_roundtrip():
    priv, pub = _make_rsa_keypair()
    cid = uuid4()
    payload = _payload_for(pub, cid, {"user": "u", "password": "p"})

    out = decrypt_payloads([payload], priv)

    assert out == {cid: {"user": "u", "password": "p"}}


def test_decrypt_payloads_multiple():
    priv, pub = _make_rsa_keypair()
    c1, c2 = uuid4(), uuid4()
    out = decrypt_payloads(
        [_payload_for(pub, c1, {"a": 1}), _payload_for(pub, c2, {"b": 2})],
        priv,
    )
    assert out == {c1: {"a": 1}, c2: {"b": 2}}


# ------------------------------------------------------- unit: PreDecryptedStore


async def test_pre_decrypted_store_bulk_retrieve():
    c1, c2 = uuid4(), uuid4()
    store = PreDecryptedCredentialStore({c1: {"x": 1}, c2: {"y": 2}})
    got = await store.bulk_retrieve([c1, c2], owner_id=uuid4())
    assert got == {c1: {"x": 1}, c2: {"y": 2}}


async def test_pre_decrypted_store_missing_raises():
    store = PreDecryptedCredentialStore({uuid4(): {"a": 1}})
    with pytest.raises(KeyError, match="missing credential"):
        await store.bulk_retrieve([uuid4()], owner_id=uuid4())


async def test_pre_decrypted_store_empty_list():
    store = PreDecryptedCredentialStore({})
    assert await store.bulk_retrieve([], owner_id=uuid4()) == {}


async def test_pre_decrypted_store_ignores_owner_id():
    # server side already filtered — any owner_id passes
    c = uuid4()
    store = PreDecryptedCredentialStore({c: {"v": 1}})
    got = await store.bulk_retrieve([c], owner_id=uuid4())
    assert got == {c: {"v": 1}}


# ------------------------------------------------------- E2E: handle_execute


class RecordingNode(BaseNode):
    last_config: dict | None = None

    @property
    def node_type(self) -> str:
        return "recording"

    async def execute(self, input_data, config):
        RecordingNode.last_config = dict(config)
        return {"ok": True}


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


@pytest.fixture(autouse=True)
def _reset_recording():
    RecordingNode.last_config = None
    yield


@pytest.fixture
def reg():
    r = NodeRegistry()
    r.register(RecordingNode)
    return r


def _graph_with_ref(credential_id) -> dict:
    return {
        "nodes": [
            {
                "id": "n1",
                "type": "recording",
                "config": {
                    "credential_ref": {
                        "credential_id": str(credential_id),
                        "inject": {"user": "smtp_user", "password": "smtp_password"},
                    }
                },
            }
        ],
        "edges": [],
    }


async def test_handle_execute_decrypts_and_runs(reg):
    priv, pub = _make_rsa_keypair()
    cid = uuid4()
    msg = {
        "type": "execute",
        "execution_id": str(uuid4()),
        "workflow_id": str(uuid4()),
        "graph": _graph_with_ref(cid),
        "credential_payloads": [
            _payload_for(pub, cid, {"user": "u@example.com", "password": "secret"}),
        ],
    }
    ws = FakeWebSocket()

    await handle_execute(ws, msg, reg, agent_private_key_pem=priv)

    # Node should have received plaintext in config
    assert RecordingNode.last_config["smtp_user"] == "u@example.com"
    assert RecordingNode.last_config["smtp_password"] == "secret"
    assert "credential_ref" not in RecordingNode.last_config
    # Workflow ran to success
    success = [m for m in ws.sent if m.get("status") == "success"]
    assert len(success) == 1


async def test_handle_execute_no_refs_ignores_payloads(reg):
    """Regression: graphs with no credential_refs run fine even if payloads present."""
    priv, _ = _make_rsa_keypair()
    graph = {
        "nodes": [{"id": "n1", "type": "recording", "config": {"k": "v"}}],
        "edges": [],
    }
    msg = {
        "type": "execute",
        "execution_id": str(uuid4()),
        "workflow_id": str(uuid4()),
        "graph": graph,
        "credential_payloads": [],
    }
    ws = FakeWebSocket()

    await handle_execute(ws, msg, reg, agent_private_key_pem=priv)

    assert RecordingNode.last_config == {"k": "v"}
    success = [m for m in ws.sent if m.get("status") == "success"]
    assert len(success) == 1


async def test_handle_execute_refs_without_private_key_fails(reg):
    cid = uuid4()
    msg = {
        "type": "execute",
        "execution_id": str(uuid4()),
        "workflow_id": str(uuid4()),
        "graph": _graph_with_ref(cid),
        "credential_payloads": [],  # even if present, no key
    }
    ws = FakeWebSocket()

    await handle_execute(ws, msg, reg, agent_private_key_pem=None)

    failed = [m for m in ws.sent if m.get("status") == "failed"]
    assert len(failed) == 1
    assert "credential resolution failed" in failed[0]["error"]["message"]
    # Node must not have been invoked
    assert RecordingNode.last_config is None


async def test_handle_execute_refs_without_payloads_fails(reg):
    priv, _ = _make_rsa_keypair()
    cid = uuid4()
    msg = {
        "type": "execute",
        "execution_id": str(uuid4()),
        "workflow_id": str(uuid4()),
        "graph": _graph_with_ref(cid),
        # credential_payloads missing entirely
    }
    ws = FakeWebSocket()

    await handle_execute(ws, msg, reg, agent_private_key_pem=priv)

    failed = [m for m in ws.sent if m.get("status") == "failed"]
    assert len(failed) == 1


async def test_handle_execute_bad_payload_fails_generic(reg):
    """Tampered ciphertext / wrong-key decrypt failure → generic message,
    no credential_id leak."""
    priv, pub = _make_rsa_keypair()
    other_priv, _ = _make_rsa_keypair()  # different keypair
    cid = uuid4()
    msg = {
        "type": "execute",
        "execution_id": str(uuid4()),
        "workflow_id": str(uuid4()),
        "graph": _graph_with_ref(cid),
        "credential_payloads": [_payload_for(pub, cid, {"x": 1})],
    }
    ws = FakeWebSocket()

    # Decrypt with WRONG private key
    await handle_execute(ws, msg, reg, agent_private_key_pem=other_priv)

    failed = [m for m in ws.sent if m.get("status") == "failed"]
    assert len(failed) == 1
    err = failed[0]["error"]["message"]
    assert "credential resolution failed" in err
    assert str(cid) not in err  # no id leak
