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
    # `anthropic` requires ANTHROPIC_API_KEY. `llamacpp` lands in PLAN_11 PR 2.
    llm_backend: Literal["stub", "anthropic", "llamacpp"] = "stub"

    # Anthropic (active when llm_backend=anthropic)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # llama.cpp server address (active when llm_backend=llamacpp) — future.
    llama_server_url: str = "http://127.0.0.1:8080"

    # Hard cap on max_tokens accepted from callers.
    max_tokens_ceiling: int = Field(default=16384, ge=512)
