"""Pydantic wire shapes for the personalization endpoints (PLAN_14 PR-G).

These mirror AI_Agent's `models/personalization.py` at the API_Server
boundary so Frontend never needs to know which service emitted a value.
AI_Agent responses are re-validated through these so a malformed Modal
payload becomes a 502 here rather than slipping through to the client.

`PersonalCandidateResponse` is the read-side shape — what Frontend
renders in the "Suggested from your edits" list. It's intentionally
narrower than `SkillResponse`: it carries only the personalization
fields a candidate UI cares about (the hint text, the diff signature
that produced it, status), without the full skill_sources audit trail.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Mirrors AI_Agent's DropReason. Empty string = candidate accepted.
DropReasonLiteral = Literal[
    "",
    "empty_diff",
    "empty_proposal",
    "hash_previously_rejected",
    "judge_reject",
]

PersonalCandidateStatusLiteral = Literal[
    "pending_review", "active", "rejected", "archived"
]


class ExtractFromDiffRequest(BaseModel):
    """Frontend trigger for one personalization extraction.

    Only `workflow_id` is required — API_Server selects the two
    revisions to diff (latest user_edit on top of its parent ai_draft).
    Front-end can override with explicit revision UUIDs in the future;
    omitted now because PR-H's auto-trigger on workflow save always
    targets the freshest pair.
    """

    workflow_id: UUID


class ExtractFromDiffResponse(BaseModel):
    """Result of one extract call.

    `candidate_id` is set when the agent accepted and a candidate row
    was persisted. `drop_reason` is the AI_Agent enum verbatim when a
    candidate was NOT created — Frontend can show "we looked, here's
    why nothing's new" without re-running the agent.

    `langsmith_run_id` echoes whatever AI_Agent minted; null when
    LangSmith is disabled or the route never reached the agent (e.g.,
    `empty_diff` short-circuited inside the agent).
    """

    candidate_id: UUID | None = None
    drop_reason: DropReasonLiteral = ""
    suggestion_hash: str | None = None
    diff_signature: str | None = None
    langsmith_run_id: str | None = None


class PersonalCandidateResponse(BaseModel):
    """One persisted personal-skill candidate.

    `hint` is the propose-stage generalization text (stored in
    `skills.condition.text`). `diff_signature` ties it back to the
    workflow change that produced it. Frontend uses both to render the
    review card.
    """

    id: UUID
    user_id: UUID
    hint: str
    diff_signature: str
    suggestion_hash: str | None = None
    status: PersonalCandidateStatusLiteral
    created_at: datetime
    updated_at: datetime


class PersonalCandidateListResponse(BaseModel):
    candidates: list[PersonalCandidateResponse] = Field(default_factory=list)


class RejectCandidateRequest(BaseModel):
    """Body for POST /candidates/{id}/reject.

    `reason` is free-form text the user can supply (e.g., "this was a
    one-off"). It lands in `personal_skill_reviews.rejection_reason`
    for the audit log; the per-user dedup guard uses the matching
    suggestion_hash regardless of reason content.
    """

    reason: str | None = Field(default=None, max_length=1000)


__all__ = [
    "DropReasonLiteral",
    "ExtractFromDiffRequest",
    "ExtractFromDiffResponse",
    "PersonalCandidateListResponse",
    "PersonalCandidateResponse",
    "PersonalCandidateStatusLiteral",
    "RejectCandidateRequest",
]
