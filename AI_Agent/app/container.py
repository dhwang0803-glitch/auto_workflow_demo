"""AI_Agent DI container — picks the active LLMBackend per Settings."""
from __future__ import annotations

from app.backends.anthropic import AnthropicBackend
from app.backends.llamacpp_gemma import LlamaCppGemmaBackend
from app.backends.protocols import LLMBackend
from app.backends.stub import StubLLMBackend
from app.config import Settings


class AIAgentContainer:
    def __init__(
        self,
        settings: Settings,
        *,
        backend_override: LLMBackend | None = None,
    ) -> None:
        self.settings = settings
        if backend_override is not None:
            self.backend: LLMBackend = backend_override
            return

        if settings.llm_backend == "stub":
            self.backend = StubLLMBackend()
        elif settings.llm_backend == "anthropic":
            if not settings.anthropic_api_key:
                raise RuntimeError(
                    "llm_backend=anthropic but ANTHROPIC_API_KEY is empty"
                )
            self.backend = AnthropicBackend(
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
            )
        elif settings.llm_backend == "llamacpp":
            self.backend = LlamaCppGemmaBackend(
                base_url=settings.llama_server_url,
                model_label=settings.llama_model_label,
                request_timeout_s=settings.llama_request_timeout_s,
            )
        else:  # pragma: no cover — Literal narrows this, but be explicit.
            raise RuntimeError(f"Unknown llm_backend: {settings.llm_backend}")
