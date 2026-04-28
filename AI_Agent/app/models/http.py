"""HTTP wire shapes for /v1/complete and /v1/stream.

AI_Agent is a low-level LLM proxy (model X per AI_Agent/docs/SPLIT.md PR
notes) — the caller (API_Server AIComposerService) supplies the fully built
system+user prompt. AI_Agent returns raw text; caller does any parsing.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CompleteRequest(BaseModel):
    system: str = Field(min_length=1)
    user_message: str = Field(min_length=1)
    max_tokens: int = Field(default=4096, ge=16, le=16384)


class CompleteResponse(BaseModel):
    text: str


class HealthResponse(BaseModel):
    status: str = "ok"
    backend: str
