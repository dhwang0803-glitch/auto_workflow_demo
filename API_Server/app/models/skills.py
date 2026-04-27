"""Pydantic wire shapes for the skill-bootstrap endpoints (PLAN_12 W2-7).

Mirrors AI_Agent's wire shapes (`AI_Agent/app/models/skills.py`,
`AI_Agent/app/models/domain.py`) at the API_Server boundary so callers
don't need to know which service emitted a value. Backend forwarding
re-validates through these so a malformed AI_Agent response becomes a 502
at the API_Server layer rather than slipping through to the client.
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


class AnswerRequest(BaseModel):
    session_id: UUID
    domain: DomainCategory
    policy_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4000)


# --- response bodies ------------------------------------------------------


class DomainClassificationResponse(BaseModel):
    domain: DomainCategory
    confidence: float
    rationale: str


class WizardQuestionBody(BaseModel):
    text: str
    parameter: str | None = None


class PolicyGapBody(BaseModel):
    policy_id: str
    policy_name: str
    questions: list[WizardQuestionBody]


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


class AnswerResponse(BaseModel):
    session_id: UUID
    skill_id: UUID
    draft: SkillDraftBody


class SkillResponse(BaseModel):
    """Full persisted skill record. Returned by approve / reject / list / get.

    `condition` and `action` stay as dicts because the W2-7 wizard wraps
    prose as `{"text": "..."}` but ADR-022 §1 leaves room for structured
    policies (compose-time matchers) that extend the JSONB shape.
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


class SkillListResponse(BaseModel):
    skills: list[SkillResponse]
