"""Pydantic schemas for the AI Composer endpoint (PLAN_02 §4).

The wire shape mirrors the LLM's required JSON output. The router validates
LLM responses against `ComposeResult` so a malformed payload becomes a
`InvalidComposerResponseError` (502) instead of a Pydantic ValidationError
leaking to the client.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.workflow import EdgeSpec, NodeSpec


class ProposedDag(BaseModel):
    nodes: list[NodeSpec] = Field(default_factory=list)
    edges: list[EdgeSpec] = Field(default_factory=list)


class DiffNodeChange(BaseModel):
    id: str
    config: dict = Field(default_factory=dict)


class ComposeDiff(BaseModel):
    """Set-style diff between current DAG and proposed_dag.

    The Frontend renders this as Accept/Reject — applying it means
    `proposed_dag` replaces `current_dag` wholesale, but the diff lets the
    UI highlight what changed.
    """

    added_nodes: list[NodeSpec] = Field(default_factory=list)
    removed_node_ids: list[str] = Field(default_factory=list)
    modified_nodes: list[DiffNodeChange] = Field(default_factory=list)


ComposeIntent = Literal["clarify", "draft", "refine"]


class ComposeResult(BaseModel):
    """LLM output payload. The model fills these fields per `intent`:

    - `clarify`: clarify_questions populated, proposed_dag/diff null
    - `draft`:   proposed_dag populated, diff null
    - `refine`:  proposed_dag + diff populated (current_dag must have been provided)
    """

    model_config = ConfigDict(extra="ignore")

    intent: ComposeIntent
    clarify_questions: list[str] | None = None
    proposed_dag: ProposedDag | None = None
    diff: ComposeDiff | None = None
    rationale: str = ""


class ComposeRequest(BaseModel):
    """Frontend → API. `current_dag` is null for the first turn."""

    session_id: UUID | None = None
    message: str = Field(min_length=1, max_length=4000)
    current_dag: ProposedDag | None = None


class ComposeResponse(BaseModel):
    """Server → Frontend wrapper. Adds session_id so the next turn can
    reference history."""

    session_id: UUID
    result: ComposeResult
