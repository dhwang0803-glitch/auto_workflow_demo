"""PLAN_11 — OpenAIChatNode tests."""
from __future__ import annotations

import json

import httpx
import pytest

from src.nodes.openai_chat import OpenAIChatNode


@pytest.fixture
def node():
    return OpenAIChatNode()


_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_SAMPLE_RESPONSE = {
    "id": "chatcmpl-123",
    "model": "gpt-4o-mini",
    "choices": [
        {
            "message": {"role": "assistant", "content": "hello back"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


async def test_openai_chat_success(node, httpx_mock):
    httpx_mock.add_response(url=_OPENAI_URL, json=_SAMPLE_RESPONSE)
    result = await node.execute(
        {},
        {
            "api_token": "sk-test",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert result["content"] == "hello back"
    assert result["model"] == "gpt-4o-mini"
    assert result["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 7


async def test_openai_chat_bearer_header(node, httpx_mock):
    httpx_mock.add_response(url=_OPENAI_URL, json=_SAMPLE_RESPONSE)
    await node.execute(
        {},
        {
            "api_token": "sk-test",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2,
            "max_tokens": 100,
        },
    )
    req = httpx_mock.get_request()
    assert req.headers["authorization"] == "Bearer sk-test"
    body = json.loads(req.content)
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 100


async def test_openai_chat_error_raises(node, httpx_mock):
    httpx_mock.add_response(url=_OPENAI_URL, status_code=401, json={"error": "bad key"})
    with pytest.raises(httpx.HTTPStatusError):
        await node.execute(
            {},
            {
                "api_token": "sk-bad",
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
