"""Pydantic response schemas for execution trigger + history."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workflow_id: UUID
    status: str
    execution_mode: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    error: dict | None = None


class ExecutionListResponse(BaseModel):
    items: list[ExecutionResponse]
    next_cursor: str | None = None
    has_more: bool
