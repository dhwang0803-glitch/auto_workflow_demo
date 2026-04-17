"""PLAN_14 — HubSpotCreateContactNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.hubspot_create_contact import HubSpotCreateContactNode


@pytest.fixture
def node():
    return HubSpotCreateContactNode()


_URL = "https://api.hubapi.com/crm/v3/objects/contacts"
_SAMPLE_RESPONSE = {
    "id": "201",
    "createdAt": "2026-04-18T00:00:00Z",
    "properties": {
        "email": "alice@example.com",
        "firstname": "Alice",
        "hs_object_id": "201",
    },
}


async def test_hubspot_create_contact_success(node, httpx_mock):
    httpx_mock.add_response(url=_URL, status_code=201, json=_SAMPLE_RESPONSE)
    result = await node.execute(
        {},
        {
            "api_token": "pat-na1-xyz",
            "properties": {"email": "alice@example.com", "firstname": "Alice"},
        },
    )
    assert result["contact_id"] == "201"
    assert result["created_at"] == "2026-04-18T00:00:00Z"
    assert result["properties"]["email"] == "alice@example.com"


async def test_hubspot_create_contact_auth_and_body(node, httpx_mock):
    httpx_mock.add_response(url=_URL, status_code=201, json=_SAMPLE_RESPONSE)
    await node.execute(
        {},
        {
            "api_token": "pat-na1-xyz",
            "properties": {"email": "bob@example.com"},
        },
    )
    req = httpx_mock.get_request()
    assert req.headers["authorization"] == "Bearer pat-na1-xyz"
    body = json.loads(req.content)
    assert body == {"properties": {"email": "bob@example.com"}}


async def test_hubspot_create_contact_error_raises(node, httpx_mock):
    httpx_mock.add_response(url=_URL, status_code=409, json={"message": "conflict"})
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {
                "api_token": "pat-na1-xyz",
                "properties": {"email": "dup@example.com"},
            },
        )
