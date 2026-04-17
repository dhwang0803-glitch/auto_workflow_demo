"""PLAN_11 — LinearCreateIssueNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.linear_create_issue import LinearCreateIssueNode


@pytest.fixture
def node():
    return LinearCreateIssueNode()


_LINEAR_URL = "https://api.linear.app/graphql"
_SAMPLE_RESPONSE = {
    "data": {
        "issueCreate": {
            "success": True,
            "issue": {
                "id": "issue-uuid",
                "identifier": "ENG-42",
                "url": "https://linear.app/acme/issue/ENG-42",
            },
        }
    }
}


async def test_linear_create_issue_success(node, httpx_mock):
    httpx_mock.add_response(url=_LINEAR_URL, json=_SAMPLE_RESPONSE)
    result = await node.execute(
        {},
        {
            "api_token": "lin_api_xxx",
            "team_id": "team-1",
            "title": "bug",
            "description": "steps to reproduce",
        },
    )
    assert result["success"] is True
    assert result["issue_id"] == "issue-uuid"
    assert result["identifier"] == "ENG-42"
    assert result["url"].endswith("/ENG-42")


async def test_linear_create_issue_auth_header_no_bearer(node, httpx_mock):
    httpx_mock.add_response(url=_LINEAR_URL, json=_SAMPLE_RESPONSE)
    await node.execute(
        {},
        {
            "api_token": "lin_api_xxx",
            "team_id": "team-1",
            "title": "bug",
        },
    )
    req = httpx_mock.get_request()
    # Linear Personal API Key: raw value, no "Bearer " prefix
    assert req.headers["authorization"] == "lin_api_xxx"


async def test_linear_create_issue_graphql_body(node, httpx_mock):
    httpx_mock.add_response(url=_LINEAR_URL, json=_SAMPLE_RESPONSE)
    await node.execute(
        {},
        {
            "api_token": "lin_api_xxx",
            "team_id": "team-1",
            "title": "bug",
            "description": "details",
        },
    )
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert "issueCreate" in body["query"]
    assert body["variables"]["input"] == {
        "teamId": "team-1",
        "title": "bug",
        "description": "details",
    }


async def test_linear_create_issue_error_raises(node, httpx_mock):
    httpx_mock.add_response(url=_LINEAR_URL, status_code=401, json={"errors": []})
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {
                "api_token": "lin_api_bad",
                "team_id": "team-1",
                "title": "bug",
            },
        )
