"""Container backend selection tests — the llm_backend env flag wires the
right impl without any actual network activity.
"""
from __future__ import annotations

import pytest

from app.backends.anthropic import AnthropicBackend
from app.backends.llamacpp_gemma import LlamaCppGemmaBackend
from app.backends.stub import StubLLMBackend
from app.config import Settings
from app.container import AIAgentContainer


def test_stub_backend_is_default() -> None:
    c = AIAgentContainer(Settings(llm_backend="stub"))
    assert isinstance(c.backend, StubLLMBackend)


def test_anthropic_backend_requires_api_key() -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AIAgentContainer(Settings(llm_backend="anthropic", anthropic_api_key=""))


def test_anthropic_backend_constructed_with_key() -> None:
    c = AIAgentContainer(
        Settings(llm_backend="anthropic", anthropic_api_key="sk-test", anthropic_model="m"),
    )
    assert isinstance(c.backend, AnthropicBackend)


def test_llamacpp_backend_constructed() -> None:
    # No network call at construction — just wires the HTTP client.
    c = AIAgentContainer(
        Settings(
            llm_backend="llamacpp",
            llama_server_url="http://llama-test:8080",
            llama_model_label="gemma-4-test",
        ),
    )
    assert isinstance(c.backend, LlamaCppGemmaBackend)
