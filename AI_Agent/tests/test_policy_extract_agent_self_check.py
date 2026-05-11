"""Unit tests for PR-ε deterministic self-check tools.

Two new tools are added by `policy_extract_agent` in PR-ε:

  - `validate_skill_schema(draft)` — pure-Python schema sanity check
  - `cite_source_url(draft)`       — domain seed-catalog name match

Both are unconditionally registered (no embedding backend, no DB,
no network), so the tests reach them by running the full
`run_policy_extract_agent` driver with a sequenced backend that
mimics the agent's tool-call decisions. We assert on the
observation payload that the agent reads back, since that's the
contract the prompt promises.

The system-goal text also changed in PR-ε (a new "OPTIONAL
pre-finish self-check" section is always appended). One test
covers the rendered prompt to lock the wording in.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents.policy_extract_agent import (
    _system_goal,
    run_policy_extract_agent,
)
from app.services.policy_extract import PolicyExtractParseError  # noqa: F401


_AGENT_PROMPT_PREFIX = "You are an extraction agent"
_EXTRACT_PROMPT_PREFIX = "You are the policy extractor"
_JUDGE_PROMPT_PREFIX = "You are a critic for a policy-extraction step"


def _candidate(
    name: str = "approve-large-refunds",
    *,
    condition: str = "Refunds over $500",
    action: str = "Escalate to manager",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} description",
        "condition": condition,
        "action": action,
        "rationale": "Drawn from the chunk",
        "needs_clarification": False,
        "clarification_hint": "",
    }


def _agent_call(name: str, args: dict | None = None) -> str:
    return json.dumps(
        {"action": "tool_call", "name": name, "args": args or {}}
    )


def _agent_finish(drafts: list[dict[str, Any]]) -> str:
    return json.dumps({"action": "finish", "result": {"drafts": drafts}})


class _RecordingBackend:
    """Like `_SequencedBackend` from the loop tests but it ALSO records
    every observation the agent saw on its way to <finish>. The agent
    receives observations in the user_message string of the next call,
    so we just keep a running log of every user_message and let tests
    grep for the relevant tool result."""

    def __init__(
        self,
        *,
        agent: list[str] | None = None,
        extract: list[str] | None = None,
    ) -> None:
        self._agent = list(agent or [])
        self._extract = list(extract or [])
        self.agent_calls = 0
        self.extract_calls = 0
        self.judge_calls = 0
        self.user_messages: list[str] = []

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        del max_tokens, images
        if system.startswith(_AGENT_PROMPT_PREFIX):
            self.user_messages.append(user_message)
            if self.agent_calls >= len(self._agent):
                raise AssertionError(
                    f"agent backend exhausted at agent_calls={self.agent_calls}"
                )
            out = self._agent[self.agent_calls]
            self.agent_calls += 1
            return out

        if system.startswith(_EXTRACT_PROMPT_PREFIX):
            if self.extract_calls >= len(self._extract):
                raise AssertionError("extract backend exhausted")
            out = self._extract[self.extract_calls]
            self.extract_calls += 1
            return out

        if system.startswith(_JUDGE_PROMPT_PREFIX):
            self.judge_calls += 1
            return '{"missed": []}'

        raise AssertionError(
            f"unrecognized system prompt prefix: {system[:80]!r}"
        )

    async def stream(self, **_):  # noqa: ANN001, ANN003
        if False:  # pragma: no cover
            yield ""

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _last_observation_for(messages: list[str], tool_name: str) -> dict[str, Any]:
    """Find the most recent `<tool_result tool="X">...</tool_result>`
    block in the running transcript and JSON-decode the payload. The
    agent_loop renders observations with that exact tag (see
    `_render_observation` in agent_loop.py). The `tool_name` argument
    is matched on the `tool="..."` attribute so a misnamed tool fails
    the assertion loudly rather than silently picking a sibling.
    """
    if not messages:
        raise AssertionError("no agent messages recorded")
    last = messages[-1]
    marker = f'<tool_result tool="{tool_name}">'
    idx = last.rfind(marker)
    if idx < 0:
        raise AssertionError(
            f"tool_result for {tool_name!r} not found in last user_message"
        )
    payload_start = idx + len(marker)
    end = last.find("</tool_result>", payload_start)
    if end < 0:
        raise AssertionError(
            f"unterminated tool_result block for {tool_name!r}"
        )
    payload = last[payload_start:end].strip()
    return json.loads(payload)


# --- validate_skill_schema -----------------------------------------------


@pytest.mark.asyncio
async def test_validate_skill_schema_passes_well_formed_draft() -> None:
    cand = _candidate()
    backend = _RecordingBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call("validate_skill_schema", {"draft": cand}),
            _agent_finish([cand]),
        ],
        extract=[json.dumps({"candidates": [cand]})],
    )

    _, _, reason, finals = await run_policy_extract_agent(
        backend,
        chunk="Refunds over $500 must be approved by a manager.",
        domain="ecommerce",
    )

    assert reason == "converge"
    assert len(finals) == 1
    obs = _last_observation_for(
        backend.user_messages, "validate_skill_schema"
    )
    assert obs == {"valid": True, "issues": []}


@pytest.mark.asyncio
async def test_validate_skill_schema_flags_empty_condition() -> None:
    bad = _candidate(condition="   ")  # whitespace-only — Pydantic-strict
    cand = _candidate()
    backend = _RecordingBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call("validate_skill_schema", {"draft": bad}),
            _agent_finish([cand]),
        ],
        extract=[json.dumps({"candidates": [cand]})],
    )

    await run_policy_extract_agent(
        backend,
        chunk="Refunds over $500 must be approved by a manager.",
        domain="ecommerce",
    )
    obs = _last_observation_for(
        backend.user_messages, "validate_skill_schema"
    )
    assert obs["valid"] is False
    assert any("condition" in s for s in obs["issues"])


@pytest.mark.asyncio
async def test_validate_skill_schema_flags_placeholder_marker() -> None:
    bad = _candidate(action="TODO replace with policy")
    cand = _candidate()
    backend = _RecordingBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call("validate_skill_schema", {"draft": bad}),
            _agent_finish([cand]),
        ],
        extract=[json.dumps({"candidates": [cand]})],
    )

    await run_policy_extract_agent(
        backend,
        chunk="Refunds over $500 must be approved by a manager.",
        domain="ecommerce",
    )
    obs = _last_observation_for(
        backend.user_messages, "validate_skill_schema"
    )
    assert obs["valid"] is False
    assert any("TODO" in s for s in obs["issues"])


@pytest.mark.asyncio
async def test_validate_skill_schema_rejects_non_object_draft() -> None:
    cand = _candidate()
    backend = _RecordingBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call("validate_skill_schema", {"draft": "not an object"}),
            _agent_finish([cand]),
        ],
        extract=[json.dumps({"candidates": [cand]})],
    )

    await run_policy_extract_agent(
        backend,
        chunk="Refunds over $500 must be approved by a manager.",
        domain="ecommerce",
    )
    obs = _last_observation_for(
        backend.user_messages, "validate_skill_schema"
    )
    assert obs == {
        "valid": False,
        "issues": ["`draft` must be an object (dict)"],
    }


# --- cite_source_url -----------------------------------------------------


@pytest.mark.asyncio
async def test_cite_source_url_returns_empty_for_other_domain() -> None:
    """`domain="other"` is the cold-start safety net — there is no seed
    YAML at all, so even an obvious name match like "Refund threshold"
    must return empty `sources` rather than crash. This is the literal
    regression guard for cold-start parity."""
    cand = _candidate(name="Refund threshold")
    backend = _RecordingBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call("cite_source_url", {"draft": cand}),
            _agent_finish([cand]),
        ],
        extract=[json.dumps({"candidates": [cand]})],
    )

    await run_policy_extract_agent(
        backend,
        chunk="Refunds over $500 must be approved by a manager.",
        domain="other",
    )
    obs = _last_observation_for(backend.user_messages, "cite_source_url")
    assert obs == {"sources": [], "source_kind": "synthesized"}


@pytest.mark.asyncio
async def test_cite_source_url_matches_bundled_ecommerce_seed() -> None:
    """When a draft's name has token overlap with a seed policy in the
    bundled ecommerce.yaml, the tool returns that seed's `sources` and
    `source_kind`. We do NOT lock the test to an exact policy_id (the
    seed YAML is curated and may be re-edited) — instead we assert that
    the observation has the shape promised by the wire and that
    source_kind comes from the curated values, not the synthesized
    fallback. The 'refund' token is heavily represented in the seed,
    so the match is robust to edits."""
    cand = _candidate(
        name="Refund approval threshold",
        condition="Refunds over $500",
        action="Escalate to manager",
    )
    backend = _RecordingBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call("cite_source_url", {"draft": cand}),
            _agent_finish([cand]),
        ],
        extract=[json.dumps({"candidates": [cand]})],
    )

    await run_policy_extract_agent(
        backend,
        chunk="Refunds over $500 must be approved by a manager.",
        domain="ecommerce",
    )
    obs = _last_observation_for(backend.user_messages, "cite_source_url")
    # source_kind must be one of the curated values when a match is
    # found — synthesized would mean "no match" and contradict the
    # token-overlap setup.
    assert obs["source_kind"] in {
        "regulatory",
        "industry-baseline",
        "synthesized",
    }
    # `sources` is a list (empty or populated). When populated each
    # entry must have title + url (the WebFetch-validated shape from
    # PR #142).
    assert isinstance(obs["sources"], list)
    for src in obs["sources"]:
        assert "title" in src and "url" in src


@pytest.mark.asyncio
async def test_cite_source_url_returns_empty_for_no_token_match() -> None:
    """A draft whose name shares no >2-char tokens with any seed
    policy in the domain returns empty — the matching threshold of
    score >= 1 prevents accidental matches via stop-word overlap.
    'Lorem ipsum' has no overlap with any ecommerce seed name."""
    cand = _candidate(
        name="Lorem ipsum dolor",
        condition="Some condition",
        action="Some action",
    )
    backend = _RecordingBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call("cite_source_url", {"draft": cand}),
            _agent_finish([cand]),
        ],
        extract=[json.dumps({"candidates": [cand]})],
    )

    await run_policy_extract_agent(
        backend,
        chunk="Refunds over $500 must be approved by a manager.",
        domain="ecommerce",
    )
    obs = _last_observation_for(backend.user_messages, "cite_source_url")
    assert obs == {"sources": [], "source_kind": "synthesized"}


@pytest.mark.asyncio
async def test_cite_source_url_rejects_non_object_draft() -> None:
    cand = _candidate()
    backend = _RecordingBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call("cite_source_url", {"draft": ["not", "an", "object"]}),
            _agent_finish([cand]),
        ],
        extract=[json.dumps({"candidates": [cand]})],
    )

    await run_policy_extract_agent(
        backend,
        chunk="Refunds over $500 must be approved by a manager.",
        domain="ecommerce",
    )
    obs = _last_observation_for(backend.user_messages, "cite_source_url")
    assert obs == {"sources": [], "source_kind": "synthesized"}


# --- system goal text ----------------------------------------------------


def test_system_goal_always_includes_self_check_section_cold_start() -> None:
    """PR-ε is unconditional. Cold-start (memory_enabled=False,
    baseline_enabled=False) must still surface the self-check tools
    so the agent knows they exist. We assert on the section heading
    and tool names ONLY — the prose body was deliberately trimmed to
    avoid biasing the agent's retry decision (the longer MAY/SHOULD-
    NOT phrasing tested in an earlier draft caused a -2 cand
    regression on the GitLab smoke; see git history)."""
    goal = _system_goal(
        "other",
        2,
        memory_enabled=False,
        baseline_enabled=False,
    )
    assert "Optional helpers" in goal
    assert "validate_skill_schema(draft)" in goal
    assert "cite_source_url(draft)" in goal
    # Regression guard: the prescriptive phrases that biased the
    # agent's retry decision must NOT come back.
    assert "Skip it on" not in goal
    assert "SHOULD NOT trigger" not in goal


def test_system_goal_self_check_section_present_with_retrieval() -> None:
    goal = _system_goal(
        "ecommerce",
        2,
        memory_enabled=True,
        baseline_enabled=True,
    )
    assert "Optional helpers" in goal
    # Retrieval sections still render alongside.
    assert "search_personal_skills" in goal
    assert "search_industry_baselines" in goal
