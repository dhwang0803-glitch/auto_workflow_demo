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
    # PLAN_14 §4.3 — the client tells us whether this save is an
    # AI-composed first draft or a user edit. Default user_edit so the
    # frontend can omit it on the manual-build path; the wizard's
    # post-compose Apply explicitly sends "ai_draft".
    revision_source: Literal["ai_draft", "user_edit"] = "user_edit"


class WorkflowUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    settings: dict = Field(default_factory=dict)
    graph: WorkflowGraph
    # PLAN_14 §4.3 — update is almost always a user_edit; we keep the
    # field configurable for parity with create and for the (rare) case
    # where /v1/compose re-drafts on top of an existing workflow.
    revision_source: Literal["ai_draft", "user_edit"] = "user_edit"


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


class WorkflowRevisionResponse(BaseModel):
    """PLAN_14 §4.3 revision row as surfaced over HTTP.

    `parent_revision_id` is the immediate ancestor (NULL on the seed
    revision). `payload` is the full WorkflowSchema at this revision —
    AI_Agent's diff function (PLAN_14 PR-C) reads two of these and
    compares them directly without us pre-computing a diff.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workflow_id: UUID
    revision_no: int
    source: Literal["ai_draft", "user_edit"]
    payload: dict
    parent_revision_id: UUID | None = None
    created_at: datetime | None = None
    created_by: UUID | None = None


class WorkflowRevisionListResponse(BaseModel):
    items: list[WorkflowRevisionResponse]
    limit: int
    offset: int


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
