"""Backend Protocols — contracts for LLM and Embedding implementations.

LLMBackend was moved from API_Server/app/services/ai_composer_service.py
during the AI_Agent split (PLAN_11 PR 1). Implementations live in sibling
modules: `anthropic.AnthropicBackend`, `stub.StubLLMBackend`,
`llamacpp_gemma.LlamaCppGemmaBackend` (PLAN_11 PR 2).

EmbeddingBackend (PLAN_12 W3-3, ADR-022 §8.5) describes the same shape for
text→vector conversion. `bge_embedding.BgeM3EmbeddingBackend` is the
production implementation (BAAI/bge-m3, 1024-dim) and
`stub_embedding.StubEmbeddingBackend` is the deterministic test/dev variant.
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
        enable_thinking: bool | None = None,
        temperature: float | None = None,
    ) -> str:
        """Return the assistant's reply as raw text.

        `enable_thinking` and `temperature` are optional per-call overrides
        used by the Phase 1 instrumentation surface in policy_extract — for
        backends without a meaningful interpretation (Anthropic, Stub) they
        are accepted and ignored.
        """
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

    async def ready(self) -> bool:
        """Return True when the backend can serve requests.

        For network-free backends (Stub) this is trivially True. For
        remote backends (Anthropic) this is True once constructed. For
        the llama-server subprocess this probes the underlying model's
        readiness — used by the Cloud Run startup probe on /v1/health.
        """
        ...

    async def aclose(self) -> None:
        """Release any held resources (HTTP pools, subprocess handles).

        Called from the FastAPI lifespan shutdown. Backends without
        resources to release should implement this as a no-op.
        """
        ...


class EmbeddingBackend(Protocol):
    """Text → fixed-dim vector. Implementations choose normalization.

    The DB column is `policy_extractions.embedding VECTOR(1024)` (PLAN_12
    §5) which is sized for BGE-M3. A backend whose `dimension` differs
    will fail the runtime sanity check in `services.policy_index` (W3-3
    follow-on); the field is exposed here so callers can assert at
    startup instead of at insert time.
    """

    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order.

        Vectors are returned as plain Python `list[float]` (not numpy)
        so callers don't need to depend on numpy. Implementations that
        normalize for cosine similarity should document it — the policy
        retrieval path (W3-6) assumes unit-length vectors.
        """
        ...

    async def ready(self) -> bool:
        """Return True when the backend can serve embed() calls.

        For Stub this is trivially True. For BGE-M3 this becomes True
        only after the SentenceTransformer model finishes loading
        (~2-3s on cuda, ~10-30s on cpu, plus a one-time HF download on
        first ever call).
        """
        ...

    async def aclose(self) -> None:
        """Release model weights / GPU memory. Lifespan shutdown hook."""
        ...
