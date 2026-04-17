"""PLAN_06 — SlackNotifyNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.slack import SlackNotifyNode


@pytest.fixture
def node():
    return SlackNotifyNode()


async def test_slack_notify_success(node, httpx_mock):
    httpx_mock.add_response(
        url="https://hooks.slack.com/services/AAA/BBB/CCC", json={"ok": True}
    )
    result = await node.execute(
        {},
        {
            "webhook_url": "https://hooks.slack.com/services/AAA/BBB/CCC",
            "text": "hello",
        },
    )
    assert result == {"status_code": 200, "ok": True}


async def test_slack_notify_error_raises(node, httpx_mock):
    httpx_mock.add_response(
        url="https://hooks.slack.com/services/AAA/BBB/CCC", status_code=500
    )
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {
                "webhook_url": "https://hooks.slack.com/services/AAA/BBB/CCC",
                "text": "hello",
            },
        )


async def test_slack_notify_sends_text_payload(node, httpx_mock):
    httpx_mock.add_response(
        url="https://hooks.slack.com/services/AAA/BBB/CCC", json={"ok": True}
    )
    await node.execute(
        {},
        {
            "webhook_url": "https://hooks.slack.com/services/AAA/BBB/CCC",
            "text": "deployment done",
        },
    )
    req = httpx_mock.get_request()
    assert req.method == "POST"
    assert json.loads(req.content) == {"text": "deployment done"}
