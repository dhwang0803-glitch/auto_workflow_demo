"""Node catalog endpoint — Frontend's palette/property-panel data source."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_catalog_requires_auth(client):
    r = await client.get("/api/v1/nodes/catalog")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_catalog_returns_all_registered_nodes(authed_client):
    r = await authed_client.get("/api/v1/nodes/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == len(body["nodes"])
    types = {n["type"] for n in body["nodes"]}
    # ADR-017 minimum catalog sanity — spot-check four diverse types.
    assert {"http_request", "email_send", "anthropic_chat", "slack_notify"} <= types
    assert body["total"] >= 21  # ADR-017 locks 21+


@pytest.mark.asyncio
async def test_catalog_surfaces_metadata_for_migrated_nodes(authed_client):
    r = await authed_client.get("/api/v1/nodes/catalog")
    nodes = {n["type"]: n for n in r.json()["nodes"]}

    http = nodes["http_request"]
    assert http["display_name"] == "HTTP Request"
    assert http["category"] == "network"
    assert http["description"]
    assert http["config_schema"]["type"] == "object"
    assert "url" in http["config_schema"]["properties"]


@pytest.mark.asyncio
async def test_catalog_unmigrated_node_falls_back_cleanly(authed_client):
    r = await authed_client.get("/api/v1/nodes/catalog")
    nodes = {n["type"]: n for n in r.json()["nodes"]}
    # openai_chat hasn't been migrated in PR #100 — must still render, with
    # display_name falling back to the type string.
    openai = nodes["openai_chat"]
    assert openai["display_name"] == "openai_chat"
    assert openai["category"] == "misc"
    assert openai["config_schema"] == {}


@pytest.mark.asyncio
async def test_catalog_categories_sorted_unique(authed_client):
    r = await authed_client.get("/api/v1/nodes/catalog")
    cats = r.json()["categories"]
    assert cats == sorted(set(cats))
    assert "network" in cats
    assert "email" in cats
    assert "ai" in cats
