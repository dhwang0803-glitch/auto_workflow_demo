"""StateGraph + node functions for the reflective policy_extract agent.

PLAN_13 §6 PR-B. Wires the data shapes from PR-A (state.py) and the
deterministic rules (eval.py) into a langgraph that loops at most
`AgentState.max_iter` times. The HTTP route + LLM judge come later.

Topology (PLAN_13 §4.1):

    extract  →  self_eval  ─converge / max_iter→  END
                    │
                    └─retry within budget→  reflect  ─no_change→  END
                                                │
                                                └─→  extract  (loop)

Termination is set inside the producing node (self_eval or reflect)
rather than via a separate finalize step — conditional edge functions
in langgraph cannot mutate state, so the node has to bake the reason in
before the edge router reads `state.terminated`.

Tracing: every node is wrapped in `@traceable`. With `LANGCHAIN_TRACING_V2`
unset (default in tests) the decorator is the identity from PR-A's
tracing.py — zero runtime cost. With it set, LangSmith captures the full
trace tree (inputs, outputs, timing) per PLAN_13 §3.
"""
from __future__ import annotations

from typing import Any

import logging

from langgraph.graph import END, StateGraph

from app.agents.eval import evaluate
from app.agents.judge import JudgeParseError, judge_extraction
from app.agents.state import AgentIteration, AgentState, EvalReport
from app.agents.tracing import traceable
from app.backends.protocols import LLMBackend
from app.services.policy_extract import extract_policies

logger = logging.getLogger(__name__)


def _format_hint(concerns: list[str]) -> str:
    """Reflect output: turn `EvalReport.coverage_concerns` into the
    "## Previous pass" body that policy_extract appends to its system
    prompt. Bulleted list — the prompt section header lives in
    `services.policy_extract._system_prompt`.
    """
    return "\n".join(f"- {c}" for c in concerns if c.strip())


def build_agent(
    backend: LLMBackend,
    *,
    judge_backend: LLMBackend | None = None,
) -> Any:
    """Compile and return the policy_extract reflective agent graph.

    The closure captures both the extraction backend and the optional
    judge backend so the compiled graph itself has no per-invocation
    parameters beyond the `AgentState` it's given.

    `judge_backend` is the LLM-judge hook from PLAN_13 §4.3 #4. Passing
    None (the default) keeps self_eval purely deterministic — the same
    behavior PR-A/PR-B shipped. Passing a backend (PR-D wiring at the
    route layer) lets self_eval ask the LLM "did the previous pass
    miss anything?" whenever the deterministic rules cleared with
    converge. Production passes the same backend for both; stubs and
    unit tests can pass a separate judge stub or leave it None.

    Returns the compiled langgraph; call `.ainvoke(state)` to run.
    """

    @traceable(name="extract", run_type="chain")
    async def extract_node(state: AgentState) -> dict:
        hint = state.pending_hint
        drafts = await extract_policies(
            backend,
            state.chunk,
            state.domain,
            images=state.images,
            prompt_hint=hint,
        )
        return {
            "in_flight": AgentIteration(
                drafts=drafts,
                prompt_hint=hint,
                eval=None,
            ),
            "pending_hint": "",
        }

    @traceable(name="self_eval", run_type="chain")
    async def self_eval_node(state: AgentState) -> dict:
        in_flight = state.in_flight
        if in_flight is None:
            # Defensive: extract should always populate in_flight before
            # self_eval runs. If we land here something upstream broke.
            return {"terminated": True, "reason": "schema_error"}

        report = evaluate(state.chunk, in_flight.drafts, state.iterations)

        # PLAN_13 §4.3 #4: judge runs only when deterministic rules
        # converged AND a judge backend is wired. The rules are cheaper
        # and more confident on retry decisions, so we save the judge
        # call for the case where they didn't trip.
        if (
            report.decision == "converge"
            and judge_backend is not None
            and len(state.iterations) + 1 < state.max_iter
        ):
            # Skip judge on the LAST allowed iteration too — flipping
            # to retry there would only land in max_iter_exhausted, so
            # the judge call is wasted budget.
            try:
                concerns = await judge_extraction(
                    judge_backend, state.chunk, in_flight.drafts
                )
            except JudgeParseError as exc:
                # Don't blow up the run on a malformed judge response.
                # Keep the deterministic verdict and log so PR-D's
                # smoke can flag judge instability.
                logger.warning(
                    "judge_extraction parse error — keeping deterministic "
                    "decision: %s",
                    exc,
                )
                concerns = []

            if concerns:
                report = EvalReport(
                    decision="retry",
                    coverage_concerns=concerns,
                    rationale=(
                        "judge flagged "
                        f"{len(concerns)} missed polic{'y' if len(concerns) == 1 else 'ies'} "
                        "after deterministic rules converged"
                    ),
                )

        finalized = in_flight.model_copy(update={"eval": report})

        update: dict = {
            "iterations": [finalized],
            "in_flight": None,
        }

        # Decide termination here (the conditional edge can only route,
        # not mutate). One iteration just finished, so completed count
        # is `len(state.iterations) + 1`.
        completed = len(state.iterations) + 1
        if report.decision == "converge":
            update["terminated"] = True
            update["reason"] = "converge"
        elif completed >= state.max_iter:
            update["terminated"] = True
            update["reason"] = "max_iter_exhausted"
        # else: retry within budget — reflect will run, no termination

        return update

    @traceable(name="reflect", run_type="chain")
    async def reflect_node(state: AgentState) -> dict:
        last = state.iterations[-1] if state.iterations else None
        if last is None or last.eval is None:
            return {"terminated": True, "reason": "schema_error"}

        new_hint = _format_hint(last.eval.coverage_concerns)

        # No-progress detection: if reflect would feed an empty hint or
        # the exact same hint that was already applied, another round
        # cannot help. Terminate before burning the Modal call. Rule 3
        # in eval.py catches the same staleness one iteration later
        # (drafts identical) — this is the early-exit shortcut.
        if not new_hint or new_hint == last.prompt_hint:
            return {
                "terminated": True,
                "reason": "no_change",
                "pending_hint": "",
            }

        return {"pending_hint": new_hint}

    def decide_after_eval(state: AgentState) -> str:
        return "end" if state.terminated else "reflect"

    def decide_after_reflect(state: AgentState) -> str:
        return "end" if state.terminated else "extract"

    g = StateGraph(AgentState)
    g.add_node("extract", extract_node)
    g.add_node("self_eval", self_eval_node)
    g.add_node("reflect", reflect_node)

    g.set_entry_point("extract")
    g.add_edge("extract", "self_eval")
    g.add_conditional_edges(
        "self_eval",
        decide_after_eval,
        {"end": END, "reflect": "reflect"},
    )
    g.add_conditional_edges(
        "reflect",
        decide_after_reflect,
        {"end": END, "extract": "extract"},
    )

    return g.compile()
