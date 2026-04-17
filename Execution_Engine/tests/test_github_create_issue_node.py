"""PLAN_14 — GitHubCreateIssueNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.github_create_issue import GitHubCreateIssueNode


@pytest.fixture
def node():
    return GitHubCreateIssueNode()


_URL = "https://api.github.com/repos/acme/web/issues"
_SAMPLE_RESPONSE = {
    "id": 123456,
    "number": 42,
    "html_url": "https://github.com/acme/web/issues/42",
    "state": "open",
}


async def test_github_create_issue_success(node, httpx_mock):
    httpx_mock.add_response(url=_URL, status_code=201, json=_SAMPLE_RESPONSE)
    result = await node.execute(
        {},
        {"api_token": "ghp_xxx", "owner": "acme", "repo": "web", "title": "bug"},
    )
    assert result["issue_id"] == 123456
    assert result["number"] == 42
    assert result["url"].endswith("/42")
    assert result["state"] == "open"


async def test_github_create_issue_headers_and_body(node, httpx_mock):
    httpx_mock.add_response(url=_URL, status_code=201, json=_SAMPLE_RESPONSE)
    await node.execute(
        {},
        {
            "api_token": "ghp_xxx",
            "owner": "acme",
            "repo": "web",
            "title": "bug",
            "body": "steps",
            "labels": ["bug", "urgent"],
            "assignees": ["alice"],
        },
    )
    req = httpx_mock.get_request()
    assert req.headers["authorization"] == "Bearer ghp_xxx"
    assert req.headers["accept"] == "application/vnd.github+json"
    assert req.headers["x-github-api-version"] == "2022-11-28"
    body = json.loads(req.content)
    assert body == {
        "title": "bug",
        "body": "steps",
        "labels": ["bug", "urgent"],
        "assignees": ["alice"],
    }


async def test_github_create_issue_error_raises(node, httpx_mock):
    httpx_mock.add_response(url=_URL, status_code=422, json={"message": "validation"})
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {"api_token": "ghp_xxx", "owner": "acme", "repo": "web", "title": "bug"},
        )
