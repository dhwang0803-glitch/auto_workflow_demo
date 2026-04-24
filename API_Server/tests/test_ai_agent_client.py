"""AIAgentHTTPBackend bearer-header smoke.

The full request/response shape is exercised by test_ai_composer.py via the
service layer. These tests pin the bearer-header wiring so the Modal endpoint
auth contract doesn't silently regress.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services.ai_agent_client import AIAgentHTTPBackend


def _capture_handler(captured: list[dict[str, Any]]):
    """Build a MockTransport handler that records each request + returns
    a canned `/v1/complete` body. Stream tests use a separate fixture."""

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append({
            "url": str(request.url),
            "auth": request.headers.get("authorization"),
        })
        return httpx.Response(200, json={"text": "ok"})

    return _handler


@pytest.mark.asyncio
async def test_complete_attaches_bearer_when_token_set(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    monkeypatch.setattr(httpx, "AsyncClient", _patch_client(transport))

    backend = AIAgentHTTPBackend(
        base_url="https://agent.example",
        bearer_token="secret-x",
    )
    text = await backend.complete(system="s", user_message="u", max_tokens=32)

    assert text == "ok"
    assert captured[0]["auth"] == "Bearer secret-x"


@pytest.mark.asyncio
async def test_complete_omits_header_when_token_empty(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    monkeypatch.setattr(httpx, "AsyncClient", _patch_client(transport))

    backend = AIAgentHTTPBackend(base_url="https://agent.example")
    await backend.complete(system="s", user_message="u", max_tokens=32)

    assert captured[0]["auth"] is None


def _patch_client(transport: httpx.MockTransport):
    """Wrap httpx.AsyncClient so every instantiation in the backend rides on
    the same MockTransport. Backend uses `async with httpx.AsyncClient(...)`
    per call, so we can't pass transport directly — patch the constructor."""
    real_cls = httpx.AsyncClient

    class _Client(real_cls):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    return _Client
