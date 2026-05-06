"""AnthropicBackend — wraps the official `anthropic` SDK.

Copied from API_Server/app/services/ai_composer_service.py during the
AI_Agent split. SDK imported lazily so test envs without the dep can
still import the module.
"""
from __future__ import annotations

from typing import AsyncIterator


def _user_content(user_message: str, images: list[str] | None) -> str | list[dict]:
    """Build the Anthropic `messages[].content` payload.

    Plain string when no images (preserves the original wire format and
    keeps dev/fallback behavior identical). When images are present, build
    a content-block list: image blocks first (the SDK convention for
    instruction-grounded vision), then the text block. Each image is a
    base64 data URL like `data:image/png;base64,...` — same shape the
    LlamaCpp backend consumes, so callers don't need backend-specific
    encoding.
    """
    if not images:
        return user_message
    blocks: list[dict] = []
    for img in images:
        if not img.startswith("data:"):
            # Non-data-URL inputs are out of contract — Anthropic SDK would
            # reject them anyway. Skip silently rather than 500-ing the dev
            # path; production traffic is on llamacpp.
            continue
        header, _, b64 = img[len("data:"):].partition(",")
        media_type, _, _ = header.partition(";")
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type or "image/png",
                    "data": b64,
                },
            }
        )
    blocks.append({"type": "text", "text": user_message})
    return blocks


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
        images: list[str] | None = None,
    ) -> str:
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=[
                # Cache the system prompt — the node catalog dominates token
                # count and is identical across a session.
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": _user_content(user_message, images)}],
        )
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return "".join(parts)

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> AsyncIterator[str]:
        # `messages.stream()` tears the HTTP connection down deterministically
        # on context exit — including when the consumer stops iterating early.
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": _user_content(user_message, images)}],
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
