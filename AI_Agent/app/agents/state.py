"""AgentState + AgentIteration + EvalReport for the policy_extract agent.

PR-A scope: data shapes only. Graph wiring (StateGraph, node functions,
conditional edges) lands in PR-B; the deterministic self_eval rule logic
lives in `eval.py`. EvalReport is colocated here rather than in eval.py
because AgentIteration carries it as a field — keeping it next to the
state model avoids the circular import that would otherwise force a
late-bound forward ref.

The langgraph reducer hint `Annotated[list, add]` lets a node return
`{"iterations": [new_iter]}` and have it appended automatically; without
the reducer the field would be replaced wholesale on every step (PLAN_13
§4.2).
"""
from __future__ import annotations

from operator import add
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models.domain import DomainCategory
from app.models.skills import SkillDraft

# Termination reason vocabulary. "" means the run is still in flight; the
# graph sets one of the four labels exactly once when END is reached.
TerminationReason = Literal[
    "",
    "converge",  # self_eval said the extraction is done
    "max_iter_exhausted",  # iter cap hit before convergence
    "no_change",  # reflect produced the same hint twice → no progress
    "schema_error",  # extract / judge response failed parse (defensive)
]

EvalDecision = Literal["converge", "retry"]


class EvalReport(BaseModel):
    """Output of the self_eval node (PLAN_13 §4.3).

    `coverage_concerns` is the natural-language list reflect turns into a
    prompt hint for the next iteration. PR-A only populates it from the
    deterministic rules in `eval.py`; PR-D adds an LLM judge that may
    extend the list.

    `schema_issues` should remain empty in normal operation —
    `policy_extract._parse_response` already raises on bad shapes before
    the agent ever sees the drafts. Kept here for defensive completeness
    so a future judge can flag inconsistencies the parser missed.
    """

    decision: EvalDecision
    coverage_concerns: list[str] = Field(default_factory=list)
    schema_issues: list[str] = Field(default_factory=list)
    rationale: str = ""


class AgentIteration(BaseModel):
    """One pass of (extract → eval). `prompt_hint` is the hint reflect
    injected into THIS iteration's extract call — empty for iter 1.

    `eval` is `None` while the iteration is in-flight (extract has
    produced drafts but self_eval hasn't yet run). It MUST be populated
    by the time the iteration lands in `AgentState.iterations` —
    completed iterations always carry a finalized EvalReport.
    """

    drafts: list[SkillDraft] = Field(default_factory=list)
    eval: EvalReport | None = None
    prompt_hint: str = ""


class AgentState(BaseModel):
    """Top-level state passed between langgraph nodes.

    `iterations` is the append-only completed-iteration log (reducer
    `add` lets self_eval return `{"iterations": [iter]}` and have it
    appended). The two transient buffers below are REPLACED on each
    return — they only carry data within a single extract→self_eval or
    reflect→extract handoff:

      - `in_flight` — extract writes the partial iteration (drafts +
        prompt_hint, eval=None). self_eval reads it, runs evaluate(),
        finalizes the iteration with the EvalReport, and appends to
        `iterations`. Cleared back to None on that same return.

      - `pending_hint` — reflect writes the hint string for the next
        iteration. extract reads it on entry and clears it on the same
        return that produces the new in_flight.
    """

    chunk: str
    images: list[str] | None = None
    domain: DomainCategory = "other"
    max_iter: int = Field(default=2, ge=1, le=5)
    iterations: Annotated[list[AgentIteration], add] = Field(default_factory=list)
    in_flight: AgentIteration | None = None
    pending_hint: str = ""
    terminated: bool = False
    reason: TerminationReason = ""

    @property
    def current_iter(self) -> int:
        """1-based index of the next iteration to run.

        Iter 1 is the first extract pass; iter 2 is the first re-extract
        after a retry decision. The conditional edge in PR-B uses this
        against `max_iter` to decide END vs reflect.
        """
        return len(self.iterations) + 1

    @property
    def latest(self) -> AgentIteration | None:
        return self.iterations[-1] if self.iterations else None
