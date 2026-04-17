"""PLAN_14 — AirtableListRecordsNode tests."""
from __future__ import annotations

import httpx
import pytest

from src.nodes.airtable_list_records import AirtableListRecordsNode


@pytest.fixture
def node():
    return AirtableListRecordsNode()


_SAMPLE_RESPONSE = {
    "records": [
        {"id": "recA", "createdTime": "2026-04-18T00:00:00Z", "fields": {"Name": "x"}},
        {"id": "recB", "createdTime": "2026-04-18T00:01:00Z", "fields": {"Name": "y"}},
    ],
    "offset": "next-cursor-xyz",
}


async def test_airtable_list_success(node, httpx_mock):
    httpx_mock.add_response(
        url="https://api.airtable.com/v0/appBase/Tasks?maxRecords=100",
        json=_SAMPLE_RESPONSE,
    )
    result = await node.execute(
        {},
        {"api_token": "pat.xyz", "base_id": "appBase", "table": "Tasks"},
    )
    assert result["count"] == 2
    assert result["offset"] == "next-cursor-xyz"
    assert result["records"][0]["id"] == "recA"


async def test_airtable_list_query_params(node, httpx_mock):
    httpx_mock.add_response(
        url="https://api.airtable.com/v0/appBase/Tasks?maxRecords=25&filterByFormula=%7BStatus%7D%3D%27Open%27&view=Grid",
        json=_SAMPLE_RESPONSE,
    )
    await node.execute(
        {},
        {
            "api_token": "pat.xyz",
            "base_id": "appBase",
            "table": "Tasks",
            "max_records": 25,
            "filter_by_formula": "{Status}='Open'",
            "view": "Grid",
        },
    )
    req = httpx_mock.get_request()
    assert req.headers["authorization"] == "Bearer pat.xyz"
    # Verify params made it into the URL (httpx_mock matched the exact URL above)
    assert "filterByFormula" in str(req.url)
    assert "view=Grid" in str(req.url)


async def test_airtable_list_error_raises(node, httpx_mock):
    httpx_mock.add_response(
        url="https://api.airtable.com/v0/appBase/Tasks?maxRecords=100",
        status_code=403,
        json={"error": {"type": "NOT_AUTHORIZED"}},
    )
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {"api_token": "pat.xyz", "base_id": "appBase", "table": "Tasks"},
        )
