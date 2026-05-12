"""Unit tests for `app.agents.personalization_agent`.

Stub backend, tracing OFF (`@traceable` is a no-op without
`LANGCHAIN_TRACING_V2` — `agents.tracing` checks that at import). The
graph contract is two LLM calls in sequence with five drop-reason
branches, so we exercise each branch plus the pure hash + signature
helpers that PR-E will rely on for the `personal_skill_reviews` dedupe.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents.personalization_agent import (
    diff_signature,
    judge_proposal,
    propose_hint,
    run_personalization_agent,
    suggestion_hash,
)
from app.models.personalization import (
    PersonalizationJudgment,
    PersonalizationOutcome,
    PersonalizationProposal,
)
from app.services.workflow_diff import WorkflowDiff, diff_workflow


# --- fixtures + helpers ----------------------------------------------------


def _wf(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {"nodes": nodes, "edges": edges or []}


def _node(nid: str, ntype: str = "http_request", **config: Any) -> dict:
    return {"id": nid, "type": ntype, "config": dict(config)}


def _slack_add_diff() -> WorkflowDiff:
    """Common fixture — user adds a Slack notify node after the fetch."""
    v1 = _wf([_node("fetch")])
    v2 = _wf(
        [_node("fetch"), _node("notify", "slack_notify", channel="#alerts")],
        [{"source": "fetch", "target": "notify"}],
    )
    return diff_workflow(v1, v2)


class _ScriptedBackend:
    """LLMBackend stub that returns canned responses in order.

    Each `complete` call pops one entry off `responses`. Records the
    (system, user, max_tokens) so prompt invariants can be asserted.
    """

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        self.calls.append(
            {
                "system": system,
                "user_message": user_message,
                "max_tokens": max_tokens,
            }
        )
        if not self.responses:
            raise AssertionError(
                "_ScriptedBackend exhausted — agent made one more call than expected"
            )
        return self.responses.pop(0)


# --- propose ----------------------------------------------------------------


async def test_propose_returns_hint_on_clean_json_envelope() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"hint": "user adds Slack notify after fetch", "is_noise": False})]
    )
    proposal = await propose_hint(backend, _slack_add_diff())
    assert proposal.hint == "user adds Slack notify after fetch"
    assert proposal.is_noise is False
    assert proposal.is_empty is False


async def test_propose_marks_noise_when_model_says_so() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"hint": "", "is_noise": True})]
    )
    proposal = await propose_hint(backend, _slack_add_diff())
    assert proposal.is_noise is True
    assert proposal.is_empty is True


async def test_propose_tolerates_json_fence() -> None:
    """`extract_json_object` strips ```json fences — Gemma 4 occasionally
    adds them despite the prompt asking for raw JSON."""
    backend = _ScriptedBackend(
        ['```json\n{"hint": "prefer 5min retry", "is_noise": false}\n```']
    )
    proposal = await propose_hint(backend, _slack_add_diff())
    assert proposal.hint == "prefer 5min retry"


async def test_propose_treats_unparseable_response_as_noise() -> None:
    """An unparseable propose response cannot be retried — the prompt was
    explicit, and re-running on the same diff would produce the same garbage.
    The agent drops on `is_empty=True`, which is the safe path."""
    backend = _ScriptedBackend(["I think the user prefers Slack alerts."])
    proposal = await propose_hint(backend, _slack_add_diff())
    assert proposal.is_noise is True
    assert proposal.is_empty is True


async def test_propose_strips_whitespace_from_hint() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"hint": "   adds Slack notify   ", "is_noise": False})]
    )
    proposal = await propose_hint(backend, _slack_add_diff())
    assert proposal.hint == "adds Slack notify"


async def test_propose_respects_max_tokens() -> None:
    backend = _ScriptedBackend([json.dumps({"hint": "x", "is_noise": False})])
    await propose_hint(backend, _slack_add_diff())
    from app.agents.personalization_agent import PROPOSE_MAX_TOKENS

    assert backend.calls[0]["max_tokens"] == PROPOSE_MAX_TOKENS


async def test_propose_user_message_includes_diff_dict() -> None:
    backend = _ScriptedBackend([json.dumps({"hint": "x", "is_noise": False})])
    diff = _slack_add_diff()
    await propose_hint(backend, diff)
    user_msg = backend.calls[0]["user_message"]
    # The diff JSON is embedded — agent sees the actual structural change.
    assert "slack_notify" in user_msg
    assert "nodes_added" in user_msg


async def test_propose_user_message_includes_v1_when_provided() -> None:
    backend = _ScriptedBackend([json.dumps({"hint": "x", "is_noise": False})])
    v1 = _wf([_node("fetch")])
    v2 = _wf(
        [_node("fetch"), _node("notify", "slack_notify")],
    )
    diff = diff_workflow(v1, v2)
    await propose_hint(backend, diff, v1_payload=v1)
    user_msg = backend.calls[0]["user_message"]
    assert "original_draft" in user_msg


# --- judge -----------------------------------------------------------------


async def test_judge_returns_accept() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"decision": "accept", "reason": "generalizable"})]
    )
    judgment = await judge_proposal(
        backend, "adds Slack notify after fetch", _slack_add_diff()
    )
    assert judgment.decision == "accept"
    assert judgment.reason == "generalizable"


async def test_judge_returns_reject() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"decision": "reject", "reason": "label rename"})]
    )
    judgment = await judge_proposal(
        backend, "renamed step", _slack_add_diff()
    )
    assert judgment.decision == "reject"


async def test_judge_parse_error_defaults_to_reject() -> None:
    """An unparseable judge response is conservative-rejected so a
    garbled model output can't sneak a noise candidate through."""
    backend = _ScriptedBackend(["yes that looks fine to me"])
    judgment = await judge_proposal(
        backend, "anything", _slack_add_diff()
    )
    assert judgment.decision == "reject"
    assert judgment.reason == "judge_parse_error"


async def test_judge_unknown_decision_value_treated_as_reject() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"decision": "maybe", "reason": "unsure"})]
    )
    judgment = await judge_proposal(
        backend, "x", _slack_add_diff()
    )
    assert judgment.decision == "reject"


async def test_judge_user_message_carries_diff_signature() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"decision": "accept", "reason": ""})]
    )
    diff = _slack_add_diff()
    await judge_proposal(backend, "adds slack notify", diff)
    user_msg = backend.calls[0]["user_message"]
    assert "diff_signature:" in user_msg
    assert "slack_notify" in user_msg


async def test_judge_system_prompt_injects_rejected_hashes() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"decision": "accept", "reason": ""})]
    )
    await judge_proposal(
        backend,
        "x",
        _slack_add_diff(),
        rejected_hashes=["abc1234567890abc", "def4567890abcdef"],
    )
    system = backend.calls[0]["system"]
    assert "abc1234567890abc" in system
    assert "def4567890abcdef" in system


async def test_judge_omits_rejected_block_when_empty() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"decision": "accept", "reason": ""})]
    )
    await judge_proposal(backend, "x", _slack_add_diff(), rejected_hashes=None)
    system = backend.calls[0]["system"]
    assert "previously rejected" not in system


async def test_judge_respects_max_tokens() -> None:
    backend = _ScriptedBackend(
        [json.dumps({"decision": "accept", "reason": ""})]
    )
    await judge_proposal(backend, "x", _slack_add_diff())
    from app.agents.personalization_agent import JUDGE_MAX_TOKENS

    assert backend.calls[0]["max_tokens"] == JUDGE_MAX_TOKENS


# --- diff_signature + suggestion_hash (pure) -------------------------------


def test_diff_signature_lists_added_and_removed_types() -> None:
    diff = _slack_add_diff()
    assert diff_signature(diff) == "added=slack_notify;removed="


def test_diff_signature_sorted_for_determinism() -> None:
    v1 = _wf([_node("a"), _node("b"), _node("c")])
    v2 = _wf(
        [
            _node("a"),
            _node("d", "zeta_notify"),
            _node("e", "alpha_check"),
            _node("f", "mu_route"),
        ]
    )
    diff = diff_workflow(v1, v2)
    # Types alphabetized — repeated runs yield the same signature.
    assert diff_signature(diff) == "added=alpha_check,mu_route,zeta_notify;removed=http_request,http_request"


def test_suggestion_hash_is_deterministic() -> None:
    diff = _slack_add_diff()
    h1 = suggestion_hash("adds slack notify", diff)
    h2 = suggestion_hash("adds slack notify", diff)
    assert h1 == h2
    assert len(h1) == 16


def test_suggestion_hash_case_and_whitespace_normalized() -> None:
    diff = _slack_add_diff()
    h1 = suggestion_hash("Adds Slack Notify", diff)
    h2 = suggestion_hash("  adds   slack   notify  ", diff)
    assert h1 == h2


def test_suggestion_hash_distinguishes_different_signatures() -> None:
    diff_slack = _slack_add_diff()
    v1 = _wf([_node("fetch")])
    v2 = _wf([_node("fetch"), _node("page", "pagerduty_alert")])
    diff_pager = diff_workflow(v1, v2)
    # Same hint, different added-type → different hash.
    h_slack = suggestion_hash("adds notify after fetch", diff_slack)
    h_pager = suggestion_hash("adds notify after fetch", diff_pager)
    assert h_slack != h_pager


# --- run_personalization_agent (orchestration) ----------------------------


async def test_run_agent_happy_path_accepts() -> None:
    backend = _ScriptedBackend(
        [
            json.dumps({"hint": "adds Slack notify after fetch", "is_noise": False}),
            json.dumps({"decision": "accept", "reason": "generalizable"}),
        ]
    )
    outcome = await run_personalization_agent(backend, _slack_add_diff())
    assert outcome.accepted is True
    assert outcome.drop_reason == ""
    assert outcome.proposal.hint == "adds Slack notify after fetch"
    assert outcome.judgment is not None
    assert outcome.judgment.decision == "accept"
    assert outcome.suggestion_hash is not None
    assert len(outcome.suggestion_hash) == 16


async def test_run_agent_drops_empty_diff_without_calling_backend() -> None:
    """Empty diff is a budget-saver path — no propose, no judge."""
    empty_diff = WorkflowDiff((), (), (), (), (), False)
    backend = _ScriptedBackend([])  # any call would raise
    outcome = await run_personalization_agent(backend, empty_diff)
    assert outcome.accepted is False
    assert outcome.drop_reason == "empty_diff"
    assert backend.calls == []


async def test_run_agent_drops_on_empty_proposal_without_calling_judge() -> None:
    """is_noise=True from propose short-circuits — judge would just see
    an empty hint and reject, so we save the call."""
    backend = _ScriptedBackend(
        [json.dumps({"hint": "", "is_noise": True})]
    )
    outcome = await run_personalization_agent(backend, _slack_add_diff())
    assert outcome.accepted is False
    assert outcome.drop_reason == "empty_proposal"
    assert outcome.judgment is None
    assert len(backend.calls) == 1  # propose only


async def test_run_agent_drops_on_judge_reject() -> None:
    backend = _ScriptedBackend(
        [
            json.dumps({"hint": "renamed Step 1 to Send report", "is_noise": False}),
            json.dumps({"decision": "reject", "reason": "label rename"}),
        ]
    )
    outcome = await run_personalization_agent(backend, _slack_add_diff())
    assert outcome.accepted is False
    assert outcome.drop_reason == "judge_reject"
    assert outcome.judgment is not None
    assert outcome.judgment.decision == "reject"
    # Hash is set even on reject so PR-E can suppress the same pattern next time.
    assert outcome.suggestion_hash is not None


async def test_run_agent_skips_judge_when_hash_previously_rejected() -> None:
    """The exact-match short-circuit — if the propose hint hashes to a
    previously-rejected fingerprint, drop without burning a judge call."""
    hint = "adds Slack notify after fetch"
    diff = _slack_add_diff()
    precomputed_hash = suggestion_hash(hint, diff)

    backend = _ScriptedBackend(
        [json.dumps({"hint": hint, "is_noise": False})]
    )
    outcome = await run_personalization_agent(
        backend, diff, rejected_hashes=[precomputed_hash]
    )
    assert outcome.accepted is False
    assert outcome.drop_reason == "hash_previously_rejected"
    assert outcome.suggestion_hash == precomputed_hash
    assert len(backend.calls) == 1  # propose only — judge skipped


async def test_run_agent_outcome_serializes_via_pydantic() -> None:
    """PR-E will write the outcome to the candidate row's agent_trace
    JSONB. Round-trip through model_dump_json must not lose fields."""
    backend = _ScriptedBackend(
        [
            json.dumps({"hint": "x", "is_noise": False}),
            json.dumps({"decision": "accept", "reason": "ok"}),
        ]
    )
    outcome = await run_personalization_agent(backend, _slack_add_diff())
    blob = outcome.model_dump_json()
    parsed = PersonalizationOutcome.model_validate_json(blob)
    assert parsed.accepted is True
    assert parsed.suggestion_hash == outcome.suggestion_hash
    assert parsed.judgment is not None


async def test_run_agent_passes_rejected_hashes_into_judge_prompt() -> None:
    """The rejected list reaches the judge system prompt even on the
    happy (non-short-circuit) path — judge sees them as a soft signal."""
    backend = _ScriptedBackend(
        [
            json.dumps({"hint": "adds metrics step", "is_noise": False}),
            json.dumps({"decision": "accept", "reason": "ok"}),
        ]
    )
    await run_personalization_agent(
        backend, _slack_add_diff(), rejected_hashes=["fff0000000000000"]
    )
    # Two calls — propose then judge.
    assert len(backend.calls) == 2
    judge_system = backend.calls[1]["system"]
    assert "fff0000000000000" in judge_system
