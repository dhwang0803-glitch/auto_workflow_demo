"""Pydantic response schemas for webhook registration."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    secret: str
    workflow_id: UUID
    created_at: datetime | None = None
