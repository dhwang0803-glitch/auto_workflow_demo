"""AI_Agent runtime settings — loaded from env via pydantic-settings."""
from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Which LLMBackend impl to mount. `stub` = deterministic, no network.
    # `anthropic` requires ANTHROPIC_API_KEY. `llamacpp` talks to llama-server.
    llm_backend: Literal["stub", "anthropic", "llamacpp"] = "stub"

    # Anthropic (active when llm_backend=anthropic)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # llama.cpp server (active when llm_backend=llamacpp).
    # In the container, llama-server is a localhost subprocess started by
    # scripts/entrypoint.sh. In local dev, start it via scripts/run_llama_server.sh.
    llama_server_url: str = "http://127.0.0.1:8080"
    # Label echoed back in responses; llama-server doesn't route by it.
    llama_model_label: str = "gemma-4-26B-A4B-Q4_K_M"
    # Generation requests can run long on L4 — the ctx-size + max_tokens bound
    # the worst case. 120s is the Cloud Run request timeout default.
    llama_request_timeout_s: float = Field(default=120.0, gt=0)

    # Hard cap on max_tokens accepted from callers.
    max_tokens_ceiling: int = Field(default=16384, ge=512)
