"""PLAN_11 — NotionCreatePageNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.notion_create_page import NotionCreatePageNode


@pytest.fixture
def node():
    return NotionCreatePageNode()


_NOTION_URL = "https://api.notion.com/v1/pages"
_SAMPLE_RESPONSE = {
    "id": "8a1b2c3d-0000-0000-0000-000000000000",
    "url": "https://www.notion.so/Title-8a1b2c3d0000000000000000000000000",
}


async def test_notion_create_page_success(node, httpx_mock):
    httpx_mock.add_response(url=_NOTION_URL, json=_SAMPLE_RESPONSE)
    result = await node.execute(
        {},
        {
            "api_token": "secret_abc",
            "parent": {"database_id": "db-1"},
            "properties": {"Name": {"title": [{"text": {"content": "hi"}}]}},
        },
    )
    assert result["page_id"] == _SAMPLE_RESPONSE["id"]
    assert result["url"] == _SAMPLE_RESPONSE["url"]


async def test_notion_create_page_headers_and_body(node, httpx_mock):
    httpx_mock.add_response(url=_NOTION_URL, json=_SAMPLE_RESPONSE)
    await node.execute(
        {},
        {
            "api_token": "secret_abc",
            "parent": {"database_id": "db-1"},
            "properties": {"Name": {"title": [{"text": {"content": "hi"}}]}},
            "children": [{"type": "paragraph", "paragraph": {}}],
        },
    )
    req = httpx_mock.get_request()
    assert req.headers["authorization"] == "Bearer secret_abc"
    assert req.headers["notion-version"] == "2022-06-28"
    body = json.loads(req.content)
    assert body["parent"] == {"database_id": "db-1"}
    assert body["children"] == [{"type": "paragraph", "paragraph": {}}]


async def test_notion_create_page_error_raises(node, httpx_mock):
    httpx_mock.add_response(url=_NOTION_URL, status_code=400, json={"message": "bad"})
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {
                "api_token": "secret_abc",
                "parent": {"database_id": "db-1"},
                "properties": {},
            },
        )
