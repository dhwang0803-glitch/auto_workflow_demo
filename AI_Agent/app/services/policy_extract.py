"""policy_extract LLM service (PLAN_12 W3-4).

Turns one parsed document chunk (from `services.document_parser`) into a
list of zero+ structured Skill candidates. The condition+action pair is
the unit (ADR-022 §8.1) — a chunk describing org structure or contact
info should produce ZERO candidates, while a chunk like "refunds over
$500 require manager approval" should produce ONE.

The LLM may surface a candidate it considers ambiguous; per ADR-022 §8.2
those carry `needs_clarification=true` plus a `clarification_hint`. The
follow-on review UI (W3-7) decides whether to drop the candidate, ask
the team a question, or accept the LLM's best guess.

Prompt design mirrors `skill_bootstrap.answers_to_skill` so the model
sees a consistent shape across the bootstrap pipeline (interview path
and docs path emit the same SkillDraft).
"""
from __future__ import annotations

from app.agents.tracing import traceable
from app.backends.protocols import LLMBackend
from app.models.domain import DomainCategory
from app.models.skills import SkillDraft
from app.services._llm_json import JsonExtractError, extract_json_object

# Gemma 4 26B-A4B emits 1000-1700 tokens of internal reasoning before the
# visible JSON for dense reference chunks (live smoke 2026-05-05: 13/20
# chunks hit `''` empty output at 1024 because the budget ran out before
# the model committed to JSON). 4096 covers the worst observed case
# (~1700 reasoning + ~700 multi-candidate JSON) with margin. Latency
# trade-off: 20-35s/chunk vs 19s/chunk previously, but reliability
# matters more for a doc-parsing pipeline.
POLICY_EXTRACT_MAX_TOKENS = 4096


class PolicyExtractParseError(ValueError):
    """The LLM response could not be parsed into a candidate list.

    Carries the raw LLM output so callers (and the FastAPI 502 detail) can
    see what was actually emitted — diagnosing reasoning-token waste means
    looking at the bytes the model produced before the budget ran out.
    """

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


def _system_prompt(domain: DomainCategory, prompt_hint: str = "") -> str:
    # The trailing "## Bias" clause was selected by the Phase 2 sweep
    # (EXPERIMENT_reasoning_trace.md §5): on the GitLab handbook fixture
    # it lifted recall from 10 → 16 candidates without changing wall time
    # vs the neutral prompt (56s vs 64s), and without the 10x latency
    # penalty of re-enabling reasoning (634s for 14 candidates). The
    # clause is intentionally short — the Rules block above is doing the
    # schema enforcement; this clause only nudges precision↔recall.
    #
    # `prompt_hint`, when non-empty, is appended as a "## Previous pass"
    # section at the very end of the prompt (PLAN_13 §4.4). It carries
    # the reflect node's coverage_concerns from the prior iteration so
    # the model knows what the previous pass missed. Empty string means
    # this is iter 1 and the agent hasn't reflected yet — back-compat
    # with the existing single-shot `/v1/policy/extract` route.
    hint_block = (
        "\n\n## Previous pass\n"
        "An earlier extraction pass on this same chunk had these gaps. "
        "Re-read the chunk and emit candidates that address them.\n"
        f"{prompt_hint.strip()}"
        if prompt_hint.strip()
        else ""
    )

    return (
        "You are the policy extractor for a workflow-automation product's "
        "skill-bootstrap flow. The user uploaded a team document; the next "
        "message contains ONE chunk of that document. Your job is to find "
        "every operational policy in this chunk and emit a structured "
        f"Skill record for each. The team's domain is `{domain}`.\n\n"
        "## What counts as a policy\n\n"
        "A policy is a condition+action pair the team applies repeatedly:\n"
        '  - "If a refund request exceeds $500, escalate to a manager"\n'
        '  - "When a P1 ticket arrives, page the on-call engineer"\n'
        '  - "Customers in EU must be offered a 14-day return window"\n\n'
        "Org structure, history, contact directories, glossary entries, "
        "and aspirational mission statements are NOT policies. Skip them.\n\n"
        "## Output\n\n"
        "Output ONLY a single JSON object. No prose, no markdown fences.\n"
        "Schema:\n"
        "  {\n"
        '    "candidates": [\n'
        "      {\n"
        '        "name": "<short imperative name>",\n'
        '        "description": "<one-sentence summary the user will recognize>",\n'
        '        "condition": "<concrete trigger as it appears in the chunk>",\n'
        '        "action": "<concrete action as it appears in the chunk>",\n'
        '        "rationale": "<one sentence on why this matters, drawn from the chunk>",\n'
        '        "needs_clarification": <true if condition or action is vague>,\n'
        '        "clarification_hint": "<concrete follow-up question if needs_clarification, else empty>"\n'
        "      }\n"
        "      // ... 0 or more candidates\n"
        "    ]\n"
        "  }\n\n"
        "## Rules\n"
        "- Empty list `{\"candidates\": []}` is the correct answer when the "
        "chunk has no actionable policy. Do NOT invent policies to fill space.\n"
        "- Each candidate's condition and action MUST come from this chunk; "
        "do not import policies you remember from other documents or general "
        "knowledge.\n"
        "- One condition+action pair = one candidate. Split a chunk that "
        "describes two distinct policies into two candidates.\n"
        "- Vague signals like \"be careful with PII\" should produce a "
        "candidate with needs_clarification=true and a clarification_hint "
        "naming what is unclear (e.g. \"What counts as PII for your team?\").\n\n"
        "## Bias\n"
        "When in doubt, INCLUDE the candidate with needs_clarification=true "
        "rather than dropping it. The downstream review UI can prune false "
        "positives, but cannot recover policies that were never extracted. "
        "Aim for high recall over precision."
        + hint_block
    )


def _parse_response(raw: str) -> list[SkillDraft]:
    try:
        body = extract_json_object(raw)
    except JsonExtractError as exc:
        raise PolicyExtractParseError(str(exc), raw=raw) from exc

    candidates_raw = body.get("candidates")
    if candidates_raw is None:
        raise PolicyExtractParseError(
            "response missing top-level `candidates` key", raw=raw
        )
    if not isinstance(candidates_raw, list):
        raise PolicyExtractParseError(
            "`candidates` must be a list", raw=raw
        )

    drafts: list[SkillDraft] = []
    for i, entry in enumerate(candidates_raw):
        if not isinstance(entry, dict):
            raise PolicyExtractParseError(
                f"candidate #{i} is not an object: {entry!r}", raw=raw
            )
        for required in ("name", "condition", "action"):
            if not entry.get(required):
                raise PolicyExtractParseError(
                    f"candidate #{i} missing required field {required!r}",
                    raw=raw,
                )
        needs = bool(entry.get("needs_clarification", False))
        hint = str(entry.get("clarification_hint", "") or "").strip()
        if needs and not hint:
            raise PolicyExtractParseError(
                f"candidate #{i} has needs_clarification=true but empty hint",
                raw=raw,
            )
        drafts.append(
            SkillDraft(
                name=str(entry["name"]).strip(),
                description=str(entry.get("description", "")).strip(),
                condition=str(entry["condition"]).strip(),
                action=str(entry["action"]).strip(),
                rationale=str(entry.get("rationale", "")).strip(),
                needs_clarification=needs,
                clarification_hint=hint,
            )
        )
    return drafts


@traceable(name="extract_policies", run_type="llm")
async def extract_policies(
    backend: LLMBackend,
    chunk: str,
    domain: DomainCategory = "other",
    *,
    images: list[str] | None = None,
    prompt_hint: str = "",
) -> list[SkillDraft]:
    """Extract zero+ skill candidates from one document chunk.

    `images` is the Phase D entry point: a PDF page rendered to PNG via
    PyMuPDF travels alongside `chunk` (the page's text) so the model
    sees both. Until Phase D ships document_parser's image rendering,
    callers pass None and the path stays text-only.

    `prompt_hint` is the PLAN_13 reflective entry point: when non-empty
    it gets appended to the system prompt as a "## Previous pass"
    section listing the prior iteration's gaps. The single-shot route
    `/v1/policy/extract` always passes "" so its output matches the
    pre-PLAN_13 behavior byte-for-byte.
    """
    text = chunk.strip()
    if not text and not images:
        # Whitespace-only chunks are dropped upstream by document_parser,
        # but if one slips through we save the LLM round-trip. With an
        # image the chunk text may be empty by design (image-only page).
        return []

    raw = await backend.complete(
        system=_system_prompt(domain, prompt_hint=prompt_hint),
        user_message=text,
        max_tokens=POLICY_EXTRACT_MAX_TOKENS,
        images=images,
    )
    return _parse_response(raw)
