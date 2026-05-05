"""AI_Agent DI container — picks the active LLM + Embedding backends per Settings."""
from __future__ import annotations

from app.backends.anthropic import AnthropicBackend
from app.backends.llamacpp_gemma import LlamaCppGemmaBackend
from app.backends.protocols import EmbeddingBackend, LLMBackend
from app.backends.stub import StubLLMBackend
from app.backends.stub_embedding import StubEmbeddingBackend
from app.config import Settings


class AIAgentContainer:
    def __init__(
        self,
        settings: Settings,
        *,
        backend_override: LLMBackend | None = None,
        embedding_override: EmbeddingBackend | None = None,
    ) -> None:
        self.settings = settings
        self.backend: LLMBackend = (
            backend_override if backend_override is not None
            else self._build_llm_backend(settings)
        )
        self.embedding: EmbeddingBackend = (
            embedding_override if embedding_override is not None
            else self._build_embedding_backend(settings)
        )

    @staticmethod
    def _build_llm_backend(settings: Settings) -> LLMBackend:
        if settings.llm_backend == "stub":
            return StubLLMBackend()
        if settings.llm_backend == "anthropic":
            if not settings.anthropic_api_key:
                raise RuntimeError(
                    "llm_backend=anthropic but ANTHROPIC_API_KEY is empty"
                )
            return AnthropicBackend(
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
            )
        if settings.llm_backend == "llamacpp":
            return LlamaCppGemmaBackend(
                base_url=settings.llama_server_url,
                model_label=settings.llama_model_label,
                request_timeout_s=settings.llama_request_timeout_s,
            )
        # pragma: no cover — Literal narrows this, but be explicit.
        raise RuntimeError(f"Unknown llm_backend: {settings.llm_backend}")

    @staticmethod
    def _build_embedding_backend(settings: Settings) -> EmbeddingBackend:
        if settings.embedding_backend == "stub":
            return StubEmbeddingBackend()
        if settings.embedding_backend == "bge_m3":
            # Local import — sentence-transformers / torch only ship in the
            # Modal production image. A dev container with EMBEDDING_BACKEND=stub
            # never imports this module.
            from app.backends.bge_embedding import BgeM3EmbeddingBackend

            device = settings.embedding_device or None
            return BgeM3EmbeddingBackend(device=device)
        # pragma: no cover — Literal narrows this, but be explicit.
        raise RuntimeError(
            f"Unknown embedding_backend: {settings.embedding_backend}"
        )
