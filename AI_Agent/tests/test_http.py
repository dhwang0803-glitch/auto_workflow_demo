"""Smoke tests for the AI_Agent HTTP boundary.

Covers /v1/health, /v1/complete (stub backend), and /v1/stream (chunk
delivery). Real-backend tests (AnthropicBackend, LlamaCppGemmaBackend)
live next to their impls.
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


class _FakeBackend:
    """Duck-typed LLMBackend recording prompts for assertion."""

    def __init__(
        self,
        *,
        response: str = "hello",
        stream_chunks: list[str] | None = None,
        is_ready: bool = True,
    ) -> None:
        self._response = response
        self._stream_chunks = stream_chunks or ["a", "b", "c"]
        self._is_ready = is_ready
        self.last_system: str | None = None
        self.last_user: str | None = None

    async def complete(self, *, system: str, user_message: str, max_tokens: int) -> str:
        self.last_system = system
        self.last_user = user_message
        return self._response

    async def stream(self, *, system: str, user_message: str, max_tokens: int) -> AsyncIterator[str]:
        self.last_system = system
        self.last_user = user_message
        for chunk in self._stream_chunks:
            yield chunk

    async def ready(self) -> bool:
        return self._is_ready

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_health_reports_backend() -> None:
    app = create_app(backend_override=_FakeBackend())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["backend"] == "stub"  # default Settings().llm_backend


@pytest.mark.asyncio
async def test_health_returns_503_when_backend_not_ready() -> None:
    # Cloud Run startup probe must see non-2xx while llama-server still loads,
    # otherwise traffic arrives before the model is mmap'd.
    app = create_app(backend_override=_FakeBackend(is_ready=False))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "starting"


@pytest.mark.asyncio
async def test_complete_forwards_prompt_and_returns_text() -> None:
    backend = _FakeBackend(response="world")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/complete",
            json={"system": "sys", "user_message": "hi", "max_tokens": 32},
        )
    assert resp.status_code == 200
    assert resp.json() == {"text": "world"}
    assert backend.last_system == "sys"
    assert backend.last_user == "hi"


@pytest.mark.asyncio
async def test_stream_yields_chunks_in_order() -> None:
    backend = _FakeBackend(stream_chunks=["one", "two", "three"])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with c.stream(
            "POST",
            "/v1/stream",
            json={"system": "sys", "user_message": "go", "max_tokens": 16},
        ) as resp:
            assert resp.status_code == 200
            collected: list[str] = []
            async for chunk in resp.aiter_text():
                if chunk:
                    collected.append(chunk)
    assert "".join(collected) == "onetwothree"
