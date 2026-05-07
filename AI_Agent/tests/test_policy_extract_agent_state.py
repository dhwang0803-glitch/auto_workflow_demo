"""Schema + reducer tests for `app.agents.state`.

PR-A scope (PLAN_13 §6) — model-only verification. The langgraph
StateGraph wiring that exercises the reducer end-to-end lands in PR-B;
here we just confirm the reducer math is the one we asked for, since
this is the pivot point of the whole graph (PLAN_13 §4.2).
"""
from __future__ import annotations

from operator import add
from typing import Annotated, get_args, get_origin

import pytest

from app.agents.state import (
    AgentIteration,
    AgentState,
    EvalReport,
)
from app.models.skills import SkillDraft


def _draft(name: str = "policy") -> SkillDraft:
    return SkillDraft(name=name, condition="cond", action="act")


def _eval(decision: str = "converge") -> EvalReport:
    return EvalReport(decision=decision, rationale="test")


def test_state_defaults_are_safe_for_a_fresh_run() -> None:
    s = AgentState(chunk="anything")
    assert s.iterations == []
    assert s.terminated is False
    assert s.reason == ""
    assert s.current_iter == 1
    assert s.latest is None


def test_max_iter_validated_to_a_sane_band() -> None:
    # 1 is permitted (single-pass mode, equivalent to non-reflective).
    AgentState(chunk="x", max_iter=1)

    with pytest.raises(ValueError):
        AgentState(chunk="x", max_iter=0)
    with pytest.raises(ValueError):
        AgentState(chunk="x", max_iter=10)


def test_iterations_field_carries_langgraph_add_reducer() -> None:
    """The reducer hint is what makes node returns append rather than
    replace. If a refactor drops it, every reflective run will look like
    a single-iteration run silently.
    """
    field = AgentState.model_fields["iterations"]
    annotation = field.metadata
    # pydantic surfaces Annotated metadata in `.metadata`; the reducer
    # we care about is `operator.add`.
    assert add in annotation, (
        f"AgentState.iterations lost its langgraph reducer; "
        f"metadata={annotation}"
    )


def test_current_iter_tracks_appended_iterations() -> None:
    s = AgentState(chunk="x")
    assert s.current_iter == 1

    s.iterations.append(AgentIteration(drafts=[_draft()], eval=_eval()))
    assert s.current_iter == 2
    assert s.latest is not None
    assert s.latest.drafts[0].name == "policy"


def test_eval_report_decision_is_constrained() -> None:
    # Allowed values
    EvalReport(decision="converge")
    EvalReport(decision="retry")
    # Anything else is a Pydantic validation error — keeps PR-B's graph
    # from routing on a typo.
    with pytest.raises(ValueError):
        EvalReport(decision="continue")  # type: ignore[arg-type]


def test_termination_reason_vocabulary() -> None:
    for reason in ("", "converge", "max_iter_exhausted", "no_change", "schema_error"):
        AgentState(chunk="x", reason=reason)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentState(chunk="x", reason="something_else")  # type: ignore[arg-type]


def test_chunk_is_required() -> None:
    """The agent has to be told what document chunk it's operating on —
    omitting it is always a programming error, not a defaultable case.
    """
    with pytest.raises(ValueError):
        AgentState()  # type: ignore[call-arg]
