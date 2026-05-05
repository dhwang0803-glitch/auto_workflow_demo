"""FastAPI dependency providers for AI_Agent."""
from __future__ import annotations

from fastapi import Request

from app.backends.protocols import EmbeddingBackend, LLMBackend
from app.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_backend(request: Request) -> LLMBackend:
    return request.app.state.backend


def get_embedding_backend(request: Request) -> EmbeddingBackend:
    return request.app.state.embedding
