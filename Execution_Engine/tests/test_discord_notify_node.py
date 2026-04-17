"""PLAN_13 — DiscordNotifyNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.discord_notify import DiscordNotifyNode


@pytest.fixture
def node():
    return DiscordNotifyNode()


_WEBHOOK_URL = "https://discord.com/api/webhooks/111/aaaa"


async def test_discord_notify_success(node, httpx_mock):
    httpx_mock.add_response(url=_WEBHOOK_URL, status_code=204)
    result = await node.execute(
        {},
        {"webhook_url": _WEBHOOK_URL, "content": "deploy complete"},
    )
    assert result == {"status_code": 204, "ok": True}


async def test_discord_notify_sends_content_payload(node, httpx_mock):
    httpx_mock.add_response(url=_WEBHOOK_URL, status_code=204)
    await node.execute(
        {},
        {
            "webhook_url": _WEBHOOK_URL,
            "content": "hello world",
            "username": "workflow-bot",
        },
    )
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"content": "hello world", "username": "workflow-bot"}


async def test_discord_notify_error_raises(node, httpx_mock):
    httpx_mock.add_response(url=_WEBHOOK_URL, status_code=404)
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {"webhook_url": _WEBHOOK_URL, "content": "x"},
        )
