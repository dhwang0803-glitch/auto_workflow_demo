"""PLAN_13 — AnthropicChatNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.anthropic_chat import AnthropicChatNode


@pytest.fixture
def node():
    return AnthropicChatNode()


_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_SAMPLE_RESPONSE = {
    "id": "msg_01",
    "model": "claude-opus-4-7",
    "content": [{"type": "text", "text": "hello back"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 5, "output_tokens": 2},
}


async def test_anthropic_chat_success(node, httpx_mock):
    httpx_mock.add_response(url=_ANTHROPIC_URL, json=_SAMPLE_RESPONSE)
    result = await node.execute(
        {},
        {
            "api_token": "sk-ant-test",
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
        },
    )
    assert result["content"] == "hello back"
    assert result["model"] == "claude-opus-4-7"
    assert result["stop_reason"] == "end_turn"
    assert result["usage"] == {"input_tokens": 5, "output_tokens": 2}


async def test_anthropic_chat_headers_and_body(node, httpx_mock):
    httpx_mock.add_response(url=_ANTHROPIC_URL, json=_SAMPLE_RESPONSE)
    await node.execute(
        {},
        {
            "api_token": "sk-ant-test",
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
            "system": "you are a helpful assistant",
            "temperature": 0.3,
        },
    )
    req = httpx_mock.get_request()
    # Anthropic uses x-api-key, not Authorization: Bearer
    assert req.headers["x-api-key"] == "sk-ant-test"
    assert "authorization" not in req.headers
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    # system is top-level, not in messages array
    assert body["system"] == "you are a helpful assistant"
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 100
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_anthropic_chat_error_raises(node, httpx_mock):
    httpx_mock.add_response(url=_ANTHROPIC_URL, status_code=401, json={"error": {}})
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {
                "api_token": "sk-ant-bad",
                "model": "claude-opus-4-7",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            },
        )
