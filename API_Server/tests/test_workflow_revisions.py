"""Workflow revision recording + read endpoint — PLAN_14 PR-B.

E2E against the live Postgres fixture in `conftest.py`. The save-hook
sits in `WorkflowService.create`/`update`, so every POST/PUT through
the workflows router is exercised. The read endpoint hits
`PostgresWorkflowRevisionRepository.list_by_workflow` via the same
ownership-checked service path the rest of `workflows.py` uses.
"""
from __future__ import annotations

from uuid import uuid4

import pytest


def _graph(n_extra: int = 0) -> dict:
    nodes = [
        {"id": "a", "type": "noop", "config": {}},
        {"id": "b", "type": "noop", "config": {}},
    ]
    for i in range(n_extra):
        nodes.append({"id": f"x{i}", "type": "noop", "config": {}})
    edges = [{"source": "a", "target": "b"}]
    return {"nodes": nodes, "edges": edges}


def _body(name: str = "wf", **overrides) -> dict:
    body = {"name": name, "settings": {}, "graph": _graph()}
    body.update(overrides)
    return body


# ------------------------------------------------------------------- create hook


async def test_create_records_seed_revision(authed_client):
    r = await authed_client.post("/api/v1/workflows", json=_body("seed"))
    assert r.status_code == 201
    wf_id = r.json()["id"]

    revs = await authed_client.get(f"/api/v1/workflows/{wf_id}/revisions")
    assert revs.status_code == 200
    items = revs.json()["items"]
    assert len(items) == 1
    seed = items[0]
    assert seed["workflow_id"] == wf_id
    assert seed["revision_no"] == 1
    # Default revision_source on create is user_edit (manual build path).
    assert seed["source"] == "user_edit"
    assert seed["parent_revision_id"] is None
    assert seed["payload"]["nodes"][0]["id"] == "a"


async def test_create_with_ai_draft_source(authed_client):
    r = await authed_client.post(
        "/api/v1/workflows",
        json=_body("from-compose", revision_source="ai_draft"),
    )
    assert r.status_code == 201
    wf_id = r.json()["id"]

    revs = await authed_client.get(f"/api/v1/workflows/{wf_id}/revisions")
    assert revs.json()["items"][0]["source"] == "ai_draft"


async def test_invalid_revision_source_rejected_422(authed_client):
    r = await authed_client.post(
        "/api/v1/workflows",
        json=_body("bad", revision_source="bogus"),
    )
    assert r.status_code == 422


# ------------------------------------------------------------------- update hook


async def test_update_appends_revision_linked_to_parent(authed_client):
    r = await authed_client.post(
        "/api/v1/workflows",
        json=_body("a", revision_source="ai_draft"),
    )
    wf_id = r.json()["id"]

    # Edit on top of the AI draft.
    upd = await authed_client.put(
        f"/api/v1/workflows/{wf_id}",
        json={
            "name": "a",
            "settings": {},
            "graph": _graph(n_extra=1),
            # default revision_source = "user_edit"
        },
    )
    assert upd.status_code == 200

    revs = (await authed_client.get(f"/api/v1/workflows/{wf_id}/revisions")).json()
    items = revs["items"]
    assert [r["revision_no"] for r in items] == [2, 1]
    latest, seed = items
    assert latest["source"] == "user_edit"
    assert latest["parent_revision_id"] == seed["id"]
    assert seed["source"] == "ai_draft"
    assert seed["parent_revision_id"] is None
    # The payload mirrors the saved graph (3 nodes after the edit).
    assert len(latest["payload"]["nodes"]) == 3
    assert len(seed["payload"]["nodes"]) == 2


async def test_multiple_updates_chain_parents(authed_client):
    r = await authed_client.post("/api/v1/workflows", json=_body("chain"))
    wf_id = r.json()["id"]
    for i in range(3):
        await authed_client.put(
            f"/api/v1/workflows/{wf_id}",
            json={"name": "chain", "settings": {}, "graph": _graph(n_extra=i + 1)},
        )

    items = (
        await authed_client.get(f"/api/v1/workflows/{wf_id}/revisions")
    ).json()["items"]
    assert [r["revision_no"] for r in items] == [4, 3, 2, 1]
    # parent_revision_id chains backwards by id.
    for newer, older in zip(items, items[1:]):
        assert newer["parent_revision_id"] == older["id"]
    # The seed at the tail has no parent.
    assert items[-1]["parent_revision_id"] is None


# ---------------------------------------------------------- list pagination + bounds


async def test_list_revisions_pagination(authed_client):
    r = await authed_client.post("/api/v1/workflows", json=_body("pager"))
    wf_id = r.json()["id"]
    for i in range(4):
        await authed_client.put(
            f"/api/v1/workflows/{wf_id}",
            json={"name": "pager", "settings": {}, "graph": _graph(n_extra=i + 1)},
        )
    # 1 seed + 4 updates = 5 revisions.

    page1 = (
        await authed_client.get(
            f"/api/v1/workflows/{wf_id}/revisions", params={"limit": 2, "offset": 0}
        )
    ).json()
    page2 = (
        await authed_client.get(
            f"/api/v1/workflows/{wf_id}/revisions", params={"limit": 2, "offset": 2}
        )
    ).json()
    assert [r["revision_no"] for r in page1["items"]] == [5, 4]
    assert [r["revision_no"] for r in page2["items"]] == [3, 2]
    assert page1["limit"] == 2 and page1["offset"] == 0
    assert page2["limit"] == 2 and page2["offset"] == 2


async def test_list_revisions_limit_bounds(authed_client):
    r = await authed_client.post("/api/v1/workflows", json=_body("bounds"))
    wf_id = r.json()["id"]

    # limit=0 violates Query(ge=1).
    too_small = await authed_client.get(
        f"/api/v1/workflows/{wf_id}/revisions", params={"limit": 0}
    )
    assert too_small.status_code == 422
    # limit>200 violates Query(le=200).
    too_big = await authed_client.get(
        f"/api/v1/workflows/{wf_id}/revisions", params={"limit": 201}
    )
    assert too_big.status_code == 422


# ---------------------------------------------------------- ownership / 404


async def test_list_revisions_unknown_workflow_404(authed_client):
    r = await authed_client.get(f"/api/v1/workflows/{uuid4()}/revisions")
    assert r.status_code == 404


async def test_list_revisions_other_owner_404(client, email_sender):
    """user-A's workflow is invisible to user-B (enumeration defence)."""
    # User A creates a workflow.
    a_email = "user-a@example.com"
    a_pw = "correct-horse-8"
    assert (
        await client.post(
            "/api/v1/auth/register", json={"email": a_email, "password": a_pw}
        )
    ).status_code == 201
    from urllib.parse import parse_qs, urlparse

    a_token_link = next(l for (to, l) in email_sender.sent if to == a_email)
    a_token = parse_qs(urlparse(a_token_link).query)["token"][0]
    await client.get("/api/v1/auth/verify", params={"token": a_token})
    a_login = await client.post(
        "/api/v1/auth/login", data={"username": a_email, "password": a_pw}
    )
    a_access = a_login.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {a_access}"
    wf = await client.post("/api/v1/workflows", json=_body("a-wf"))
    a_wf_id = wf.json()["id"]

    # User B logs in fresh and tries to read A's revisions.
    b_email = "user-b@example.com"
    b_pw = "correct-horse-9"
    assert (
        await client.post(
            "/api/v1/auth/register", json={"email": b_email, "password": b_pw}
        )
    ).status_code == 201
    b_token_link = next(l for (to, l) in email_sender.sent if to == b_email)
    b_token = parse_qs(urlparse(b_token_link).query)["token"][0]
    await client.get("/api/v1/auth/verify", params={"token": b_token})
    b_login = await client.post(
        "/api/v1/auth/login", data={"username": b_email, "password": b_pw}
    )
    client.headers["Authorization"] = f"Bearer {b_login.json()['access_token']}"

    snoop = await client.get(f"/api/v1/workflows/{a_wf_id}/revisions")
    assert snoop.status_code == 404
