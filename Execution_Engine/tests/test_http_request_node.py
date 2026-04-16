"""PLAN_01 — HttpRequestNode + NodeRegistry tests."""
from __future__ import annotations

import pytest
import httpx

from src.nodes.base import BaseNode
from src.nodes.http_request import HttpRequestNode
from src.nodes.registry import NodeRegistry, registry


async def test_http_request_get_happy(httpx_mock):
    httpx_mock.add_response(url="https://api.example.com/data", json={"ok": True})
    node = HttpRequestNode()
    result = await node.execute({}, {"url": "https://api.example.com/data"})
    assert result["status_code"] == 200
    assert '"ok"' in result["body"]


async def test_http_request_post_with_body(httpx_mock):
    httpx_mock.add_response(url="https://api.example.com/submit", json={"id": 1})
    node = HttpRequestNode()
    result = await node.execute(
        {},
        {"url": "https://api.example.com/submit", "method": "POST", "body": {"name": "test"}},
    )
    assert result["status_code"] == 200
    req = httpx_mock.get_request()
    assert req.method == "POST"


async def test_http_request_timeout():
    node = HttpRequestNode()
    with pytest.raises(httpx.TimeoutException):
        await node.execute({}, {"url": "https://10.255.255.1", "timeout_seconds": 0.1})


def test_registry_register_and_get():
    r = NodeRegistry()
    r.register(HttpRequestNode)
    assert r.get("http_request") is HttpRequestNode
    assert "http_request" in r.list_types()
    with pytest.raises(KeyError):
        r.get("nonexistent")
