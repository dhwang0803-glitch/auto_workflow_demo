"""FastAPI dependency providers for AI_Agent."""
from __future__ import annotations

from fastapi import Request

from app.backends.protocols import LLMBackend
from app.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_backend(request: Request) -> LLMBackend:
    return request.app.state.backend
