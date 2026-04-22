"""LLMBackend Protocol — contract for LLM implementations.

Moved from API_Server/app/services/ai_composer_service.py during the
AI_Agent split (PLAN_11 PR 1). Implementations live in sibling modules:
`anthropic.AnthropicBackend`, `stub.StubLLMBackend`, and the future
`llamacpp_gemma.LlamaCppGemmaBackend` (PLAN_11 PR 2).
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol


class LLMBackend(Protocol):
    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        """Return the assistant's reply as raw text."""
        ...

    def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Yield text chunks as the model emits them.

        Implementations MUST close the underlying stream when the consumer
        stops iterating (e.g. on client disconnect).
        """
        ...
