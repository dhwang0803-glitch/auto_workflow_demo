"""Pydantic request/response schemas for workflow CRUD.

Graph validation lives in `app.services.dag_validator`; these models only
enforce shape. The service layer runs DAG checks (cycle / edge refs)
after Pydantic parsing.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeSpec(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=64)
    config: dict = Field(default_factory=dict)


class EdgeSpec(BaseModel):
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)


class WorkflowGraph(BaseModel):
    nodes: list[NodeSpec] = Field(min_length=1)
    edges: list[EdgeSpec] = Field(default_factory=list)


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    settings: dict = Field(default_factory=dict)
    graph: WorkflowGraph


class WorkflowUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    settings: dict = Field(default_factory=dict)
    graph: WorkflowGraph


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    settings: dict
    graph: dict
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowListResponse(BaseModel):
    items: list[WorkflowSummary]
    total: int
    limit: int
    plan_tier: str
    approaching_limit: bool


class ActivateRequest(BaseModel):
    trigger_type: Literal["cron", "interval"]
    cron: str | None = None
    interval_seconds: int | None = Field(default=None, ge=10)

    @model_validator(mode="after")
    def _check_fields(self):
        if self.trigger_type == "cron" and not self.cron:
            raise ValueError("cron field required when trigger_type is cron")
        if self.trigger_type == "interval" and not self.interval_seconds:
            raise ValueError("interval_seconds required when trigger_type is interval")
        return self
