"""PLAN_08 — resolve_credential_refs unit tests.

Pure async function over in-memory fakes — no DB, no Celery.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.runtime.credentials import (
    graph_has_credential_refs,
    resolve_credential_refs,
)
from tests.fakes import InMemoryCredentialStore


@pytest.fixture
def store():
    return InMemoryCredentialStore()


async def test_no_refs_returns_original(store):
    owner = uuid4()
    graph = {
        "nodes": [{"id": "a", "type": "http_request", "config": {"url": "x"}}],
        "edges": [],
    }
    result = await resolve_credential_refs(graph, store, owner)
    assert result is graph  # no copy when no refs — cheap short-circuit


async def test_single_ref_injects_and_strips(store):
    owner = uuid4()
    cid = await store.store(
        owner, "smtp-1", {"user": "u@example.com", "password": "p"},
        credential_type="smtp",
    )
    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "email_send",
                "config": {
                    "smtp_host": "smtp.example.com",
                    "smtp_port": 587,
                    "credential_ref": {
                        "credential_id": str(cid),
                        "inject": {
                            "user": "smtp_user",
                            "password": "smtp_password",
                        },
                    },
                },
            }
        ],
        "edges": [],
    }
    resolved = await resolve_credential_refs(graph, store, owner)
    cfg = resolved["nodes"][0]["config"]
    assert cfg["smtp_user"] == "u@example.com"
    assert cfg["smtp_password"] == "p"
    assert "credential_ref" not in cfg
    # Other config keys preserved.
    assert cfg["smtp_host"] == "smtp.example.com"
    assert cfg["smtp_port"] == 587


async def test_multiple_refs_bulk_resolve(store):
    owner = uuid4()
    cid1 = await store.store(owner, "a", {"token": "aaa"})
    cid2 = await store.store(owner, "b", {"token": "bbb"})
    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "http_request",
                "config": {
                    "credential_ref": {
                        "credential_id": str(cid1),
                        "inject": {"token": "bearer"},
                    }
                },
            },
            {
                "id": "n2",
                "type": "http_request",
                "config": {
                    "credential_ref": {
                        "credential_id": str(cid2),
                        "inject": {"token": "bearer"},
                    }
                },
            },
        ],
        "edges": [],
    }
    resolved = await resolve_credential_refs(graph, store, owner)
    assert resolved["nodes"][0]["config"]["bearer"] == "aaa"
    assert resolved["nodes"][1]["config"]["bearer"] == "bbb"


async def test_owner_filter_propagates(store):
    owner_a = uuid4()
    owner_b = uuid4()
    cid = await store.store(owner_a, "a", {"token": "secret"})
    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "http_request",
                "config": {
                    "credential_ref": {
                        "credential_id": str(cid),
                        "inject": {"token": "bearer"},
                    }
                },
            }
        ],
        "edges": [],
    }
    with pytest.raises(KeyError):
        await resolve_credential_refs(graph, store, owner_b)


async def test_inject_missing_key_raises(store):
    owner = uuid4()
    cid = await store.store(owner, "a", {"token": "t"})
    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "http_request",
                "config": {
                    "credential_ref": {
                        "credential_id": str(cid),
                        # "typo" key not in decrypted dict
                        "inject": {"nonexistent": "bearer"},
                    }
                },
            }
        ],
        "edges": [],
    }
    with pytest.raises(KeyError):
        await resolve_credential_refs(graph, store, owner)


async def test_original_graph_not_mutated(store):
    owner = uuid4()
    cid = await store.store(owner, "a", {"user": "u"})
    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "email_send",
                "config": {
                    "credential_ref": {
                        "credential_id": str(cid),
                        "inject": {"user": "smtp_user"},
                    },
                },
            }
        ],
        "edges": [],
    }
    await resolve_credential_refs(graph, store, owner)
    original_cfg = graph["nodes"][0]["config"]
    assert "credential_ref" in original_cfg
    assert "smtp_user" not in original_cfg


def test_graph_has_credential_refs_true():
    graph = {
        "nodes": [
            {"id": "a", "type": "email_send", "config": {"credential_ref": {"credential_id": "x"}}}
        ],
        "edges": [],
    }
    assert graph_has_credential_refs(graph) is True


def test_graph_has_credential_refs_false():
    graph = {"nodes": [{"id": "a", "type": "http_request", "config": {}}], "edges": []}
    assert graph_has_credential_refs(graph) is False
