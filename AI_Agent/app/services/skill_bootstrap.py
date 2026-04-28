"""gap_analyze + answers_to_skill LLM services (PLAN_12 W2-4).

The 2026-04-28 polish redesign (memory `project_wizard_polish_abc.md`)
shifted question generation OUT of the LLM and INTO the seed YAML. The
service now does:

- analyze_gaps(domain, extracted_skills) — once per interview, after domain
  classification. Two paths:
    * extracted_skills empty (Persona A) → deterministic short-circuit:
      emit every seed policy as missing, attach seed prompts/baselines/
      sources verbatim. NO LLM call.
    * extracted_skills non-empty (Persona B) → LLM coverage check ONLY:
      decides which seed policy_ids the team's declared skills already
      cover. Service then enriches the missing list with seed prompts/
      baselines/sources (LLM never sees or generates question text).

- answers_to_skill(domain, policy_id, [(parameter_name, answer)]) — once
  per policy at interview-end. Compiles all per-parameter answers for a
  single policy into ONE structured Skill draft with values substituted
  inline. Replaces the old answer_to_skill (1 Q+A → 1 skill) which
  fragmented one policy's parameters across multiple skills.

- answer_to_skill(domain, policy_id, question, answer) — legacy single-
  question shim. Routes through `answers_to_skill` with a one-element
  batch so the live LLM prompt is identical. Kept for the W2-7 API_Server
  contract until PR #143 cuts over.

Both prompts read the seed YAML once at module load. The seed loader is
shared across the service, the stub backend, and tests.
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
    ParameterAnswer,
    PolicyGap,
    PolicySource,
    SkillDraft,
    SourceKind,
    WizardQuestion,
)
from app.services._llm_json import JsonExtractError, extract_json_object

POLICIES_DIR = Path(__file__).parent.parent.parent / "data" / "policies"

# Per ADR-022 §6 multi-turn budget. answers_to_skill compiles a whole
# policy's worth of parameters into one draft, so it gets a wider window
# than the legacy single-shot (which had 512). gap_analyze stays at 1024
# since the deterministic path doesn't hit the LLM at all.
GAP_ANALYZE_MAX_TOKENS = 1024
ANSWERS_TO_SKILL_MAX_TOKENS = 768


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


def _param_names(seed_policy: dict) -> list[str]:
    """Extract parameter names from the new object-list schema."""
    return [p["name"] for p in seed_policy.get("parameters", [])]


def _find_parameter(seed_policy: dict, name: str) -> dict | None:
    for p in seed_policy.get("parameters", []):
        if p["name"] == name:
            return p
    return None


def _build_policy_gap(seed_policy: dict) -> PolicyGap:
    """Turn a seed policy into a PolicyGap with all wizard fields filled in.

    Used by both deterministic short-circuit (extracted_skills empty) and
    LLM-judged path (after LLM tells us which policy_ids are missing). The
    wizard fields (`prompt`, `default_baseline`, `baseline_source`,
    `sources`, `source_kind`) come straight from the seed YAML — the LLM
    never generates them.
    """
    questions = [
        WizardQuestion(
            text=p["prompt"],
            parameter=p["name"],
            default_baseline=p.get("default_baseline", "") or "",
            baseline_source=p.get("baseline_source", "") or "",
            help_text=p.get("help_text", "") or "",
            example_answer=p.get("example_answer", "") or "",
        )
        for p in seed_policy.get("parameters", [])
    ]
    sources = [
        PolicySource(title=s["title"], url=s["url"])
        for s in seed_policy.get("sources", []) or []
    ]
    kind: SourceKind = seed_policy.get("source_kind", "synthesized")
    return PolicyGap(
        policy_id=seed_policy["id"],
        policy_name=seed_policy["name"],
        parameters=questions,
        sources=sources,
        source_kind=kind,
        # Backward-compat alias — same payload, drop after PR #143.
        questions=questions,
    )


# --- gap_analyze ----------------------------------------------------------


def _gap_analyze_system_prompt(domain: str) -> str:
    """Coverage-only prompt. LLM picks which policy_ids are missing; the
    service attaches seed prompts/baselines/sources after parsing.
    """
    seed = _seed_policies(domain)
    lines = [
        "You are the gap analyzer for a workflow-automation product's "
        "skill-bootstrap flow. The user has classified their domain as "
        f"`{domain}`. Below are the typical policies we expect a {domain} "
        "team to have. The user's already-declared skills will arrive in "
        "the next message as a JSON array.",
        "",
        f"## Typical {domain} policies",
        "",
    ]
    for p in seed:
        lines.append(f"### {p['id']} — {p['name']}")
        lines.append(f"- condition: {p['condition'].strip()}")
        lines.append(f"- action: {p['action'].strip()}")
        lines.append(f"- parameters: {', '.join(_param_names(p))}")
        lines.append("")
    lines.extend(
        [
            "## Task",
            "",
            "For each typical policy, decide whether the user's declared "
            "skills already cover it. Coverage requires the same condition "
            "AND the same action — not just the same topic. Return ONLY "
            "the policy_ids that are NOT covered. Do not write questions "
            "or commentary; the service will attach the seed-defined "
            "wizard questions after parsing your output.",
            "",
            "Output ONLY a single JSON object. No prose, no markdown fences.",
            "Schema:",
            '  {"missing_policy_ids": ["<exact id>", "<exact id>", ...]}',
            "",
            "Rules:",
            "- Each id MUST be an EXACT id from the typical policy list "
            "above. Do not invent ids or paraphrase them.",
            "- Skip ids fully covered by declared skills.",
            "- Empty list is a valid answer when every policy is covered.",
        ]
    )
    return "\n".join(lines)


def _parse_gap_response(raw: str, domain: str) -> GapAnalysis:
    try:
        body = extract_json_object(raw)
    except JsonExtractError as exc:
        raise SkillBootstrapParseError(str(exc)) from exc

    # Tolerate both the new `missing_policy_ids` key and the legacy
    # `missing: [{policy_id, ...}]` shape (so a stub or older live LLM
    # still parses while the prompt rolls out).
    missing_ids: list[str] = []
    if "missing_policy_ids" in body:
        ids_raw = body["missing_policy_ids"]
        if not isinstance(ids_raw, list):
            raise SkillBootstrapParseError(
                "`missing_policy_ids` must be a list"
            )
        missing_ids = [str(x) for x in ids_raw]
    elif "missing" in body:
        miss_raw = body["missing"]
        if not isinstance(miss_raw, list):
            raise SkillBootstrapParseError("`missing` must be a list")
        for entry in miss_raw:
            if isinstance(entry, dict) and "policy_id" in entry:
                missing_ids.append(str(entry["policy_id"]))
            elif isinstance(entry, str):
                missing_ids.append(entry)
            else:
                raise SkillBootstrapParseError(
                    f"missing entry must be object or string, got {entry!r}"
                )
    else:
        raise SkillBootstrapParseError(
            "response missing both `missing_policy_ids` and `missing`"
        )

    seed_index = {p["id"]: p for p in _seed_policies(domain)}
    enriched: list[PolicyGap] = []
    for pid in missing_ids:
        if pid not in seed_index:
            raise SkillBootstrapParseError(
                f"policy_id {pid!r} not in seed for domain {domain!r}"
            )
        enriched.append(_build_policy_gap(seed_index[pid]))

    return GapAnalysis(missing=enriched)


async def analyze_gaps(
    backend: LLMBackend,
    domain: DomainCategory,
    extracted_skills: list[ExtractedSkill],
) -> GapAnalysis:
    seeds = _seed_policies(domain)
    if not seeds:
        # "other" or any future un-seeded domain. Wizard handles the empty
        # case by falling back to a free-form skill capture flow.
        return GapAnalysis(missing=[])

    if not extracted_skills:
        # Persona A short-circuit: every policy is a gap by construction.
        # Skip the LLM entirely — the seed already has all wizard fields,
        # so an LLM call would only add latency.
        return GapAnalysis(
            missing=[_build_policy_gap(p) for p in seeds]
        )

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


# --- answers_to_skill (batch) --------------------------------------------


def _answers_to_skill_system_prompt(domain: str, seed_policy: dict) -> str:
    """Compile prompt for the batch (per-policy) draft.

    Includes every parameter's name + question + baseline so the LLM can
    weave the user's specific answers into a coherent condition/action.
    """
    param_block_lines: list[str] = []
    for p in seed_policy.get("parameters", []):
        param_block_lines.append(
            f"- {p['name']}: prompt={p['prompt']!r}, baseline={p.get('default_baseline', '')!r}"
        )
    param_block = "\n".join(param_block_lines) or "(no parameters)"

    return (
        "You are the answer-to-skill compiler for a workflow-automation "
        "product's skill-bootstrap flow. The user's domain is "
        f"`{domain}`. You will receive the user's per-parameter answers "
        "for ONE policy in the next message. Your job is to compile those "
        "answers into a single executable Skill record that captures the "
        "team's specific values for every parameter.\n\n"
        f"## Source policy template ({seed_policy['id']})\n"
        f"- name: {seed_policy['name']}\n"
        f"- condition (template): {seed_policy['condition'].strip()}\n"
        f"- action (template): {seed_policy['action'].strip()}\n"
        f"- rationale: {seed_policy['rationale'].strip()}\n"
        f"- parameters:\n{param_block}\n\n"
        "## Output\n\n"
        "Output ONLY a single JSON object. No prose, no markdown fences. "
        "Schema:\n"
        "  {\n"
        '    "name": "<short imperative name reflecting the policy>",\n'
        '    "description": "<one-sentence summary the user will recognize>",\n'
        '    "condition": "<concrete trigger with the user\'s specific '
        'values substituted for every parameter>",\n'
        '    "action": "<concrete action with the user\'s specific values '
        'substituted for every parameter>",\n'
        '    "rationale": "<one sentence on why this matters>",\n'
        '    "needs_clarification": <true if ANY answer is ambiguous, '
        "contradictory, or non-actionable>,\n"
        '    "clarification_hint": "<concrete follow-up question naming '
        'the parameter that needs clarification, if needs_clarification, '
        'else empty>"\n'
        "  }\n\n"
        "## Rules\n"
        "- Embed every parameter's user value into condition/action. "
        "Never echo a raw parameter name (e.g. answer \"$500\" → "
        "condition contains \"$500\", not REFUND_AUTO_APPROVE_LIMIT).\n"
        "- If multiple answers conflict (e.g. two different thresholds), "
        "set needs_clarification=true and write a clarification_hint that "
        "names which parameter is in conflict.\n"
        "- If any answer is \"I don't know\" / \"it depends\" without "
        "specifics, set needs_clarification=true and write a concrete "
        "clarification_hint.\n"
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


async def answers_to_skill(
    backend: LLMBackend,
    domain: DomainCategory,
    policy_id: str,
    answers: list[ParameterAnswer],
) -> SkillDraft:
    seed_policy = _find_policy(domain, policy_id)
    if seed_policy is None:
        raise ValueError(
            f"unknown policy_id {policy_id!r} for domain {domain!r}"
        )

    seed_param_names = set(_param_names(seed_policy))
    for entry in answers:
        if entry.parameter not in seed_param_names:
            raise ValueError(
                f"unknown parameter {entry.parameter!r} for policy "
                f"{policy_id!r}"
            )

    # Render the user's answers as one block per parameter so the LLM can
    # weave them into condition/action without hallucinating param names.
    user_lines: list[str] = []
    for entry in answers:
        param = _find_parameter(seed_policy, entry.parameter)
        prompt_text = param["prompt"] if param else entry.parameter
        user_lines.append(
            f"- {entry.parameter}: question={prompt_text!r}, "
            f"answer={entry.answer.strip()!r}"
        )

    raw = await backend.complete(
        system=_answers_to_skill_system_prompt(domain, seed_policy),
        user_message="Per-parameter answers:\n" + "\n".join(user_lines),
        max_tokens=ANSWERS_TO_SKILL_MAX_TOKENS,
    )
    return _parse_skill_response(raw)


# --- legacy single-shot wrapper ------------------------------------------


async def answer_to_skill(
    backend: LLMBackend,
    domain: DomainCategory,
    policy_id: str,
    question: str,
    answer: str,
) -> SkillDraft:
    """Legacy single-shot shim. PR #143 removes the caller; until then we
    pass through to the batch path with a one-element list. The `question`
    arg is ignored — the seed prompt is used so the live LLM sees the
    same prompt regardless of which client called it.
    """
    seed_policy = _find_policy(domain, policy_id)
    if seed_policy is None:
        raise ValueError(
            f"unknown policy_id {policy_id!r} for domain {domain!r}"
        )

    # Pick the first parameter as the target — the legacy contract had no
    # parameter binding on the answer, and PR #143 retires this path.
    params = seed_policy.get("parameters", [])
    if not params:
        raise ValueError(
            f"policy {policy_id!r} has no parameters — legacy answer_to_skill "
            "cannot route this answer"
        )
    return await answers_to_skill(
        backend,
        domain,
        policy_id,
        [ParameterAnswer(parameter=params[0]["name"], answer=answer)],
    )
