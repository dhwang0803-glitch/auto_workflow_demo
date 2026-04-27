"""Wire shapes for the skill-bootstrap pipeline (PLAN_12 W2-4).

Two endpoints share these:

- POST /v1/skills/gap_analyze        → which seed policies are not yet
                                       covered by the team's declared skills
- POST /v1/skills/answer_to_skill    → user's free-text answer compiled into
                                       a structured Skill draft for review

Output shapes intentionally mirror the `skills` DB table from PLAN_12 §5
(name / condition / action / rationale + needs_clarification flag from
ADR-022 §8.2). The DB write itself happens in the consumer PR (W2-7) once
the user approves a draft via the review UI.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.domain import DomainCategory


class ExtractedSkill(BaseModel):
    """A skill the team has already declared.

    Source-agnostic: comes from doc extraction (W3 path) or prior wizard
    answers. Only the fields gap_analyze actually needs for matching.
    """
    name: str = Field(min_length=1, max_length=255)
    condition: str = Field(min_length=1)
    action: str = Field(min_length=1)


class WizardQuestion(BaseModel):
    text: str = Field(min_length=1)
    parameter: str | None = None  # one of the seed policy's parameter names


class PolicyGap(BaseModel):
    policy_id: str  # exact id from data/policies/{domain}.yaml
    policy_name: str  # enriched from seed by the service for frontend display
    questions: list[WizardQuestion]


class GapAnalysis(BaseModel):
    missing: list[PolicyGap]


class GapAnalyzeRequest(BaseModel):
    domain: DomainCategory
    extracted_skills: list[ExtractedSkill] = Field(default_factory=list)


class AnswerToSkillRequest(BaseModel):
    domain: DomainCategory
    policy_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4000)


class SkillDraft(BaseModel):
    """Structured skill produced from a single Q+A turn.

    Pre-DB shape: no id, no workspace_id, no timestamps. Those get added
    when the user approves the draft via the review UI (W2-6/W2-7).
    """
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    condition: str = Field(min_length=1)
    action: str = Field(min_length=1)
    rationale: str = ""
    needs_clarification: bool = False
    clarification_hint: str = ""
