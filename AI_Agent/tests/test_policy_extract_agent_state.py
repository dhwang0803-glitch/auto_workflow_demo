"""Schema tests for `app.agents.state` after the PR-β refactor.

PR-β removed `AgentState` (langgraph reducer model) — the agent loop
no longer maintains a centralized state object; iterations and final
reason live on `agent_loop.AgentResult` directly. What survives in
`state.py` is the slice of the trace that goes on the wire:
`AgentIteration`, `EvalReport`, `TerminationReason`. These tests pin
the shape so a future refactor doesn't quietly drop a field.
"""
from __future__ import annotations

import pytest

from app.agents.state import AgentIteration, EvalReport
from app.models.skills import SkillDraft


def _draft(name: str = "policy") -> SkillDraft:
    return SkillDraft(name=name, condition="cond", action="act")


def test_eval_report_decision_is_constrained() -> None:
    EvalReport(decision="converge")
    EvalReport(decision="retry")
    with pytest.raises(ValueError):
        EvalReport(decision="continue")  # type: ignore[arg-type]


def test_eval_report_defaults() -> None:
    r = EvalReport(decision="converge")
    assert r.coverage_concerns == []
    assert r.schema_issues == []
    assert r.rationale == ""


def test_agent_iteration_defaults() -> None:
    """An iteration may be in-flight (extract done, eval not yet) —
    both `eval=None` and `prompt_hint=""` must be allowed defaults so
    `extract_handler` can construct one without backfilling fields.
    """
    it = AgentIteration()
    assert it.drafts == []
    assert it.eval is None
    assert it.prompt_hint == ""


def test_agent_iteration_carries_drafts_and_eval() -> None:
    it = AgentIteration(
        drafts=[_draft()],
        prompt_hint="- look for thresholds",
        eval=EvalReport(decision="converge", rationale="ok"),
    )
    assert it.drafts[0].name == "policy"
    assert it.prompt_hint == "- look for thresholds"
    assert it.eval is not None
    assert it.eval.decision == "converge"
