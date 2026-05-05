"""LlamaCppGemmaBackend — Gemma 4 served via llama.cpp's OpenAI-compatible API.

`llama-server` (from llama.cpp) exposes `/v1/chat/completions` with the same
wire format OpenAI uses, so we talk to it with raw httpx to avoid a full SDK
dependency. `/health` is probed by the Cloud Run startup probe via the
FastAPI `/v1/health` endpoint — model load takes 30-60s on L4 scale-to-zero.

The caller (API_Server AIComposerService) supplies a fully built prompt; we
just carry it over the HTTP boundary and stream the response back.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class LlamaCppGemmaBackend:
    def __init__(
        self,
        *,
        base_url: str,
        model_label: str,
        request_timeout_s: float,
        health_timeout_s: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_label = model_label
        self._health_timeout_s = health_timeout_s
        # Injected client keeps tests hermetic (MockTransport) and lets the
        # container own pool lifecycle.
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(request_timeout_s, connect=10.0),
        )

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        resp = await self._client.post(
            "/v1/chat/completions",
            json=self._chat_payload(system, user_message, max_tokens, stream=False),
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        # httpx's async stream context releases the connection deterministically
        # on exit — including when the consumer stops iterating (client disconnect).
        async with self._client.stream(
            "POST",
            "/v1/chat/completions",
            json=self._chat_payload(system, user_message, max_tokens, stream=True),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[len("data: ") :]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning("llama-server sent non-JSON SSE data: %r", payload)
                    continue
                delta = chunk["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield delta

    async def ready(self) -> bool:
        try:
            resp = await self._client.get("/health", timeout=self._health_timeout_s)
        except httpx.HTTPError as exc:
            logger.debug("llama-server /health probe failed: %s", exc)
            return False
        return resp.status_code == 200

    async def aclose(self) -> None:
        await self._client.aclose()

    def _chat_payload(
        self,
        system: str,
        user_message: str,
        max_tokens: int,
        *,
        stream: bool,
    ) -> dict:
        return {
            "model": self._model_label,
            "max_tokens": max_tokens,
            "stream": stream,
            # Every service that uses this backend (compose, classify,
            # gap_analyze, answers_to_skill, policy_extract) emits JSON.
            # Greedy decoding + GBNF grammar constraint together make
            # non-JSON output physically impossible. Default sampling
            # (temp=0.8, no grammar) hit 65-75% empty-response rate on
            # the live handbook smoke (2026-05-05) — model wandered into
            # control/whitespace token loops on dense reference chunks.
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            # Disable Gemma 4's <think>...</think> reasoning trace.
            # Without this, the chat-template parser strips the trace from
            # `content` but the model still spends 1500-3700 tokens
            # generating it (live smoke 2026-05-05 chunk #18: 76s wall,
            # 3832 tokens generated, 165 tokens visible). For structured-
            # JSON tasks reasoning produces no value, so we set both
            # llama.cpp's chat_template_kwargs.enable_thinking and the
            # server-side reasoning_format=none — whichever the running
            # build of llama-server understands wins, the other is
            # silently ignored.
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_format": "none",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        }
