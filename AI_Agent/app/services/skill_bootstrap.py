"""gap_analyze + answer_to_skill LLM services (PLAN_12 W2-4).

Two single-shot LLM calls in the Persona A wizard pipeline:

- analyze_gaps(domain, extracted_skills) — once per interview, after domain
  classification (W2-2). Compares the team's declared skills against the
  domain seed and emits the missing policies + 1-2 wizard questions per
  missing policy. For "other" domain or empty seed, short-circuits without
  hitting the LLM.

- answer_to_skill(domain, policy_id, question, answer) — once per wizard
  answer. Compiles the user's free-text answer into a structured Skill
  draft mirroring the `skills` DB table shape, with a needs_clarification
  flag per ADR-022 §8.2.

Both prompts are built dynamically from data/policies/{domain}.yaml so the
seed YAMLs remain the single source of truth. The seed loader is shared
between both functions and cached at module load.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml

from app.backends.protocols import LLMBackend
from app.models.domain import DomainCategory
from app.models.skills import (
    ExtractedSkill,
    GapAnalysis,
    PolicyGap,
    SkillDraft,
    WizardQuestion,
)
from app.services._llm_json import JsonExtractError, extract_json_object

POLICIES_DIR = Path(__file__).parent.parent.parent / "data" / "policies"

# Per ADR-022 §6 multi-turn budget: gap_analyze is the heavier of the two
# (full seed + extracted skills in prompt) so it gets the bigger output
# allowance. answer_to_skill returns a tight schema so 512 is plenty.
GAP_ANALYZE_MAX_TOKENS = 1024
ANSWER_TO_SKILL_MAX_TOKENS = 512


class SkillBootstrapParseError(ValueError):
    """The LLM response could not be parsed into the expected schema."""


# --- seed loading ---------------------------------------------------------


@lru_cache(maxsize=1)
def _seeds_by_domain() -> dict[str, list[dict]]:
    """Return {domain: policies_list} for every seed YAML.

    "other" is never a key here — there is no seed file for it, and the
    services short-circuit before reaching the loader.
    """
    out: dict[str, list[dict]] = {}
    for path in sorted(POLICIES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        out[doc["domain"]] = doc["policies"]
    return out


def _seed_policies(domain: str) -> list[dict]:
    return _seeds_by_domain().get(domain, [])


def _find_policy(domain: str, policy_id: str) -> dict | None:
    for p in _seed_policies(domain):
        if p["id"] == policy_id:
            return p
    return None


# --- gap_analyze ----------------------------------------------------------


def _gap_analyze_system_prompt(domain: str) -> str:
    seed = _seed_policies(domain)
    lines = [
        "You are the gap analyzer for a workflow-automation product's "
        "skill-bootstrap flow. The user has classified their domain as "
        f"`{domain}`. Below are the typical policies we expect a {domain} "
        "team to have. The user's already-declared skills will arrive in "
        "the next message as a JSON array (which may be empty).",
        "",
        f"## Typical {domain} policies",
        "",
    ]
    for p in seed:
        lines.append(f"### {p['id']} — {p['name']}")
        lines.append(f"- condition: {p['condition'].strip()}")
        lines.append(f"- action: {p['action'].strip()}")
        lines.append(f"- parameters: {', '.join(p['parameters'])}")
        lines.append("")
    lines.extend(
        [
            "## Task",
            "",
            "For each typical policy, decide whether the user's declared "
            "skills already cover it. Coverage requires the same condition "
            "AND the same action — not just the same topic. For each "
            "policy NOT covered, generate 1-2 short wizard questions that "
            "would elicit the user's specific values for that policy's "
            "parameters.",
            "",
            "Output ONLY a single JSON object. No prose, no markdown fences.",
            "Schema:",
            '  {"missing": [',
            '    {"policy_id": "<exact id from list above>",',
            '     "questions": [',
            '       {"text": "...", "parameter": "<one of the policy\'s parameters>"}',
            "     ]}",
            "  ]}",
            "",
            "Rules:",
            "- `policy_id` MUST be an EXACT id from the typical policy list "
            "above. Do not invent ids or paraphrase them.",
            "- Each question targets one parameter. Combining two parameters "
            "in one question is OK only when they read naturally together.",
            "- Phrase questions in plain language. Do not use parameter "
            "names verbatim (e.g. ask 'What dollar amount triggers a "
            "manager approval for refunds?', not 'What is your "
            "REFUND_AUTO_APPROVE_LIMIT?').",
            "- Cap to 2 questions per missing policy.",
            "- Skip policies fully covered by declared skills (do not "
            "include them in `missing`).",
        ]
    )
    return "\n".join(lines)


def _parse_gap_response(raw: str, domain: str) -> GapAnalysis:
    try:
        body = extract_json_object(raw)
    except JsonExtractError as exc:
        raise SkillBootstrapParseError(str(exc)) from exc

    missing_raw = body.get("missing")
    if not isinstance(missing_raw, list):
        raise SkillBootstrapParseError(
            f"`missing` must be a list, got {type(missing_raw).__name__}"
        )

    seed_index = {p["id"]: p for p in _seed_policies(domain)}
    enriched: list[PolicyGap] = []

    for entry in missing_raw:
        if not isinstance(entry, dict):
            raise SkillBootstrapParseError(
                f"missing entry must be object, got {type(entry).__name__}"
            )
        pid = entry.get("policy_id")
        if pid not in seed_index:
            raise SkillBootstrapParseError(
                f"policy_id {pid!r} not in seed for domain {domain!r}"
            )

        questions_raw = entry.get("questions") or []
        if not isinstance(questions_raw, list):
            raise SkillBootstrapParseError(
                f"`questions` for {pid} must be a list"
            )
        seed_params = set(seed_index[pid]["parameters"])
        questions: list[WizardQuestion] = []
        for q in questions_raw:
            if not isinstance(q, dict) or "text" not in q:
                raise SkillBootstrapParseError(
                    f"question for {pid} missing `text`: {q!r}"
                )
            param = q.get("parameter")
            # Trust the LLM on parameter names but null out anything not in
            # the seed — that prevents downstream consumers from acting on
            # phantom parameter names.
            if param is not None and param not in seed_params:
                param = None
            questions.append(
                WizardQuestion(text=str(q["text"]).strip(), parameter=param)
            )

        enriched.append(
            PolicyGap(
                policy_id=pid,
                policy_name=seed_index[pid]["name"],
                questions=questions,
            )
        )

    return GapAnalysis(missing=enriched)


async def analyze_gaps(
    backend: LLMBackend,
    domain: DomainCategory,
    extracted_skills: list[ExtractedSkill],
) -> GapAnalysis:
    if not _seed_policies(domain):
        # "other" or any future un-seeded domain. Wizard handles the empty
        # case by falling back to a free-form skill capture flow.
        return GapAnalysis(missing=[])

    user_payload = json.dumps(
        [s.model_dump() for s in extracted_skills],
        ensure_ascii=False,
    )
    raw = await backend.complete(
        system=_gap_analyze_system_prompt(domain),
        user_message=user_payload,
        max_tokens=GAP_ANALYZE_MAX_TOKENS,
    )
    return _parse_gap_response(raw, domain)


# --- answer_to_skill ------------------------------------------------------


def _answer_to_skill_system_prompt(domain: str, seed_policy: dict) -> str:
    return (
        "You are the answer-to-skill compiler for a workflow-automation "
        "product's skill-bootstrap flow. The user's domain is "
        f"`{domain}`. You will receive a wizard question and the user's "
        "answer in the next message. Your job is to compile the answer "
        "into an executable Skill record.\n\n"
        f"## Source policy template ({seed_policy['id']})\n"
        f"- name: {seed_policy['name']}\n"
        f"- condition (template): {seed_policy['condition'].strip()}\n"
        f"- action (template): {seed_policy['action'].strip()}\n"
        f"- rationale: {seed_policy['rationale'].strip()}\n"
        f"- parameters: {', '.join(seed_policy['parameters'])}\n\n"
        "## Output\n\n"
        "Output ONLY a single JSON object. No prose, no markdown fences. "
        "Schema:\n"
        "  {\n"
        '    "name": "<short imperative name>",\n'
        '    "description": "<one-sentence summary the user will recognize>",\n'
        '    "condition": "<concrete trigger with the user\'s specific '
        'values substituted>",\n'
        '    "action": "<concrete action with the user\'s specific values '
        'substituted>",\n'
        '    "rationale": "<one sentence on why this matters>",\n'
        '    "needs_clarification": <true if answer is ambiguous, '
        "contradictory, or non-actionable>,\n"
        '    "clarification_hint": "<concrete follow-up question if '
        'needs_clarification, else empty>"\n'
        "  }\n\n"
        "## Rules\n"
        "- Embed the user's exact values into condition/action. E.g. answer "
        '"$500" → condition contains "$500", not the parameter name.\n'
        "- If the user answered \"I don't know\" / \"it depends\" without "
        "specifics, set needs_clarification=true and write a concrete "
        "clarification_hint.\n"
        "- If the user gave specifics that conflict with the source policy "
        "template (e.g. opted out entirely), still produce a valid skill "
        "reflecting their actual answer; set needs_clarification=true only "
        "when ambiguity blocks execution.\n"
        "- Reuse the source rationale verbatim if the user gave no "
        "team-specific reason.\n"
        "- Keep all fields concise; users will review every skill."
    )


def _parse_skill_response(raw: str) -> SkillDraft:
    try:
        body = extract_json_object(raw)
    except JsonExtractError as exc:
        raise SkillBootstrapParseError(str(exc)) from exc

    required = ("name", "condition", "action")
    missing = [k for k in required if not body.get(k)]
    if missing:
        raise SkillBootstrapParseError(f"missing required fields: {missing}")

    needs = bool(body.get("needs_clarification", False))
    hint = body.get("clarification_hint", "") or ""
    if not isinstance(hint, str):
        hint = str(hint)
    if needs and not hint.strip():
        raise SkillBootstrapParseError(
            "needs_clarification=true but clarification_hint is empty"
        )

    return SkillDraft(
        name=str(body["name"]).strip(),
        description=str(body.get("description", "")).strip(),
        condition=str(body["condition"]).strip(),
        action=str(body["action"]).strip(),
        rationale=str(body.get("rationale", "")).strip(),
        needs_clarification=needs,
        clarification_hint=hint.strip(),
    )


async def answer_to_skill(
    backend: LLMBackend,
    domain: DomainCategory,
    policy_id: str,
    question: str,
    answer: str,
) -> SkillDraft:
    seed_policy = _find_policy(domain, policy_id)
    if seed_policy is None:
        # Surface as ValueError; the endpoint maps it to 422 (caller bug,
        # not LLM bug — they passed an unknown policy_id).
        raise ValueError(
            f"unknown policy_id {policy_id!r} for domain {domain!r}"
        )

    raw = await backend.complete(
        system=_answer_to_skill_system_prompt(domain, seed_policy),
        user_message=f"Question: {question.strip()}\nAnswer: {answer.strip()}",
        max_tokens=ANSWER_TO_SKILL_MAX_TOKENS,
    )
    return _parse_skill_response(raw)
