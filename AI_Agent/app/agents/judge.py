"""LLM judge for the policy_extract reflective agent (PLAN_13 §4.3 #4).

Runs only when the deterministic rules in `eval.py` decided `converge`
— the rules are cheap and we don't want to burn a Modal call when one
of them already reached a confident retry verdict. Self_eval calls this
on the converge path to ask "did the previous extraction miss anything
the chunk explicitly states?".

The judge stays narrow on purpose:
  - It NEVER invents policies that aren't in the chunk (jail bait for
    false positives that bloat the wizard with garbage).
  - It NEVER restates existing candidates (we don't want it telling
    reflect to "make sure to extract the refund policy" when the
    extractor already did).
  - It outputs a small JSON object — empty `missed` list is the
    expected default.

The output is a list of natural-language concern strings; the graph
hands them straight to reflect via `EvalReport.coverage_concerns`.
"""
from __future__ import annotations

from app.agents.tracing import traceable
from app.backends.protocols import LLMBackend
from app.models.skills import SkillDraft
from app.services._llm_json import JsonExtractError, extract_json_object

# Short critique only — the judge is supposed to bullet-list missed
# policies, not produce reasoning. Larger budgets just feed reasoning
# tokens that get discarded.
JUDGE_MAX_TOKENS = 256

# Hard cap on the number of concerns we hand back to reflect. A judge
# that returns 30 bullets is almost certainly hallucinating; capping
# protects the next iteration's prompt from getting flooded.
_MAX_CONCERNS = 5


class JudgeParseError(ValueError):
    """Judge response could not be coerced into the expected schema."""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


def _system_prompt() -> str:
    return (
        "You are a critic for a policy-extraction step. The user "
        "uploaded a team document; the next message contains ONE chunk "
        "of that document and the candidates an earlier extraction "
        "step already produced. Your only job is to flag policies "
        "that are stated IN the chunk but missing from the candidate "
        "list.\n\n"
        "## How to find missed policies\n"
        "\n"
        "1. Read the chunk and identify EVERY distinct condition+action "
        "policy it states. Chunks often contain multiple — do not stop "
        "at the first one you recognize.\n"
        "2. For each policy you found in step 1, check whether the "
        "candidate list already covers it. Match by intent, not exact "
        "wording — a candidate that captures the same condition+action "
        "is a match.\n"
        "3. Anything you found in step 1 that is NOT matched in step 2 "
        "is a `missed` bullet.\n"
        "\n"
        "Return `{\"missed\": []}` when the candidate list already "
        "covers every policy in the chunk — but only after running "
        "steps 1-3, not as a default.\n\n"
        "## Output\n"
        "Output ONLY a single JSON object. No prose, no markdown.\n"
        '  {"missed": ["<short bullet>", ...]}\n\n'
        "## Rules\n"
        "- A 'missed' bullet must point to a condition+action pair the "
        "chunk EXPLICITLY states — \"when X happens, do Y\". A category "
        "label, a topic name, or a list entry without an associated "
        "action is NOT a policy.\n"
        "- If the extractor returned ZERO candidates, treat that as a "
        "strong signal the chunk has no policies. Only override it "
        "when the chunk uses imperative policy language (must, shall, "
        "require, approve, escalate, prohibit) tied to a clear action.\n"
        "- Each bullet stays under 80 characters and quotes concrete "
        "language from the chunk so reflect can target it.\n"
        "- Do NOT invent policies that aren't in the chunk.\n"
        "- Do NOT restate candidates that the extractor already "
        "produced — we already have those.\n"
        "- Org structure, history, contact directories, glossary "
        "entries, taxonomies, routing tables, and aspirational "
        "language are NOT policies."
    )


def _user_message(chunk: str, drafts: list[SkillDraft]) -> str:
    if not drafts:
        candidate_block = "(no candidates produced)"
    else:
        candidate_block = "\n".join(
            f"- {d.name}: when `{d.condition}`, `{d.action}`" for d in drafts
        )
    return (
        f"chunk:\n```\n{chunk}\n```\n\n"
        f"existing candidates:\n{candidate_block}"
    )


def _parse_response(raw: str) -> list[str]:
    try:
        body = extract_json_object(raw)
    except JsonExtractError as exc:
        raise JudgeParseError(str(exc), raw=raw) from exc

    missed = body.get("missed")
    if missed is None:
        raise JudgeParseError(
            "judge response missing top-level `missed` key", raw=raw
        )
    if not isinstance(missed, list):
        raise JudgeParseError(
            "`missed` must be a list", raw=raw
        )

    out: list[str] = []
    for entry in missed:
        if not isinstance(entry, str):
            # Skip non-string entries silently rather than failing the
            # whole judge call — a well-formed model occasionally wraps
            # a string in a one-key dict, and we'd rather degrade than
            # error out reflect.
            continue
        cleaned = entry.strip()
        if cleaned:
            out.append(cleaned)
        if len(out) >= _MAX_CONCERNS:
            break
    return out


@traceable(name="judge_extraction", run_type="llm")
async def judge_extraction(
    backend: LLMBackend,
    chunk: str,
    drafts: list[SkillDraft],
) -> list[str]:
    """Ask the judge to flag missed policies in `chunk`.

    Returns an empty list when the judge confirms nothing is missing
    (the common case after deterministic rules already cleared the
    extraction). The list is bounded by `_MAX_CONCERNS`.

    Raises `JudgeParseError` when the response can't be parsed — the
    caller (self_eval node) catches this and treats it as "judge had
    nothing useful to say", preserving the deterministic decision so
    one bad model response can't blow up the whole agent run.
    """
    raw = await backend.complete(
        system=_system_prompt(),
        user_message=_user_message(chunk, drafts),
        max_tokens=JUDGE_MAX_TOKENS,
    )
    return _parse_response(raw)
