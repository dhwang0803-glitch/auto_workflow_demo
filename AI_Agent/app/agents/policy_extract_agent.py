"""ReAct agent driver for policy_extract (PLAN_15 / ADR-024 / PR-β).

Replaces the langgraph StateGraph that lived here through PR-A/B/C/D.
The new shape:

    extract_policies(hint?)   ←─┐
            │                    │
            ▼                    │
       <observation>             │ agent decides
            │                    │ next call
            ▼                    │
    evaluate_coverage()  ────────┘
            │
            ▼ (decision==converge)
       <finish>{"drafts": [...]}

The agent (LLM) chooses tool order. We provide:

  * `extract_policies(hint)` — wraps `services.policy_extract.extract_policies`.
    The chunk / domain / images are baked into the closure so the LLM
    only juggles the hint (the only piece of state the *agent* picks).
  * `evaluate_coverage()` — wraps `eval.evaluate` (deterministic) and
    optionally `judge.judge_extraction` (LLM critic, same backend).

`<finish>` (no separate `finalize` tool) carries the final drafts. We
considered a `finalize(drafts)` tool for symmetry with the other two but
it would just be a wrapper for the same termination signal — the agent
narrative is cleaner with one canonical "I'm done" verb (Anthropic
agents convention).

External wire shape preserved: this module returns
`(iterations: list[AgentIteration], terminated: bool, reason: TerminationReason)`
which `models/agents.AgentTrace` consumes verbatim. API_Server / Frontend
unchanged. The shape is built by tracking each (extract → evaluate)
pair as an `AgentIteration` entry — same as the old langgraph trace.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from app.agents._tool_parse import ToolParseError
from app.agents.agent_loop import AgentResult, run_agent
from app.agents.eval import evaluate
from app.agents.judge import JudgeParseError, judge_extraction
from app.agents.state import AgentIteration, EvalReport, TerminationReason
from app.agents.tracing import traceable
from app.backends.protocols import LLMBackend
from app.models.domain import DomainCategory
from app.models.skills import SkillDraft
from app.services.policy_extract import (
    PolicyExtractParseError,
    extract_policies as extract_policies_service,
)

logger = logging.getLogger(__name__)


# Map agent_loop termination → existing AgentTrace.reason vocabulary so
# external callers (API_Server proxy, Frontend wizard) don't have to
# learn new strings.
_REASON_MAP: dict[str, TerminationReason] = {
    "finish": "converge",
    "max_iter_exhausted": "max_iter_exhausted",
    "no_progress": "no_change",
    "parse_error": "schema_error",
    "tool_not_found": "schema_error",
}


def _system_goal(domain: DomainCategory, max_iter: int) -> str:
    """Tell the agent its job + guardrails. Tone is medium-prescriptive:
    we describe the typical flow so the model has an easy default, but
    the actual decisions ("retry?", "what hint?", "when done?") still
    belong to the LLM. ADR-024 §6 picks this tone over either extreme.

    `max_iter` is named explicitly so the model can budget its passes —
    if we omit it the loop's hard cap still fires but the model wastes
    the late iterations re-extracting fruitlessly.
    """
    return (
        "You are an extraction agent that pulls policy candidates from "
        "ONE chunk of a team document.\n"
        f"\nThe chunk's domain is `{domain}`. Use the tools in this order:\n"
        "\n"
        "1. ALWAYS start by calling `extract_policies` — it returns "
        "candidates, or an empty list for boilerplate chunks. Do NOT "
        "decide on your own that a chunk is boilerplate; let "
        "`extract_policies` decide.\n"
        "2. Call `evaluate_coverage` to check whether your candidates "
        "cover every condition+action policy stated in the chunk.\n"
        "3. If the evaluator decides `retry`, call `extract_policies` "
        "AGAIN with a `hint` argument that names the gap. The "
        "evaluator's `coverage_concerns` field tells you what to "
        "target.\n"
        "4. Once the evaluator decides `converge`, emit a `finish` "
        "action with the final candidate list as `result`.\n"
        "\n"
        "## Rules\n"
        "\n"
        "- Do NOT invent policies the chunk does not state.\n"
        f"- Stop after at most {max_iter} extraction passes — reflection "
        "without textual support cannot recover absent rules.\n"
        "- The `drafts` you put in the finish `result` MUST be the EXACT "
        "JSON objects you received from the most recent `extract_policies` "
        "tool result. Do not paraphrase, drop fields, or reorder keys.\n"
        '- An empty `{"drafts": []}` is the correct finish when '
        "`extract_policies` returned no candidates and the evaluator "
        "agreed.\n"
        "\n"
        "## First-turn template\n"
        "\n"
        "Your first action is always `extract_policies`:\n"
        "\n"
        '{"thought": "Examining the chunk for policy candidates.", '
        '"action": "tool_call", "name": "extract_policies", "args": {}}\n'
    )


def _draft_dict(d: SkillDraft) -> dict[str, Any]:
    return d.model_dump()


def _drafts_from_dicts(values: Any) -> list[SkillDraft]:
    """Coerce a list-of-dicts the agent returned in `<finish>` into
    `SkillDraft` objects. The agent is told to copy verbatim from the
    last extract result, so this is mostly a re-validation step.
    """
    if not isinstance(values, list):
        return []
    out: list[SkillDraft] = []
    for v in values:
        if not isinstance(v, dict):
            continue
        try:
            out.append(SkillDraft(**v))
        except (TypeError, ValueError):
            # Skip malformed entries silently — the agent occasionally
            # drops `description` or invents a field. Better partial
            # output than 502.
            continue
    return out


@traceable(name="run_policy_extract_agent", run_type="chain")
async def run_policy_extract_agent(
    backend: LLMBackend,
    *,
    chunk: str,
    domain: DomainCategory = "other",
    images: list[str] | None = None,
    max_iter: int = 2,
    judge_backend: LLMBackend | None = None,
) -> tuple[list[AgentIteration], bool, TerminationReason, list[SkillDraft]]:
    """Drive the agent over one chunk and return a langgraph-compatible trace.

    Returns a 4-tuple: `(iterations, terminated, reason, final_candidates)`.
    The first three pack into `AgentTrace`; the fourth is what the
    response's `candidates` field carries (PLAN_13 §8 #4 latest-iter
    superset semantics).

    Raises `PolicyExtractParseError` if the FIRST extraction call fails
    to parse — same envelope as `/v1/policy/extract`. Later-iteration
    parse failures get folded into the agent trace as a normal tool
    error obs (the model can recover or finish).
    """
    iterations: list[AgentIteration] = []
    in_flight: AgentIteration | None = None
    parse_error_iter1: PolicyExtractParseError | None = None

    async def extract_handler(args: Mapping[str, Any]) -> Any:
        nonlocal in_flight, parse_error_iter1
        hint = str(args.get("hint", "") or "").strip()
        try:
            drafts = await extract_policies_service(
                backend,
                chunk,
                domain,
                images=images,
                prompt_hint=hint,
            )
        except PolicyExtractParseError as exc:
            if not iterations:
                # First extraction failed to parse — preserve the
                # original exception so the route handler can return
                # its 502 envelope unchanged. Re-raise into the
                # agent_loop's tool dispatcher, which will surface as
                # an error obs; the caller checks
                # `parse_error_iter1` after run_agent returns.
                parse_error_iter1 = exc
            raise

        in_flight = AgentIteration(
            drafts=drafts,
            prompt_hint=hint,
            eval=None,
        )
        return {"drafts": [_draft_dict(d) for d in drafts]}

    async def evaluate_handler(args: Mapping[str, Any]) -> Any:
        # `args` is unused — the tool reads from the closure's `in_flight`
        # and `iterations`. Allowing args lets the agent pass an empty
        # `{}` without us complaining; the `_OUTPUT_FORMAT` spec already
        # tells it `{}` is fine for no-arg calls.
        del args
        nonlocal in_flight
        if in_flight is None:
            return {
                "decision": "retry",
                "coverage_concerns": [
                    "call extract_policies before evaluate_coverage",
                ],
                "rationale": "no in-flight extraction to evaluate",
            }

        report = evaluate(chunk, in_flight.drafts, iterations)

        # Optional LLM judge on the converge path — same gate as the old
        # langgraph self_eval (judge runs only when deterministic rules
        # cleared, AND a judge backend is wired, AND we still have iter
        # budget left for a re-extract if the judge flips the verdict).
        completed = len(iterations) + 1
        if (
            report.decision == "converge"
            and judge_backend is not None
            and completed < max_iter
        ):
            try:
                concerns = await judge_extraction(
                    judge_backend, chunk, in_flight.drafts
                )
            except JudgeParseError as exc:
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
                        f"judge flagged {len(concerns)} missed "
                        f"polic{'y' if len(concerns) == 1 else 'ies'} "
                        "after deterministic rules converged"
                    ),
                )

        finalized = in_flight.model_copy(update={"eval": report})
        iterations.append(finalized)
        in_flight = None

        return {
            "decision": report.decision,
            "coverage_concerns": list(report.coverage_concerns),
            "rationale": report.rationale,
        }

    extract_tool = _make_tool(
        name="extract_policies",
        description=(
            "Extract candidate policies from the document chunk in scope. "
            "Pass an optional `hint` (string) to focus on a previously "
            "missed policy — the evaluator's `coverage_concerns` field "
            "supplies the right text. Returns a list of candidate skill "
            "drafts."
        ),
        parameters={
            "hint": (
                "string (optional) — a short phrase from the chunk "
                "naming what to look for in this pass. Use the "
                "evaluator's most recent coverage_concerns. Empty on "
                "the first call."
            ),
        },
        handler=extract_handler,
    )
    evaluate_tool = _make_tool(
        name="evaluate_coverage",
        description=(
            "Self-evaluate the most recent `extract_policies` result "
            "against the chunk. Returns `{decision, coverage_concerns, "
            "rationale}`. Use `coverage_concerns` as the `hint` to a "
            "follow-up `extract_policies` call when `decision==retry`."
        ),
        parameters={},
        handler=evaluate_handler,
    )

    user_request = f"chunk:\n```\n{chunk}\n```"

    # Loop budget: each completed reflective iteration is two tool calls
    # (extract + evaluate). Add headroom for the agent's <finish> turn
    # plus a tolerance for one stray retry (e.g., a tool_not_found that
    # the model recovers from on the next turn). 2 * max_iter + 4 covers
    # the worst observed paths in PR-α stub testing.
    loop_budget = max(8, max_iter * 2 + 4)

    try:
        result: AgentResult = await run_agent(
            backend,
            system_goal=_system_goal(domain, max_iter),
            user_request=user_request,
            tools=[extract_tool, evaluate_tool],
            max_iter=loop_budget,
        )
    except ToolParseError as exc:  # pragma: no cover — defensive
        # parse errors inside agent_loop are caught by the loop itself
        # and returned as terminated_reason="parse_error". This branch
        # only fires if the loop's own internal parsing raises — should
        # not happen, but better an explicit 502 than a crash.
        if parse_error_iter1 is not None:
            raise parse_error_iter1 from exc
        raise

    # If the first extraction call failed to parse, surface the original
    # exception — the route handler returns its 502 envelope from this.
    if parse_error_iter1 is not None:
        raise parse_error_iter1

    # If the agent issued <finish> without ever calling evaluate_coverage
    # on the last extraction, in_flight is still populated. Append it
    # with a placeholder eval so the trace doesn't lose that work.
    if in_flight is not None:
        iterations.append(
            in_flight.model_copy(
                update={
                    "eval": EvalReport(
                        decision="converge",
                        rationale=(
                            "agent finished without evaluating the last "
                            "extraction — drafts still surfaced"
                        ),
                    )
                }
            )
        )

    reason = _REASON_MAP.get(result.terminated_reason, "schema_error")

    # Final candidates: prefer the agent's <finish> payload (verbatim
    # copy of the last extract) → coerce to SkillDraft. Fall back to the
    # last iteration's drafts if the payload is missing or malformed
    # (PLAN_13 §8 #4 superset policy still applies to the trace).
    final_candidates: list[SkillDraft] = []
    if isinstance(result.final, dict) and "drafts" in result.final:
        final_candidates = _drafts_from_dicts(result.final["drafts"])
    if not final_candidates and iterations:
        final_candidates = list(iterations[-1].drafts)

    return iterations, True, reason, final_candidates


def _make_tool(*, name: str, description: str, parameters: dict[str, str], handler: Any) -> Any:
    """Local re-export of `agents.tool.Tool` to keep this module's import
    surface tight — callers don't need to import Tool to invoke the
    agent. Trivial wrapper, no logic.
    """
    from app.agents.tool import Tool

    return Tool(
        name=name,
        description=description,
        parameters=parameters,
        handler=handler,
    )
