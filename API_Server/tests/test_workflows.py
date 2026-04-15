"""End-to-end workflow CRUD tests — PLAN_02."""
from __future__ import annotations

from uuid import uuid4


def _graph(cycle: bool = False) -> dict:
    nodes = [
        {"id": "a", "type": "noop", "config": {}},
        {"id": "b", "type": "noop", "config": {}},
    ]
    edges = [{"source": "a", "target": "b"}]
    if cycle:
        edges.append({"source": "b", "target": "a"})
    return {"nodes": nodes, "edges": edges}


def _body(name: str = "wf-1", **overrides) -> dict:
    body = {"name": name, "settings": {}, "graph": _graph()}
    body.update(overrides)
    return body


# --------------------------------------------------------------------- create


async def test_create_workflow_happy_path(authed_client):
    r = await authed_client.post("/api/v1/workflows", json=_body("first"))
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "first"
    assert body["is_active"] is True
    assert body["graph"]["nodes"][0]["id"] == "a"


async def test_create_workflow_with_cycle_rejected_422(authed_client):
    r = await authed_client.post(
        "/api/v1/workflows",
        json={"name": "cyc", "settings": {}, "graph": _graph(cycle=True)},
    )
    assert r.status_code == 422
    assert "cycle" in r.json()["detail"]


async def test_create_workflow_invalid_edge_reference_422(authed_client):
    r = await authed_client.post(
        "/api/v1/workflows",
        json={
            "name": "bad-edge",
            "settings": {},
            "graph": {
                "nodes": [{"id": "a", "type": "noop", "config": {}}],
                "edges": [{"source": "a", "target": "ghost"}],
            },
        },
    )
    assert r.status_code == 422
    assert "target" in r.json()["detail"]


async def test_create_workflow_quota_enforced_403(authed_client):
    # conftest sets light tier limit to 3.
    for i in range(3):
        r = await authed_client.post("/api/v1/workflows", json=_body(f"wf-{i}"))
        assert r.status_code == 201

    r = await authed_client.post("/api/v1/workflows", json=_body("wf-overflow"))
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "limit reached" in detail and "light" in detail


# ----------------------------------------------------------------------- list


async def test_list_workflows_returns_quota_metadata(authed_client):
    await authed_client.post("/api/v1/workflows", json=_body("one"))
    r = await authed_client.get("/api/v1/workflows")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["limit"] == 3           # from conftest override
    assert body["plan_tier"] == "light"
    assert body["approaching_limit"] is False
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "one"


async def test_list_workflows_approaching_limit_flag(authed_client):
    # 3 * 0.9 = 2.7 → threshold 2. After 3 rows the flag is on.
    for i in range(3):
        await authed_client.post("/api/v1/workflows", json=_body(f"wf-{i}"))
    r = await authed_client.get("/api/v1/workflows")
    body = r.json()
    assert body["total"] == 3
    assert body["approaching_limit"] is True


async def test_list_excludes_soft_deleted(authed_client):
    c = await authed_client.post("/api/v1/workflows", json=_body("keeper"))
    d = await authed_client.post("/api/v1/workflows", json=_body("doomed"))
    doomed_id = d.json()["id"]

    del_r = await authed_client.delete(f"/api/v1/workflows/{doomed_id}")
    assert del_r.status_code == 204

    r = await authed_client.get("/api/v1/workflows")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "keeper"


# ------------------------------------------------------------------ ownership


async def test_get_workflow_owned(authed_client):
    c = await authed_client.post("/api/v1/workflows", json=_body("mine"))
    wf_id = c.json()["id"]
    r = await authed_client.get(f"/api/v1/workflows/{wf_id}")
    assert r.status_code == 200
    assert r.json()["name"] == "mine"


async def test_get_workflow_not_found_returns_404(authed_client):
    r = await authed_client.get(f"/api/v1/workflows/{uuid4()}")
    assert r.status_code == 404


async def test_update_workflow_happy(authed_client):
    c = await authed_client.post("/api/v1/workflows", json=_body("v1"))
    wf_id = c.json()["id"]
    r = await authed_client.put(
        f"/api/v1/workflows/{wf_id}", json=_body("v2-renamed")
    )
    assert r.status_code == 200
    assert r.json()["name"] == "v2-renamed"


async def test_update_nonexistent_returns_404(authed_client):
    r = await authed_client.put(
        f"/api/v1/workflows/{uuid4()}", json=_body("whatever")
    )
    assert r.status_code == 404


async def test_delete_workflow_soft_deletes_and_reduces_count(authed_client):
    c = await authed_client.post("/api/v1/workflows", json=_body("goner"))
    wf_id = c.json()["id"]

    del_r = await authed_client.delete(f"/api/v1/workflows/{wf_id}")
    assert del_r.status_code == 204

    # Quota should free up — create again in the same slot.
    for i in range(3):
        r = await authed_client.post("/api/v1/workflows", json=_body(f"r-{i}"))
        assert r.status_code == 201

    # After delete + 3 new, total is 3, not 4 (cap respected).
    lr = await authed_client.get("/api/v1/workflows")
    assert lr.json()["total"] == 3
