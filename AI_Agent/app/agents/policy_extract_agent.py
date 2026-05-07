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

from langgraph.graph import END, StateGraph

from app.agents.eval import evaluate
from app.agents.state import AgentIteration, AgentState
from app.agents.tracing import traceable
from app.backends.protocols import LLMBackend
from app.services.policy_extract import extract_policies


def _format_hint(concerns: list[str]) -> str:
    """Reflect output: turn `EvalReport.coverage_concerns` into the
    "## Previous pass" body that policy_extract appends to its system
    prompt. Bulleted list — the prompt section header lives in
    `services.policy_extract._system_prompt`.
    """
    return "\n".join(f"- {c}" for c in concerns if c.strip())


def build_agent(backend: LLMBackend) -> Any:
    """Compile and return the policy_extract reflective agent graph.

    The closure captures the LLM backend so the compiled graph itself
    has no per-invocation parameters beyond the `AgentState` it's given.
    Test code passes a stub backend; production wiring (PR-C) passes
    the FastAPI dependency-injected backend.

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
