"""Pydantic schemas for agent registration."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class AgentRegisterRequest(BaseModel):
    public_key: str
    gpu_info: dict = {}


class AgentRegisterResponse(BaseModel):
    agent_id: UUID
    agent_token: str
