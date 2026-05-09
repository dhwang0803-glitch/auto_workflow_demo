"""Wire shapes for the reflective policy_extract endpoint (PLAN_13 §3).

Lives in its own module to break the circular import between
`models/skills.py` (defines SkillDraft, which `agents/state.py`
imports) and the agent state types we want to surface on the wire.
Import direction is one-way:

    models/skills.py        defines SkillDraft
            ▲
            │  (imports SkillDraft)
            │
    agents/state.py         defines AgentIteration, EvalReport, TerminationReason
            ▲
            │  (imports AgentIteration + TerminationReason)
            │
    models/agents.py        defines reflective request/response shapes

The response packages the same internal AgentIteration + EvalReport
Pydantic models — operators get the full reasoning trace without an
external tracing UI (PLAN_13 §3 In-Scope: "응답에 `agent_trace`
포함"). The companion `langsmith_url` is populated when LangSmith
tracing is enabled (PR-D wires it); PR-C leaves it null.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.state import AgentIteration, TerminationReason
from app.models.domain import DomainCategory
from app.models.skills import SkillDraft


class PolicyExtractReflectiveRequest(BaseModel):
    """POST body for /v1/policy/extract_reflective.

    `chunk`, `domain`, `images` mirror PolicyExtractRequest exactly —
    a caller comparing the two endpoints A/B (the regression-guard use
    case from PLAN_13 §4.6) only has to add `max_iter`.

    `max_iter`:
      - 1 — single-pass mode, equivalent to /v1/policy/extract
      - 2 — default per PLAN_13 §4.5 latency budget
      - up to 5 — upper guard. Higher caps would be a different
        optimization regime; revisit if PR-D's smoke shows late
        iterations recovering candidates.
    """

    chunk: str = Field(min_length=1, max_length=8000)
    domain: DomainCategory = "other"
    images: list[str] | None = None
    max_iter: int = Field(default=2, ge=1, le=5)
    # Optional caller identity. Drives personalization retrieval
    # (`search_personal_skills` tool, PLAN_15 PR-γ). None / empty means
    # an anonymous request — the agent runs against an empty memory
    # pool, identical to the pre-PR-γ behavior. Format is opaque to
    # this layer; the file loader rejects values containing path
    # traversal characters.
    user_id: str | None = None


class AgentTrace(BaseModel):
    """Full reasoning trace returned alongside the final candidates.

    `iterations` is the ordered append-only log produced by the graph
    — each entry carries that pass's drafts, finalized EvalReport, and
    the prompt_hint the extract call actually applied. The graph
    cannot return an unterminated state, so `terminated` is always
    True on a successful response; `reason` names which of the four
    termination paths from PLAN_13 §4.1 fired.
    """

    iterations: list[AgentIteration] = Field(default_factory=list)
    terminated: bool
    reason: TerminationReason


class PolicyExtractReflectiveResponse(BaseModel):
    """Response body for /v1/policy/extract_reflective.

    `candidates` is the FINAL iteration's drafts. PLAN_13 §8 #4 chose
    "latest iter only" rather than union — reflect's hint enrichment
    means the latest iter is treated as a superset of earlier ones.
    If PR-D's smoke shows a real recall recovery in iter 1 that iter 2
    drops, the union policy will be revisited.

    `agent_trace` carries every intermediate pass for operator review;
    `langsmith_run_id`, if non-null, is the UUID langgraph used as the
    LangSmith run id when tracing was active. The client (smoke script,
    Frontend, dashboard link) can paste it into the LangSmith UI's
    search to navigate to the run — we don't construct a URL
    server-side because the canonical run URL needs the user's
    LangSmith org_id and project_id, neither of which the agent
    server carries.
    """

    candidates: list[SkillDraft] = Field(default_factory=list)
    agent_trace: AgentTrace
    langsmith_run_id: str | None = None
