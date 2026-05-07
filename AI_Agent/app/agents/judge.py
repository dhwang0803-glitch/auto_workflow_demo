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
        "## Output\n"
        "Output ONLY a single JSON object. No prose, no markdown.\n"
        '  {"missed": ["<short bullet>", ...]}\n\n'
        "## Rules\n"
        "- A 'missed' bullet must point to a condition+action policy "
        "that appears in the chunk text. If no policy is missing, "
        "return `{\"missed\": []}` — that is the most common correct "
        "answer.\n"
        "- Each bullet stays under 80 characters and quotes concrete "
        "language from the chunk so reflect can target it.\n"
        "- Do NOT invent policies that aren't in the chunk.\n"
        "- Do NOT restate candidates that the extractor already "
        "produced — we already have those.\n"
        "- Org structure, history, contact directories, glossary "
        "entries, and aspirational language are NOT policies."
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
