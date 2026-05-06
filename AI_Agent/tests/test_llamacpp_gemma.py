"""Unit tests for LlamaCppGemmaBackend.

llama-server is mocked via httpx.MockTransport — the backend never spawns
a real process here. Covers OpenAI chat completion parsing (non-stream),
SSE delta assembly (stream), and /health probe behavior.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

import httpx
import pytest

from app.backends.llamacpp_gemma import LlamaCppGemmaBackend


def _backend_with(handler: Callable[[httpx.Request], Awaitable[httpx.Response]]) -> LlamaCppGemmaBackend:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://llama-test")
    return LlamaCppGemmaBackend(
        base_url="http://llama-test",
        model_label="gemma-4-test",
        request_timeout_s=5.0,
        client=client,
    )


@pytest.mark.asyncio
async def test_complete_posts_openai_payload_and_returns_message() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    backend = _backend_with(handler)
    try:
        result = await backend.complete(system="sys", user_message="ping", max_tokens=128)
    finally:
        await backend.aclose()

    assert result == "pong"
    body = captured["body"]
    assert body["model"] == "gemma-4-test"
    assert body["max_tokens"] == 128
    assert body["stream"] is False
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "ping"},
    ]


@pytest.mark.asyncio
async def test_stream_assembles_sse_deltas_in_order() -> None:
    # llama-server emits OpenAI-style SSE: `data: {json}\n\n` per token chunk,
    # terminated by `data: [DONE]`.
    def _sse(deltas: list[str]) -> bytes:
        frames = []
        for d in deltas:
            frame = {"choices": [{"index": 0, "delta": {"content": d}}]}
            frames.append(f"data: {json.dumps(frame)}\n\n")
        frames.append("data: [DONE]\n\n")
        return "".join(frames).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content.decode())["stream"] is True
        return httpx.Response(
            200,
            content=_sse(["one", "two", "three"]),
            headers={"content-type": "text/event-stream"},
        )

    backend = _backend_with(handler)
    try:
        collected: list[str] = []
        async for chunk in backend.stream(system="s", user_message="u", max_tokens=16):
            collected.append(chunk)
    finally:
        await backend.aclose()

    assert collected == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_stream_skips_non_content_deltas() -> None:
    # The very first SSE frame from llama-server contains role=assistant with
    # no content — must not emit an empty token.
    async def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    backend = _backend_with(handler)
    try:
        collected = [c async for c in backend.stream(system="s", user_message="u", max_tokens=8)]
    finally:
        await backend.aclose()

    assert collected == ["hi"]


@pytest.mark.asyncio
async def test_ready_true_when_health_200() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    backend = _backend_with(handler)
    try:
        assert await backend.ready() is True
    finally:
        await backend.aclose()


@pytest.mark.asyncio
async def test_ready_false_on_connection_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("llama-server not up")

    backend = _backend_with(handler)
    try:
        assert await backend.ready() is False
    finally:
        await backend.aclose()


@pytest.mark.asyncio
async def test_complete_strips_vision_channel_tag_prefix() -> None:
    # Phase A 2026-05-06 captured `<|channel>thought\n<channel|>VISION OK`
    # leaking from the chat-template's vision branch despite
    # enable_thinking=False. The backend strips the leading channel-tag
    # construct so callers see clean text.
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "<|channel>thought\n<channel|>VISION OK",
                        }
                    }
                ]
            },
        )

    backend = _backend_with(handler)
    try:
        result = await backend.complete(system="s", user_message="u", max_tokens=8)
    finally:
        await backend.aclose()

    assert result == "VISION OK"


@pytest.mark.asyncio
async def test_complete_preserves_content_with_inline_angle_brackets() -> None:
    # Real content can legitimately contain angle brackets (XML, JSX). The
    # strip MUST anchor at start and only consume a true channel-tag pair.
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "<div>ok</div>"}}
                ]
            },
        )

    backend = _backend_with(handler)
    try:
        result = await backend.complete(system="s", user_message="u", max_tokens=8)
    finally:
        await backend.aclose()

    assert result == "<div>ok</div>"


@pytest.mark.asyncio
async def test_complete_builds_vision_content_list_when_images_supplied() -> None:
    # Vision call: user message becomes an OpenAI content-list with text +
    # image_url block(s). Each image_url.url is the base64 data URL the
    # caller passed unchanged (Phase A 2026-05-06 confirmed external URL
    # fetching is unreliable in llama-server; inline base64 is the contract).
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    img = "data:image/png;base64,iVBORw0KGgo="
    backend = _backend_with(handler)
    try:
        await backend.complete(
            system="s", user_message="describe", max_tokens=8, images=[img]
        )
    finally:
        await backend.aclose()

    body = captured["body"]
    user_msg = body["messages"][1]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == [
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": img}},
    ]


@pytest.mark.asyncio
async def test_complete_keeps_string_user_message_when_no_images() -> None:
    # Text-only path keeps the legacy plain-string content. Lets text-mode
    # callers (and existing mocks) keep their wire shape.
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    backend = _backend_with(handler)
    try:
        await backend.complete(system="s", user_message="text", max_tokens=8)
    finally:
        await backend.aclose()

    assert captured["body"]["messages"][1] == {"role": "user", "content": "text"}


@pytest.mark.asyncio
async def test_ready_false_on_503() -> None:
    # llama-server returns 503 while the model is still loading; ready() must
    # propagate that so Cloud Run's startup probe keeps waiting.
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    backend = _backend_with(handler)
    try:
        assert await backend.ready() is False
    finally:
        await backend.aclose()
