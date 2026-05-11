"""ReAct agent driver for policy_extract (PLAN_15 / ADR-024 / PR-β + γ + δ + ε).

Replaces the langgraph StateGraph that lived here through PR-A/B/C/D.
The new shape (PR-β with optional PR-γ + PR-δ retrieval prepended,
plus PR-ε deterministic self-check helpers always-registered):

    [search_industry_baselines(query)]  ← PR-δ, registered only when
            │                              the chunk's domain has a
            │                              seeded YAML and an
            │                              embedding backend is wired
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
    [validate_skill_schema(draft)]  ← PR-ε, deterministic, optional
    [cite_source_url(draft)]        ← PR-ε, deterministic, optional
            │
            ▼
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
  * `search_industry_baselines(query, k)` — optional, registered only
    when a populated `IndustryBaselinePool` and an `EmbeddingBackend`
    reach this driver. The cold-start guarantee is the same shape as
    PR-γ: empty pool / `domain="other"` / no embedder → tool absent
    from the catalog, system goal omits the section, agent behavior
    is bit-identical to PR-β/γ baseline (PLAN_13 §11.6 regression
    guard).
  * `validate_skill_schema(draft)` — PR-ε. Pure-Python schema sanity
    check on ONE draft. Always registered (no external dependency,
    no embedding, no I/O). Returns `{valid, issues}`. The model MAY
    call it before `<finish>` on a draft it suspects is malformed.
  * `cite_source_url(draft)` — PR-ε. Looks up the chunk's domain seed
    YAML for a name-overlap match against the draft and returns
    `{sources, source_kind, policy_id?}`. Always registered; the
    deterministic seed-match path is independent of the embedding-
    backed `search_industry_baselines` tool, so the cite tool works
    even on cold-start (no embedder), at the cost of a coarser
    matching heuristic (token overlap on `name`). Empty result is
    normal — `domain="other"` / unseeded domain / no name match.

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
import re
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
from app.services.industry_baselines import IndustryBaselinePool
from app.services.personal_memory import PersonalMemoryPool
from app.services.policy_extract import (
    PolicyExtractParseError,
    extract_policies as extract_policies_service,
)
from app.services.skill_bootstrap import _seed_policies

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
    domain: DomainCategory,
    max_iter: int,
    *,
    memory_enabled: bool,
    baseline_enabled: bool,
) -> str:
    """Tell the agent its job + guardrails. Tone is medium-prescriptive:
    we describe the typical flow so the model has an easy default, but
    the actual decisions ("retry?", "what hint?", "when done?") still
    belong to the LLM. ADR-024 §6 picks this tone over either extreme.

    `max_iter` is named explicitly so the model can budget its passes —
    if we omit it the loop's hard cap still fires but the model wastes
    the late iterations re-extracting fruitlessly.

    `memory_enabled` / `baseline_enabled` each gate one optional
    retrieval section. When False (cold-start, anonymous request, or
    feature disabled) the corresponding guidance is omitted from the
    goal so the model is not tempted to call a tool that isn't
    registered. The "ALWAYS start with extract_policies" rule in the
    base text holds — retrieval is described as a step that happens
    BEFORE that first extract, never as a substitute for it.

    The PR-ε self-check section is ALWAYS appended — `validate_skill_schema`
    and `cite_source_url` are deterministic and unconditionally registered.
    Wording is MAY-level (per PLAN_13 §11.3 conservative posture) so the
    agent does not insert a mandatory turn before every `<finish>`; the
    GitLab smoke regression guard checks that delta=+3 holds across PR-δ
    → PR-ε.
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

    extras = ""
    if memory_enabled:
        # Memory tool is registered — teach the model when (and when NOT)
        # to use it. The "before extract_policies" placement matters: if
        # the model interleaves it after the first extract, the matches
        # cannot influence iter 1, defeating the point. The "ONCE per
        # chunk" cap keeps the LLM cost bounded — there is no scenario
        # where calling search a second time helps.
        extras += (
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
    if baseline_enabled:
        # Industry-baseline tool surfaces vetted domain policies (the
        # same `data/policies/{domain}.yaml` that drives the wizard's
        # gap_analyze short-circuit). Wording is parallel to the
        # personal-memory section so the model treats the two as
        # complementary retrieval inputs, not alternates: personal
        # patterns win precedence (they reflect THIS user's intent),
        # baselines fill the gap when there is no personal pattern.
        extras += (
            "\n## Optional retrieval (industry-standard baselines)\n"
            "\n"
            "You also have `search_industry_baselines(query, k=3)` "
            "available. It looks up vetted domain-standard policies "
            "for the chunk's domain and returns matches plus a "
            "`pool_size` field. Each match carries `policy_id`, "
            "`name`, the `condition`/`action` text, and any source "
            "citations the catalog has on file.\n"
            "\n"
            "- You MAY call it ONCE before the FIRST `extract_policies`, "
            "passing the chunk's main topic as `query`. Use the matches "
            "as a sanity-check for what a typical team in this domain "
            "would have a policy about; fold a one-line summary into "
            "the FIRST `extract_policies` call's `hint` argument so the "
            "extractor sees the grounding.\n"
            "- DO NOT call it more than once per chunk.\n"
            "- DO NOT copy a baseline's text verbatim into the final "
            "drafts — the chunk is the source of truth for THIS team's "
            "policy. Baselines are only a pattern hint; the extractor "
            "must still pull the team-specific condition+action from "
            "the chunk itself.\n"
        )

    # PR-ε self-check section is unconditional. Earlier drafts of this
    # section used MAY/SHOULD-NOT prescriptive wording plus an explicit
    # "skip on empty drafts" line and saw a -2 cand regression on the
    # GitLab smoke (chunks 11/16 converged at iter 1 instead of retrying).
    # The terse phrasing below removes all references to `<finish>`,
    # "empty", and "skip" — words that biased the agent toward early
    # finish on borderline chunks. Tools are still always-registered;
    # the prompt simply names them without prescribing when to call.
    extras += (
        "\n## Optional helpers (read-only)\n"
        "\n"
        "`validate_skill_schema(draft)` and `cite_source_url(draft)` "
        "are deterministic helpers — a schema check and a citation "
        "lookup respectively. Use them at your discretion. Neither "
        "modifies the drafts you submit.\n"
    )
    return base + extras


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
    baseline_pool: IndustryBaselinePool | None = None,
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

    # `search_personal_skills` and `search_industry_baselines` are each
    # registered ONLY when there is something to retrieve — that means
    # an active pool with size > 0 AND an embedding backend to embed
    # the query. With either pool=None (cold-start / feature-disabled
    # path) the corresponding tool is absent from the catalog, the
    # system goal omits its guidance section, and the agent's behavior
    # is bit-for-bit identical to the previous PR's baseline. This is
    # the GitLab smoke regression guard from
    # `project_personalization_memory_pattern.md` (PR-γ) extended to
    # PR-δ.
    memory_enabled = (
        memory_pool is not None
        and memory_pool.size > 0
        and embedding_backend is not None
    )
    baseline_enabled = (
        baseline_pool is not None
        and baseline_pool.size > 0
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

    async def search_industry_baselines_handler(args: Mapping[str, Any]) -> Any:
        # Mirror of the personal-skills handler shape: same closure
        # pattern (one pool loaded per request), same defensive guards
        # (return empty shape if the registration check is bypassed),
        # same `k` coercion (Gemma 4 stringly-typed integer quirk).
        # The return shape adds `policy_id` and `sources` because
        # PLAN_13 §11.3 promised those fields specifically — they are
        # the grounding hint the model needs to differentiate vetted
        # standards from the user's chunk text.
        if baseline_pool is None or embedding_backend is None:
            return {"matches": [], "pool_size": 0}
        query = str(args.get("query", "") or "").strip()
        if not query:
            return {"matches": [], "pool_size": baseline_pool.size}
        k_raw = args.get("k", 3)
        try:
            k = int(k_raw)
        except (TypeError, ValueError):
            k = 3
        if k <= 0:
            return {"matches": [], "pool_size": baseline_pool.size}
        vectors = await embedding_backend.embed([query])
        if not vectors:
            return {"matches": [], "pool_size": baseline_pool.size}
        hits = baseline_pool.search(vectors[0], k=k)
        return {
            "matches": [
                {
                    "policy_id": h.policy_id,
                    "name": h.name,
                    "condition": h.condition,
                    "action": h.action,
                    "sources": list(h.sources),
                    "source_kind": h.source_kind,
                }
                for h in hits
            ],
            "pool_size": baseline_pool.size,
        }

    async def validate_skill_schema_handler(args: Mapping[str, Any]) -> Any:
        # Pure-Python schema sanity check. We deliberately do NOT route
        # through `SkillDraft(**draft)` — Pydantic ValidationError
        # message strings are noisy ("Field required", with full pydantic
        # location tuples) and the agent's prompt budget is better spent
        # on terse human-grade issue text. The fields we check mirror
        # `SkillDraft`'s constraints in `app/models/skills.py`: `name`
        # 1-255 chars, `condition` and `action` non-empty after strip.
        # Plus a few "obvious malformation" heuristics that Pydantic
        # cannot express (placeholder markers, whitespace-only fields).
        draft = args.get("draft")
        if not isinstance(draft, dict):
            return {
                "valid": False,
                "issues": ["`draft` must be an object (dict)"],
            }

        issues: list[str] = []
        name = str(draft.get("name", "") or "").strip()
        condition = str(draft.get("condition", "") or "").strip()
        action = str(draft.get("action", "") or "").strip()

        if not name:
            issues.append("`name` is required and must be non-empty")
        elif len(name) > 255:
            issues.append("`name` exceeds 255 characters")

        if not condition:
            issues.append(
                "`condition` is required and must be non-empty after "
                "stripping whitespace"
            )
        if not action:
            issues.append(
                "`action` is required and must be non-empty after "
                "stripping whitespace"
            )

        # Placeholder-marker heuristic: catch the LLM occasionally
        # leaving "...", "TODO", or "XXX" in a draft when it ran out of
        # confidence on a field. These tokens never appear in real
        # policies, so a hit is always a defect signal.
        for field, value in (
            ("name", name),
            ("condition", condition),
            ("action", action),
        ):
            for marker in ("...", "TODO", "XXX"):
                if marker in value:
                    issues.append(
                        f"`{field}` contains placeholder marker "
                        f"'{marker}' — replace with concrete text"
                    )
                    break

        return {"valid": len(issues) == 0, "issues": issues}

    async def cite_source_url_handler(args: Mapping[str, Any]) -> Any:
        # Deterministic seed-catalog lookup — independent of the
        # embedding-backed `search_industry_baselines` tool. We match
        # by token overlap on the draft's `name` against the bundled
        # `data/policies/{domain}.yaml` (cached by skill_bootstrap's
        # lru_cache, so this is O(N seeds) per call with no I/O after
        # the first). Token overlap is intentionally coarse: the
        # citation surface only matters when there is a clear lexical
        # match (e.g. "Refund threshold" → ecommerce.refund); ambiguous
        # cases return empty rather than risk a misattribution.
        draft = args.get("draft")
        if not isinstance(draft, dict):
            return {
                "sources": [],
                "source_kind": "synthesized",
            }
        name = str(draft.get("name", "") or "").strip().lower()
        if not name:
            return {
                "sources": [],
                "source_kind": "synthesized",
            }

        try:
            policies = _seed_policies(domain)
        except Exception:  # pragma: no cover — defensive
            policies = []
        if not policies:
            return {
                "sources": [],
                "source_kind": "synthesized",
            }

        name_tokens = {
            t for t in re.findall(r"\w+", name) if len(t) > 2
        }
        if not name_tokens:
            return {
                "sources": [],
                "source_kind": "synthesized",
            }

        best_score = 0
        best: dict[str, Any] | None = None
        for p in policies:
            seed_name = str(p.get("name", "") or "").lower()
            seed_tokens = {
                t for t in re.findall(r"\w+", seed_name) if len(t) > 2
            }
            score = len(name_tokens & seed_tokens)
            if score > best_score:
                best_score = score
                best = p

        # Require at least 1 token overlap above the stop-word floor.
        # 0 overlap = no match (rather than picking the first policy by
        # accident). The seed catalog has 8-15 policies per domain so
        # this threshold is safe — false positives at 1 overlap would
        # only happen on truly tangential names.
        if best is None or best_score == 0:
            return {
                "sources": [],
                "source_kind": "synthesized",
            }

        sources: list[dict[str, str]] = []
        for s in best.get("sources", []) or []:
            if isinstance(s, dict) and "title" in s and "url" in s:
                sources.append(
                    {"title": str(s["title"]), "url": str(s["url"])}
                )

        return {
            "policy_id": best.get("id"),
            "sources": sources,
            "source_kind": str(best.get("source_kind") or "synthesized"),
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
    baseline_tool = _make_tool(
        name="search_industry_baselines",
        description=(
            "Look up vetted industry-standard policies for the chunk's "
            "domain. Returns up to `k` matches plus a `pool_size` "
            "field showing how many baselines the catalog holds. Each "
            "match carries `policy_id`, `name`, `condition`/`action` "
            "text, and any source citations. Useful BEFORE the first "
            "`extract_policies` call as a sanity-check for what a "
            "typical team in this domain would have a policy about — "
            "but the chunk is still the source of truth; do not copy "
            "baseline text verbatim into final drafts."
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
        handler=search_industry_baselines_handler,
    )
    validate_tool = _make_tool(
        name="validate_skill_schema",
        description=(
            "Run a deterministic schema check on ONE skill draft. "
            "Returns `{valid, issues}` — empty issues means the draft "
            "passes. Pure-Python, no I/O."
        ),
        parameters={
            "draft": (
                "object — one skill draft (dict with `name`, "
                "`condition`, `action`, plus optional `description` "
                "and `rationale`)."
            ),
        },
        handler=validate_skill_schema_handler,
    )
    cite_tool = _make_tool(
        name="cite_source_url",
        description=(
            "Look up the chunk's domain seed catalog for a policy "
            "whose name overlaps with the draft's `name`. Returns "
            "`{sources, source_kind, policy_id?}` on a match, or "
            "`{sources: [], source_kind: \"synthesized\"}` otherwise."
        ),
        parameters={
            "draft": (
                "object — one skill draft. Only `name` is consulted "
                "(token-overlap match against seed catalog names)."
            ),
        },
        handler=cite_source_url_handler,
    )

    # Catalog ordering: retrieval tools FIRST when enabled, so the
    # agent's top-down read of the tool list surfaces them in front of
    # `extract_policies`. Personal skills outrank baselines because
    # the user's own past edits are a stronger signal of THIS team's
    # intent than a generic domain standard. `extract_policies` and
    # `evaluate_coverage` anchor the working middle. PR-ε self-check
    # tools (`validate_skill_schema`, `cite_source_url`) anchor the
    # tail — they are post-extract sanity probes and the tail position
    # in the catalog reinforces "use these LAST" without us having to
    # spell that out in the tool descriptions.
    tools: list[Any] = []
    if memory_enabled:
        tools.append(search_tool)
    if baseline_enabled:
        tools.append(baseline_tool)
    tools.extend([extract_tool, evaluate_tool, validate_tool, cite_tool])

    user_request = f"chunk:\n```\n{chunk}\n```"

    # Loop budget: each completed reflective iteration is two tool calls
    # (extract + evaluate). Add headroom for the agent's <finish> turn
    # plus a tolerance for one stray retry (e.g., a tool_not_found that
    # the model recovers from on the next turn) plus PR-ε self-check
    # headroom (the model MAY call validate_skill_schema and/or
    # cite_source_url before <finish>; observed cap is 2 calls regardless
    # of draft count because the prompt says "MAY", not "MUST per draft").
    # 2 * max_iter + 6 covers the worst observed paths.
    loop_budget = max(10, max_iter * 2 + 6)

    try:
        result: AgentResult = await run_agent(
            backend,
            system_goal=_system_goal(
                domain,
                max_iter,
                memory_enabled=memory_enabled,
                baseline_enabled=baseline_enabled,
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
