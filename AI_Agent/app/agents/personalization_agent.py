"""PLAN_14 PR-D — personalization propose+judge agent.

Two sequential LLM calls over a `WorkflowDiff` (PR-C):

  1. `propose_hint`  — diff (+ optional v1 context) → generalization hint
                       (≤ 30 words) or noise-drop signal.
  2. `judge_proposal` — hint + diff signature (+ optional list of
                       previously-rejected hashes) → accept | reject.

No tool calling, no loop, no langgraph. PLAN_14 §4.2 caps the agent at
`max_iter=1` — the reflect branch from PLAN_13 is deliberately omitted
because a hint that the judge rejects is noise, and re-running propose
on the same diff would just produce the same noise. So we keep the
implementation as plain async functions and let PR-E's
`personalization_service.py` orchestrate the DB write + dedupe.

`@traceable` rides on each LLM-touching helper plus the public
entrypoint, so the LangSmith trace tree shows the propose / judge calls
as separate child nodes under one `personalization_agent` parent.

Hash + signature helpers (`suggestion_hash`, `diff_signature`) are pure
functions and intentionally live here next to the agent — both feed the
judge prompt (rejected-hashes injection, §4.5) and PR-E's dedupe
(`personal_skill_reviews.suggestion_hash`, §4.3), and keeping them in
the diff module would split a single concern across two files.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.agents.tracing import traceable
from app.backends.protocols import LLMBackend
from app.models.personalization import (
    PersonalizationJudgment,
    PersonalizationOutcome,
    PersonalizationProposal,
)
from app.services._llm_json import JsonExtractError, extract_json_object
from app.services.workflow_diff import WorkflowDiff

logger = logging.getLogger(__name__)

# ---- token budgets ---------------------------------------------------------
# PLAN_14 §4.5 — propose ≤ 256 (30-word hint plus a bit of reasoning room),
# judge ≤ 128 (decision + short reason). Generous enough that a model that
# adds a thinking trace does not truncate before the JSON envelope, tight
# enough that a propose that strays into prose still terminates.
PROPOSE_MAX_TOKENS = 256
JUDGE_MAX_TOKENS = 128

# How many recent rejected hashes to surface in the judge prompt. Five is
# enough to anchor the model on the user's recent dismissals without
# bloating the prompt — and the judge prompt's job is to flag a re-hit,
# not to memorize the full history.
_REJECTED_HASH_PROMPT_LIMIT = 5

# PLAN_14 §4.3 — SHA256 prefix length for the dedupe hash. 16 hex chars
# = 64 bits, collision probability negligible at the scale we're at.
_HASH_PREFIX_LEN = 16


# --- prompt helpers --------------------------------------------------------


def _propose_system_prompt() -> str:
    return (
        "You inspect ONE edit a user made to an AI-generated workflow draft.\n\n"
        "Output a JSON object describing whether the edit captures a "
        "generalizable preference:\n"
        '  {"hint": "<short generalization, ≤ 30 words>", "is_noise": <bool>}\n\n'
        "Set `is_noise` to true and `hint` to \"\" when the edit is a label "
        "rename, typo fix, parameter rename, or any other one-off correction "
        "that won't apply to other workflows. Otherwise set `is_noise` to "
        "false and write a hint that captures the user's preference "
        "(e.g. \"adds Slack notify after credential-touching steps\", "
        "\"prefers 5min retry instead of the default 30s\").\n\n"
        "Output ONLY the JSON object. No prose, no markdown."
    )


def _propose_user_message(
    diff_dict: dict[str, Any], v1_payload: dict[str, Any] | None
) -> str:
    parts: list[str] = []
    if v1_payload is not None:
        # Sort keys for prompt stability — two identical diffs over the
        # same v1 must produce identical prompts so the suggestion_hash
        # path is reproducible in tests.
        parts.append(
            "original_draft:\n```json\n"
            + json.dumps(v1_payload, sort_keys=True)
            + "\n```"
        )
    parts.append(
        "diff:\n```json\n" + json.dumps(diff_dict, sort_keys=True) + "\n```"
    )
    return "\n\n".join(parts)


def _judge_system_prompt(rejected_hashes_sample: list[str] | None) -> str:
    rejected_block = ""
    if rejected_hashes_sample:
        sample = list(rejected_hashes_sample)[:_REJECTED_HASH_PROMPT_LIMIT]
        rejected_block = (
            "\n\nThe user previously rejected these suggestion fingerprints "
            "— reject anything matching them:\n"
            + "\n".join(f"- {h}" for h in sample)
        )
    return (
        "Validate a personalization hint derived from a user's workflow edit.\n\n"
        "Reject when any holds:\n"
        "- The hint is workflow-specific and won't generalize.\n"
        "- The hint is a label rename / typo correction.\n"
        "- The hint contradicts the diff signature (claims a node type "
        "the diff did not actually add or remove).\n"
        f"{rejected_block}\n\n"
        "Output ONLY a JSON object: "
        '{"decision": "accept"|"reject", "reason": "<short>"}'
    )


def _judge_user_message(hint: str, signature: str) -> str:
    return f"hint: {hint}\ndiff_signature: {signature}"


# --- parsers ---------------------------------------------------------------


def _parse_propose_response(raw: str) -> PersonalizationProposal:
    try:
        body = extract_json_object(raw)
    except JsonExtractError:
        # Treat unparseable propose responses as noise — the propose
        # call is supposed to be terse JSON; a free-form reply means the
        # model didn't follow the contract, and re-prompting wastes
        # tokens. The caller (`run_personalization_agent`) drops on
        # is_noise=True.
        logger.info("propose response failed JSON parse; treating as noise")
        return PersonalizationProposal(hint="", is_noise=True, raw=raw)

    hint = body.get("hint", "")
    is_noise = bool(body.get("is_noise", False))
    if not isinstance(hint, str):
        hint = ""
        is_noise = True
    return PersonalizationProposal(
        hint=hint.strip(),
        is_noise=is_noise,
        raw=raw,
    )


def _parse_judge_response(raw: str) -> PersonalizationJudgment:
    try:
        body = extract_json_object(raw)
    except JsonExtractError:
        # An unparseable judge response is conservative-rejected — the
        # candidate falls through to the human review queue's "rejected
        # for parser failure" bucket, which is the safe default when we
        # can't read the model's decision.
        logger.info("judge response failed JSON parse; defaulting to reject")
        return PersonalizationJudgment(
            decision="reject", reason="judge_parse_error", raw=raw
        )

    decision_raw = body.get("decision", "")
    decision = "accept" if decision_raw == "accept" else "reject"
    reason_value = body.get("reason", "")
    reason = str(reason_value) if reason_value is not None else ""
    return PersonalizationJudgment(decision=decision, reason=reason, raw=raw)


# --- hash + signature helpers ---------------------------------------------


def diff_signature(diff: WorkflowDiff) -> str:
    """Stable string fingerprint of a diff's node-type churn.

    PLAN_14 §4.3. Used both for the judge prompt (so the model can
    sanity-check the hint against the actual structural change) and as
    one half of `suggestion_hash`. We deliberately ignore params /
    edges / ordering — those vary across workflows even when the
    generalization is the same ("user always adds a Slack notify").
    """
    added = sorted(
        n.get("type", "") for n in diff.nodes_added if n.get("type")
    )
    removed = sorted(
        n.get("type", "") for n in diff.nodes_removed if n.get("type")
    )
    return f"added={','.join(added)};removed={','.join(removed)}"


def suggestion_hash(hint: str, diff: WorkflowDiff) -> str:
    """PLAN_14 §4.3 — SHA256 prefix of (normalized hint || diff signature).

    `hint` is lowercased + whitespace-stripped before hashing so the
    same user pattern produces a stable hash across small wording
    drift from the LLM. PR-E's `personal_skill_reviews` table uses
    this hash to suppress a previously-rejected candidate from being
    re-surfaced on a similar future edit.
    """
    normalized = " ".join(hint.strip().lower().split())
    blob = f"{normalized}|{diff_signature(diff)}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:_HASH_PREFIX_LEN]


# --- LLM-touching nodes ----------------------------------------------------


@traceable(name="personalization_propose", run_type="llm")
async def propose_hint(
    backend: LLMBackend,
    diff: WorkflowDiff,
    v1_payload: dict[str, Any] | None = None,
) -> PersonalizationProposal:
    raw = await backend.complete(
        system=_propose_system_prompt(),
        user_message=_propose_user_message(diff.to_dict(), v1_payload),
        max_tokens=PROPOSE_MAX_TOKENS,
    )
    return _parse_propose_response(raw)


@traceable(name="personalization_judge", run_type="llm")
async def judge_proposal(
    backend: LLMBackend,
    hint: str,
    diff: WorkflowDiff,
    *,
    rejected_hashes: list[str] | None = None,
) -> PersonalizationJudgment:
    raw = await backend.complete(
        system=_judge_system_prompt(rejected_hashes),
        user_message=_judge_user_message(hint, diff_signature(diff)),
        max_tokens=JUDGE_MAX_TOKENS,
    )
    return _parse_judge_response(raw)


# --- public entrypoint -----------------------------------------------------


@traceable(name="personalization_agent", run_type="chain")
async def run_personalization_agent(
    backend: LLMBackend,
    diff: WorkflowDiff,
    v1_payload: dict[str, Any] | None = None,
    *,
    rejected_hashes: list[str] | None = None,
) -> PersonalizationOutcome:
    """Run propose → judge on a single diff. Returns the outcome.

    `accepted=True` is PR-E's signal to write a candidate row.
    `accepted=False` always carries a `drop_reason`:
      * `empty_diff` — the diff was empty (nothing to propose on).
      * `empty_proposal` — propose returned noise or empty hint.
      * `hash_previously_rejected` — the hint+signature hash matches a
        previously-rejected suggestion; judge call skipped.
      * `judge_reject` — judge returned `decision: reject`.

    `suggestion_hash` is populated whenever propose produced a non-empty
    hint, regardless of judge outcome — PR-E uses it to dedupe across
    runs even when the judgment is reject.
    """
    if diff.is_empty:
        return PersonalizationOutcome(
            proposal=PersonalizationProposal(),
            accepted=False,
            drop_reason="empty_diff",
        )

    proposal = await propose_hint(backend, diff, v1_payload)
    if proposal.is_empty:
        return PersonalizationOutcome(
            proposal=proposal,
            accepted=False,
            drop_reason="empty_proposal",
        )

    hsh = suggestion_hash(proposal.hint, diff)

    if rejected_hashes and hsh in rejected_hashes:
        # Cheap pre-check: if this exact (hint, signature) was rejected
        # before, skip the judge LLM call. Saves a Modal hop on repeat
        # noise patterns. Judge is still aware of the rejected list via
        # the prompt-side injection — this gate just short-circuits the
        # exact-match case where the model never even needs to think.
        return PersonalizationOutcome(
            proposal=proposal,
            accepted=False,
            drop_reason="hash_previously_rejected",
            suggestion_hash=hsh,
        )

    judgment = await judge_proposal(
        backend, proposal.hint, diff, rejected_hashes=rejected_hashes
    )
    accepted = judgment.decision == "accept"
    return PersonalizationOutcome(
        proposal=proposal,
        judgment=judgment,
        accepted=accepted,
        drop_reason="" if accepted else "judge_reject",
        suggestion_hash=hsh,
    )


__all__ = [
    "PROPOSE_MAX_TOKENS",
    "JUDGE_MAX_TOKENS",
    "diff_signature",
    "judge_proposal",
    "propose_hint",
    "run_personalization_agent",
    "suggestion_hash",
]
