"""PLAN_14 — NotionQueryDatabaseNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.notion_query_database import NotionQueryDatabaseNode


@pytest.fixture
def node():
    return NotionQueryDatabaseNode()


_DB_ID = "db-uuid-1"
_URL = f"https://api.notion.com/v1/databases/{_DB_ID}/query"
_SAMPLE_RESPONSE = {
    "results": [{"id": "p1"}, {"id": "p2"}],
    "has_more": True,
    "next_cursor": "cursor-abc",
}


async def test_notion_query_success(node, httpx_mock):
    httpx_mock.add_response(url=_URL, json=_SAMPLE_RESPONSE)
    result = await node.execute(
        {},
        {"api_token": "secret_abc", "database_id": _DB_ID},
    )
    assert result["count"] == 2
    assert result["has_more"] is True
    assert result["next_cursor"] == "cursor-abc"


async def test_notion_query_headers_and_body(node, httpx_mock):
    httpx_mock.add_response(url=_URL, json=_SAMPLE_RESPONSE)
    await node.execute(
        {},
        {
            "api_token": "secret_abc",
            "database_id": _DB_ID,
            "filter": {"property": "Status", "select": {"equals": "Done"}},
            "sorts": [{"property": "Name", "direction": "ascending"}],
            "page_size": 50,
        },
    )
    req = httpx_mock.get_request()
    assert req.headers["authorization"] == "Bearer secret_abc"
    assert req.headers["notion-version"] == "2022-06-28"
    body = json.loads(req.content)
    assert body["page_size"] == 50
    assert body["filter"]["property"] == "Status"
    assert body["sorts"][0]["direction"] == "ascending"


async def test_notion_query_error_raises(node, httpx_mock):
    httpx_mock.add_response(url=_URL, status_code=404, json={"message": "not found"})
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {"api_token": "secret_abc", "database_id": _DB_ID},
        )
