"""AIAgentHTTPBackend — LLMBackend implementation that proxies to AI_Agent.

PLAN_11 PR 1 wires this as the preferred backend when
`settings.ai_agent_base_url` is set. Falls back to the in-tree
AnthropicBackend/StubLLMBackend when unset so envs without AI_Agent still
boot.

The HTTP contract lives in AI_Agent/app/models/http.py:
- POST /v1/complete  → `{text}` JSON
- POST /v1/stream    → chunked text/plain

See AI_Agent/docs/SPLIT.md for the boundary spec.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class AIAgentHTTPBackend:
    def __init__(self, *, base_url: str, timeout_s: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        # Generous overall timeout for cold-start (Cloud Run GPU 30-60s);
        # connect timeout is short since we fail fast if DNS/TCP misbehave.
        self._timeout = httpx.Timeout(timeout_s, connect=10.0)

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/complete",
                json={
                    "system": system,
                    "user_message": user_message,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            return resp.json()["text"]

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/stream",
                json={
                    "system": system,
                    "user_message": user_message,
                    "max_tokens": max_tokens,
                },
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    if chunk:
                        yield chunk
