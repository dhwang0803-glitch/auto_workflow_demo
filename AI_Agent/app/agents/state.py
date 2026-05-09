"""Shared data shapes for the policy_extract agent (PLAN_15 / ADR-024).

Originally housed the `AgentState` model that langgraph nodes mutated
through their reducer protocol (PLAN_13 PR-A/B). PR-β replaced the
graph with a ReAct agent loop driven by `policy_extract_agent.py`, so
the langgraph-specific state shape is gone. What remains here is the
slice of the trace that survives on the wire:

  * `EvalReport` — output of the deterministic + judge evaluation, per
    completed iteration.
  * `AgentIteration` — one (extract → evaluate) pair, plus the
    `prompt_hint` the extract call was given. Carries through to
    `models/agents.AgentTrace.iterations` for clients (API_Server proxy,
    Frontend wizard).
  * `TerminationReason` — vocabulary the agent's outer reason field
    uses on the wire.

Anything the agent loop tracks ad-hoc (raw model output per turn,
tool-call args, observation strings) lives in `agent_loop.AgentStep`
and is not surfaced on the public wire — it's available in LangSmith
traces and in-process for debug, but the public `agent_trace` shape
stays compatible with the pre-refactor schema so external consumers
(API_Server, Frontend) need no changes.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.skills import SkillDraft

# Termination reason vocabulary. "" means a run is still in flight; the
# agent loop sets one of these labels exactly once when it terminates.
# `no_change` is the agent_loop's `no_progress` translated into PLAN_13
# vocabulary so wire shape stays stable.
TerminationReason = Literal[
    "",
    "converge",
    "max_iter_exhausted",
    "no_change",
    "schema_error",
]

EvalDecision = Literal["converge", "retry"]


class EvalReport(BaseModel):
    """Output of `evaluate_coverage` — combination of deterministic
    rules (`agents.eval.evaluate`) and an optional LLM judge
    (`agents.judge.judge_extraction`).

    `coverage_concerns` is the natural-language list the agent feeds
    back into the next `extract_policies` call as a `hint`. The agent
    is told (via `_system_goal`) to use the most recent concerns
    verbatim; the prompt itself contains the chunk text so the model
    can look up surrounding context.
    """

    decision: EvalDecision
    coverage_concerns: list[str] = Field(default_factory=list)
    schema_issues: list[str] = Field(default_factory=list)
    rationale: str = ""


class AgentIteration(BaseModel):
    """One pass of (extract → evaluate). `prompt_hint` is the hint
    `extract_policies` was given on this pass — empty for iter 1, the
    previous iter's `coverage_concerns` joined with bullets for retries.

    `eval` is `None` while the iteration is in-flight (extract done,
    evaluate not yet called) — `policy_extract_agent.run_policy_extract_agent`
    only appends to `iterations` once the eval call lands, so any entry
    a wire client receives carries a finalized `EvalReport`.
    """

    drafts: list[SkillDraft] = Field(default_factory=list)
    eval: EvalReport | None = None
    prompt_hint: str = ""
