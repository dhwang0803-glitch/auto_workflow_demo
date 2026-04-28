"""AnthropicBackend — wraps the official `anthropic` SDK.

Copied from API_Server/app/services/ai_composer_service.py during the
AI_Agent split. SDK imported lazily so test envs without the dep can
still import the module.
"""
from __future__ import annotations

from typing import AsyncIterator


class AnthropicBackend:
    def __init__(self, *, api_key: str, model: str) -> None:
        from anthropic import AsyncAnthropic  # local import — see docstring

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=[
                # Cache the system prompt — the node catalog dominates token
                # count and is identical across a session.
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return "".join(parts)

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        # `messages.stream()` tears the HTTP connection down deterministically
        # on context exit — including when the consumer stops iterating early.
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user_message}],
        ) as s:
            async for text in s.text_stream:
                yield text

    async def ready(self) -> bool:
        # The Anthropic SDK has no cheap liveness endpoint; the API itself is
        # the health signal. Assume remote is reachable — the first real call
        # will surface the failure if not.
        return True

    async def aclose(self) -> None:
        await self._client.close()
