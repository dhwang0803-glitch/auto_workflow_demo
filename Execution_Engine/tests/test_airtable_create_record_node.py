"""PLAN_11 — AirtableCreateRecordNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.airtable_create_record import AirtableCreateRecordNode


@pytest.fixture
def node():
    return AirtableCreateRecordNode()


_SAMPLE_RESPONSE = {
    "id": "recABC123",
    "createdTime": "2026-04-18T12:00:00.000Z",
    "fields": {"Name": "test"},
}


async def test_airtable_create_record_success(node, httpx_mock):
    httpx_mock.add_response(
        url="https://api.airtable.com/v0/appBase/Tasks", json=_SAMPLE_RESPONSE
    )
    result = await node.execute(
        {},
        {
            "api_token": "pat.xyz",
            "base_id": "appBase",
            "table": "Tasks",
            "fields": {"Name": "test"},
        },
    )
    assert result["record_id"] == "recABC123"
    assert result["created_time"] == _SAMPLE_RESPONSE["createdTime"]
    assert result["fields"] == {"Name": "test"}


async def test_airtable_create_record_url_encoding(node, httpx_mock):
    # table name with spaces must be URL-encoded into the path
    httpx_mock.add_response(
        url="https://api.airtable.com/v0/appBase/My%20Tasks", json=_SAMPLE_RESPONSE
    )
    await node.execute(
        {},
        {
            "api_token": "pat.xyz",
            "base_id": "appBase",
            "table": "My Tasks",
            "fields": {"Name": "test"},
        },
    )
    req = httpx_mock.get_request()
    assert str(req.url) == "https://api.airtable.com/v0/appBase/My%20Tasks"


async def test_airtable_create_record_bearer_header(node, httpx_mock):
    httpx_mock.add_response(
        url="https://api.airtable.com/v0/appBase/Tasks", json=_SAMPLE_RESPONSE
    )
    await node.execute(
        {},
        {
            "api_token": "pat.xyz",
            "base_id": "appBase",
            "table": "Tasks",
            "fields": {"Name": "test"},
        },
    )
    req = httpx_mock.get_request()
    assert req.headers["authorization"] == "Bearer pat.xyz"
    body = json.loads(req.content)
    assert body == {"fields": {"Name": "test"}}


async def test_airtable_create_record_error_raises(node, httpx_mock):
    httpx_mock.add_response(
        url="https://api.airtable.com/v0/appBase/Tasks",
        status_code=422,
        json={"error": {"type": "INVALID_REQUEST"}},
    )
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {
                "api_token": "pat.xyz",
                "base_id": "appBase",
                "table": "Tasks",
                "fields": {"Name": "test"},
            },
        )
