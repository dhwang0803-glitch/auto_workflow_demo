"""Wire shapes for the skill-bootstrap pipeline (PLAN_12 W2-4).

Two endpoints share these:

- POST /v1/skills/gap_analyze        → which seed policies are not yet
                                       covered by the team's declared skills
- POST /v1/skills/answers_to_skill   → user's per-parameter answers compiled
                                       into a structured Skill draft (batch
                                       — N answers per policy → 1 Skill)
- POST /v1/skills/answer_to_skill    → legacy single-question shim
                                       (1 Q+A → 1 Skill draft). Kept while
                                       API_Server cuts over to the batch
                                       shape; remove after PR #144.

Output shapes mirror the `skills` DB table from PLAN_12 §5
(name / condition / action / rationale + needs_clarification flag from
ADR-022 §8.2). The DB write itself happens in the consumer PR (W2-7) once
the user approves a draft via the review UI.

2026-04-28 polish redesign (memory `project_wizard_polish_abc.md`):

- `WizardQuestion` carries `default_baseline` + `baseline_source` so the
  wizard can render a "Use baseline" button (B) with honest attribution (C)
- `PolicyGap` carries `parameters` (the new authoritative per-parameter
  list) + `sources` + `source_kind` for library-view rendering
- `questions` field on `PolicyGap` is preserved for backward compatibility
  with the W2-7 API_Server contract; PR #143 cuts API_Server over to
  `parameters`, after which `questions` can be dropped
"""
from __future__ import annotations

from typing import Literal

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
    """One micro-question targeted at a specific seed parameter.

    Question text comes verbatim from the seed YAML's
    `parameters[].prompt` — the LLM never generates this. `default_baseline`
    + `baseline_source` let the wizard offer a one-click adopt button with
    honest attribution. `help_text` (2-3 sentence jargon explainer) and
    `example_answer` (one-line placeholder) are W2-4d additions that let
    the wizard render an inline help row + ghost-text example without the
    LLM inventing them on the fly. Both are optional on the wire (default
    `""`) for forward-compat with custom seeds, but every shipped seed in
    `data/policies/*.yaml` is required by `tests/test_policy_seeds.py` to
    fill them.
    """
    text: str = Field(min_length=1)
    parameter: str | None = None  # one of the seed policy's parameter names
    default_baseline: str = ""
    baseline_source: str = ""
    help_text: str = ""
    example_answer: str = ""


# Honest labelling of where a policy comes from (memory project_wizard_polish_abc.md):
# - regulatory: the policy is grounded in a real legal/regulatory source
# - industry-baseline: the policy has external industry references that are
#   linkable (e.g. Stripe / NRF docs)
# - synthesized: best-practice patchwork derived from training data; no
#   external authoritative URL exists. Library view shows this as such.
SourceKind = Literal["regulatory", "industry-baseline", "synthesized"]


class PolicySource(BaseModel):
    title: str
    url: str


class PolicyGap(BaseModel):
    policy_id: str  # exact id from data/policies/{domain}.yaml
    policy_name: str  # enriched from seed by the service for frontend display
    parameters: list[WizardQuestion] = Field(default_factory=list)
    sources: list[PolicySource] = Field(default_factory=list)
    source_kind: SourceKind = "synthesized"
    # Backward-compat: alias of `parameters` for callers (API_Server pre-#143)
    # that still expect the old `questions` shape. Drop once #143 ships.
    questions: list[WizardQuestion] = Field(default_factory=list)


class GapAnalysis(BaseModel):
    missing: list[PolicyGap]


class GapAnalyzeRequest(BaseModel):
    domain: DomainCategory
    extracted_skills: list[ExtractedSkill] = Field(default_factory=list)


class ParameterAnswer(BaseModel):
    """One (parameter, user_answer) pair within an `answers_to_skill` batch.

    `parameter` MUST be one of the seed policy's parameter names; the
    service rejects unknown names so a UI bug cannot smuggle phantom params
    into the LLM prompt.
    """
    parameter: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4000)


class AnswersToSkillRequest(BaseModel):
    """Batch input: one policy + per-parameter answers → one SkillDraft."""
    domain: DomainCategory
    policy_id: str = Field(min_length=1)
    answers: list[ParameterAnswer] = Field(min_length=1)


class AnswerToSkillRequest(BaseModel):
    """Legacy single-shot shape (W2-7 contract). Kept until API_Server #143."""
    domain: DomainCategory
    policy_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4000)


class SkillDraft(BaseModel):
    """Structured skill produced from a single Q+A turn or a batch of N Q+A.

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


class PolicyExtractRequest(BaseModel):
    """Wire shape for POST /v1/policy/extract (PLAN_12 W3-4).

    The chunk is one slice of a parsed document (see services.document_parser).
    Domain is optional context — when known, gives the extractor a hint about
    typical policy shapes for that domain. Pass DomainCategory's "other" or
    omit when the team's domain is unclassified.

    `images` (Phase D) carries optional base64 PNG data URLs from
    document_parser's per-page render. When supplied, the LLM sees the
    rendered page alongside the extracted text — useful when the text
    extractor garbles a tabular layout but the image is legible. Each
    entry MUST be a `data:image/png;base64,...` URL (the
    LlamaCppGemmaBackend contract from Phase B).
    """
    chunk: str = Field(min_length=1, max_length=8000)
    domain: DomainCategory = "other"
    images: list[str] | None = None


class PolicyExtractResponse(BaseModel):
    """List of zero+ skill candidates the extractor found in the chunk.

    Empty list is a normal outcome — chunks describing org structure,
    history, or contact directories should produce no skills (ADR-022
    §8.1: skills are condition+action pairs, not topic mentions).
    """
    candidates: list[SkillDraft] = Field(default_factory=list)
