"""ReAct agent driver for policy_extract (PLAN_15 / ADR-024 / PR-β + γ).

Replaces the langgraph StateGraph that lived here through PR-A/B/C/D.
The new shape (PR-β with optional PR-γ retrieval prepended):

    [search_personal_skills(query)]   ← PR-γ, registered only when
            │                            a non-empty user memory pool
            ▼                            is supplied
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
  * `search_personal_skills(query, k)` — optional, registered only
    when a populated `PersonalMemoryPool` and an `EmbeddingBackend`
    reach this driver. With no pool / empty pool / no embedder, the
    tool is absent from the catalog and the system goal omits its
    guidance section, preserving the cold-start baseline (Path 1
    design, memory `project_personalization_memory_pattern.md`).

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
from app.backends.protocols import EmbeddingBackend, LLMBackend
from app.models.domain import DomainCategory
from app.models.skills import SkillDraft
from app.services.personal_memory import PersonalMemoryPool
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


def _system_goal(
    domain: DomainCategory, max_iter: int, *, memory_enabled: bool
) -> str:
    """Tell the agent its job + guardrails. Tone is medium-prescriptive:
    we describe the typical flow so the model has an easy default, but
    the actual decisions ("retry?", "what hint?", "when done?") still
    belong to the LLM. ADR-024 §6 picks this tone over either extreme.

    `max_iter` is named explicitly so the model can budget its passes —
    if we omit it the loop's hard cap still fires but the model wastes
    the late iterations re-extracting fruitlessly.

    `memory_enabled` controls whether `search_personal_skills` is in
    the tool catalog. When False (cold-start, anonymous request, or
    feature disabled) the goal text omits the retrieval guidance
    entirely so the model isn't tempted to call a tool that isn't
    registered.
    """
    base = (
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

    if not memory_enabled:
        return base

    # Memory tool is registered — teach the model when (and when NOT)
    # to use it. The "before extract_policies" placement matters: if
    # the model interleaves it after the first extract, the matches
    # cannot influence iter 1, defeating the point. The "ONCE per
    # chunk" cap keeps the LLM cost bounded — there is no scenario
    # where calling search a second time helps.
    extra = (
        "\n## Optional retrieval (the user's past edits)\n"
        "\n"
        "You also have `search_personal_skills(query, k=3)` available. "
        "It looks up policies the user has previously hand-edited and "
        "returns matches plus a `pool_size` field describing how many "
        "patterns the user has accumulated.\n"
        "\n"
        "- You MAY call it ONCE before the FIRST `extract_policies`, "
        "passing the chunk's main topic as `query`. If matches come "
        "back, fold them into the FIRST `extract_policies` call as the "
        "`hint` argument so the extractor uses them as light scaffolding.\n"
        "- DO NOT call it more than once per chunk — repeated lookups "
        "won't surface anything new and waste budget.\n"
        "- If `pool_size == 0`, the user has no recorded patterns yet; "
        "skip retrieval and proceed straight to `extract_policies`.\n"
    )
    return base + extra


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
    memory_pool: PersonalMemoryPool | None = None,
    embedding_backend: EmbeddingBackend | None = None,
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

    # `search_personal_skills` is registered ONLY when there is something
    # to retrieve — that means an active pool with size > 0 AND an
    # embedding backend to embed the query. With `memory_pool=None` (the
    # cold-start / feature-disabled path), the tool is absent from the
    # catalog, the system goal omits its guidance section, and the
    # agent's behavior is bit-for-bit identical to PR-β. This is the
    # GitLab smoke regression guard from
    # `project_personalization_memory_pattern.md`.
    memory_enabled = (
        memory_pool is not None
        and memory_pool.size > 0
        and embedding_backend is not None
    )

    async def search_personal_skills_handler(args: Mapping[str, Any]) -> Any:
        # Closure over `memory_pool` + `embedding_backend` shares one
        # loaded pool across every call within a request — the
        # "session cache" requirement (round-trip / I/O minimization).
        # Defensive guards: if the model calls this tool when memory is
        # disabled (impossible by the registration guard, but cheap to
        # cover), return the empty shape rather than raising.
        if memory_pool is None or embedding_backend is None:
            return {"matches": [], "pool_size": 0}
        query = str(args.get("query", "") or "").strip()
        if not query:
            return {"matches": [], "pool_size": memory_pool.size}
        # `k` defaults to 3 — same as the system prompt advertises.
        # Coerce defensively: Gemma 4 occasionally emits "k": "3"
        # (string) instead of an int.
        k_raw = args.get("k", 3)
        try:
            k = int(k_raw)
        except (TypeError, ValueError):
            k = 3
        if k <= 0:
            return {"matches": [], "pool_size": memory_pool.size}
        vectors = await embedding_backend.embed([query])
        if not vectors:
            return {"matches": [], "pool_size": memory_pool.size}
        hits = memory_pool.search(vectors[0], k=k)
        return {
            "matches": [
                {
                    "condition": h.condition,
                    "action": h.action,
                    "rationale": "from your past edits",
                }
                for h in hits
            ],
            "pool_size": memory_pool.size,
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
    search_tool = _make_tool(
        name="search_personal_skills",
        description=(
            "Look up the user's past hand-edited policies for ones "
            "similar to a query. Returns up to `k` matches plus a "
            "`pool_size` field showing how many patterns the user has "
            "on file. Useful BEFORE the first `extract_policies` call "
            "to surface scaffolding the user has already approved; "
            "fold any matches into that call's `hint`."
        ),
        parameters={
            "query": (
                "string — the chunk's main topic, e.g., 'refund "
                "approval threshold'. Phrase it as a noun phrase rather "
                "than a sentence."
            ),
            "k": (
                "integer (default 3) — maximum matches to return. The "
                "default is plenty; raising it adds prompt-budget noise."
            ),
        },
        handler=search_personal_skills_handler,
    )

    tools = [extract_tool, evaluate_tool]
    if memory_enabled:
        # Insert search ahead of extract so the catalog rendering puts
        # it first — the agent reads tools top-down and we want the
        # retrieval option in front when relevant.
        tools = [search_tool, extract_tool, evaluate_tool]

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
            system_goal=_system_goal(
                domain, max_iter, memory_enabled=memory_enabled
            ),
            user_request=user_request,
            tools=tools,
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
