"""Pydantic wire shapes for the skill-bootstrap endpoints (PLAN_12 W2-7).

Mirrors AI_Agent's wire shapes (`AI_Agent/app/models/skills.py`,
`AI_Agent/app/models/domain.py`) at the API_Server boundary so callers
don't need to know which service emitted a value. Backend forwarding
re-validates through these so a malformed AI_Agent response becomes a 502
at the API_Server layer rather than slipping through to the client.

W2-7 batch cut-over (this PR): `/answer` (single-shot) replaced by
`/answers` (batch — N (parameter, answer) pairs per policy → 1 SkillDraft).
PolicyGapBody now carries `parameters` / `sources` / `source_kind` from
the W2-4 polish redesign + `help_text` / `example_answer` from W2-4d so
the wizard can render parameter cards with attribution and inline help
without a second round-trip. `questions` stays as a backward-compat
alias of `parameters` for any client still on the old shape — drop after
Frontend ships PR #144.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Must stay aligned with AI_Agent/data/policies/*.yaml + AI_Agent's
# DomainCategory Literal. Any addition needs a coordinated change in both
# brands plus a new seed YAML.
DomainCategory = Literal[
    "ecommerce",
    "services",
    "consulting",
    "content",
    "nonprofit",
    "other",
]

# Mirrors Database.SkillStatus — kept independent so an API contract
# change here doesn't force a Database release.
SkillStatusLiteral = Literal[
    "active",
    "pending_review",
    "rejected",
    "archived",
]


# --- request bodies -------------------------------------------------------


class ClassifyDomainRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ExtractedSkillBody(BaseModel):
    """A skill the team has already declared (doc extraction or prior wizard).

    Persona A starts with an empty list. Persona B (W3 doc upload path)
    fills this with structured skills extracted from uploaded documents.
    """
    name: str = Field(min_length=1, max_length=255)
    condition: str = Field(min_length=1)
    action: str = Field(min_length=1)


class BootstrapRequest(BaseModel):
    domain: DomainCategory
    # Optional — frontend mints a UUID for the wizard session. We round-
    # trip it so subsequent /answer calls correlate without server-side
    # session storage. Per W2-7 design decisions: stateless.
    session_id: UUID | None = None
    extracted_skills: list[ExtractedSkillBody] = Field(default_factory=list)


class ParameterAnswerBody(BaseModel):
    """One (parameter_name, user_answer) pair within an /answers batch.

    `parameter` MUST be one of the seed policy's parameter names — AI_Agent
    rejects unknown names so a Frontend bug cannot smuggle phantom params
    into the LLM prompt.
    """
    parameter: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4000)


class AnswersRequest(BaseModel):
    """Batch input: one policy + per-parameter answers → one SkillDraft.

    `source_kind` / `sources` are forwarded from the wizard's
    `PolicyGapBody` so the persisted skill carries provenance into the
    library view without a second AI_Agent round-trip. Both are
    optional — Persona A (no doc upload) still works if the frontend
    omits them, but the library view will fall back to "synthesized".
    """
    session_id: UUID
    domain: DomainCategory
    policy_id: str = Field(min_length=1)
    answers: list[ParameterAnswerBody] = Field(min_length=1)
    source_kind: SourceKindLiteral | None = None
    sources: list[PolicySourceBody] = Field(default_factory=list)


# --- response bodies ------------------------------------------------------


class DomainClassificationResponse(BaseModel):
    domain: DomainCategory
    confidence: float
    rationale: str


# Honest labelling of where a policy comes from (memory project_wizard_polish_abc.md):
# - regulatory: grounded in a real legal/regulatory source
# - industry-baseline: linkable external industry references (Stripe, NRF, etc.)
# - synthesized: best-practice patchwork derived from training data; no
#   external authoritative URL exists. Library view shows this honestly.
SourceKindLiteral = Literal["regulatory", "industry-baseline", "synthesized"]


class PolicySourceBody(BaseModel):
    title: str
    url: str


class WizardQuestionBody(BaseModel):
    """One micro-question targeted at a specific seed parameter.

    `default_baseline` + `baseline_source` let the wizard offer a
    one-click "Use baseline" button with honest attribution.
    `help_text` (jargon explainer, 2-3 sentences) and `example_answer`
    (one-line placeholder) come from W2-4d.
    """
    text: str
    parameter: str | None = None
    default_baseline: str = ""
    baseline_source: str = ""
    help_text: str = ""
    example_answer: str = ""


class PolicyGapBody(BaseModel):
    policy_id: str
    policy_name: str
    parameters: list[WizardQuestionBody] = Field(default_factory=list)
    sources: list[PolicySourceBody] = Field(default_factory=list)
    source_kind: SourceKindLiteral = "synthesized"
    # Backward-compat alias of `parameters` for any client still on the
    # pre-#143 shape. AI_Agent emits the same payload in both fields;
    # drop after Frontend cuts over (PR #144).
    questions: list[WizardQuestionBody] = Field(default_factory=list)


class BootstrapResponse(BaseModel):
    session_id: UUID
    domain: DomainCategory
    missing: list[PolicyGapBody]


class SkillDraftBody(BaseModel):
    name: str
    description: str = ""
    condition: str
    action: str
    rationale: str = ""
    needs_clarification: bool = False
    clarification_hint: str = ""


class AnswersResponse(BaseModel):
    session_id: UUID
    skill_id: UUID
    draft: SkillDraftBody


class SkillResponse(BaseModel):
    """Full persisted skill record. Returned by approve / reject / list / get.

    `condition` and `action` stay as dicts because the W2-7 wizard wraps
    prose as `{"text": "..."}` but ADR-022 §1 leaves room for structured
    policies (compose-time matchers) that extend the JSONB shape.

    `source_kind` / `sources` carry provenance from the wizard turn
    (PolicyGapBody) — extracted from the skill's latest `source_ref`
    JSONB by the service layer. Optional: skills created before this
    field landed have `source_kind=None` and `sources=[]`.
    """
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    condition: dict
    action: dict
    scope: str
    status: SkillStatusLiteral
    created_at: datetime
    updated_at: datetime
    source_kind: SourceKindLiteral | None = None
    sources: list[PolicySourceBody] = Field(default_factory=list)


class SkillListResponse(BaseModel):
    skills: list[SkillResponse]
