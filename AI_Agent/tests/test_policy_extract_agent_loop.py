"""End-to-end tests for `app.agents.policy_extract_agent.run_policy_extract_agent`.

PR-β replaced the langgraph StateGraph with a ReAct agent loop. The
public input/output is the same (chunk → drafts + iterations + reason),
so these tests target the contract on that boundary plus the four
termination paths that matter on the wire:

  - **converge** — extract → evaluate(converge) → finish
  - **retry-then-converge** — extract → evaluate(retry, deterministic)
    → extract(with hint) → evaluate(converge) → finish
  - **judge flips converge→retry** — extract → evaluate(rule converge,
    judge flags missing) → extract → evaluate(converge) → finish
  - **first-extract parse error** — propagates as PolicyExtractParseError

The test backend (`_SequencedBackend`) dispatches by system-prompt
prefix into three buckets, since the agent loop, the extractor, and
the LLM judge each carry a distinct opening line. Per-bucket call
counts let assertions verify "extract was called exactly N times" or
"the judge ran once" without bookkeeping in the test body.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents.policy_extract_agent import run_policy_extract_agent
from app.services.policy_extract import PolicyExtractParseError


# Prompt prefixes used to dispatch in the sequenced backend. Match the
# first sentence of each system prompt verbatim so a wording polish in
# any of the three callers fails the test loudly instead of silently
# misrouting.
_AGENT_PROMPT_PREFIX = "You are an extraction agent"
_EXTRACT_PROMPT_PREFIX = "You are the policy extractor"
_JUDGE_PROMPT_PREFIX = "You are a critic for a policy-extraction step"


def _candidate(
    name: str = "approve-large-refunds",
    *,
    condition: str = "Refunds over $500",
    action: str = "Escalate to manager",
    needs_clarification: bool = False,
    clarification_hint: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} description",
        "condition": condition,
        "action": action,
        "rationale": "Drawn from the chunk",
        "needs_clarification": needs_clarification,
        "clarification_hint": clarification_hint,
    }


def _extract_payload(*candidates: dict[str, Any]) -> str:
    """Wraps candidates into the `{candidates: [...]}` shape that
    `services.policy_extract` parses out of the LLM body.
    """
    return json.dumps({"candidates": list(candidates)})


def _agent_call(name: str, args: dict | None = None) -> str:
    """Format an agent decision turn — one `<tool_call>` block."""
    body = json.dumps(args or {})
    return f"<tool_call name=\"{name}\">\n{body}\n</tool_call>"


def _agent_finish(drafts: list[dict[str, Any]]) -> str:
    return f"<finish>\n{json.dumps({'drafts': drafts})}\n</finish>"


class _SequencedBackend:
    """Sequenced responses dispatched into three buckets by system prompt.

    Every call increments `total_calls`; each bucket also has its own
    counter (`agent_calls`, `extract_calls`, `judge_calls`) so tests
    can assert on a single bucket without bookkeeping. Exhausting any
    queue raises a clear assertion — silent fall-through to a default
    response made it too easy to miss "the agent looped further than
    expected" failures in PR-β development.
    """

    def __init__(
        self,
        *,
        agent: list[str] | None = None,
        extract: list[str] | None = None,
        judge: list[str] | None = None,
    ) -> None:
        self._agent = list(agent or [])
        self._extract = list(extract or [])
        self._judge = list(judge or [])
        self.agent_calls = 0
        self.extract_calls = 0
        self.judge_calls = 0
        self.total_calls = 0
        self.last_extract_images: list[str] | None = None

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        del user_message, max_tokens
        self.total_calls += 1

        if system.startswith(_AGENT_PROMPT_PREFIX):
            if self.agent_calls >= len(self._agent):
                raise AssertionError(
                    f"agent backend exhausted at agent_calls={self.agent_calls}"
                )
            out = self._agent[self.agent_calls]
            self.agent_calls += 1
            return out

        if system.startswith(_EXTRACT_PROMPT_PREFIX):
            if self.extract_calls >= len(self._extract):
                raise AssertionError(
                    f"extract backend exhausted at extract_calls={self.extract_calls}"
                )
            out = self._extract[self.extract_calls]
            self.last_extract_images = images
            self.extract_calls += 1
            return out

        if system.startswith(_JUDGE_PROMPT_PREFIX):
            # Default judge response when none queued: nothing missing.
            if self.judge_calls < len(self._judge):
                out = self._judge[self.judge_calls]
            else:
                out = '{"missed": []}'
            self.judge_calls += 1
            return out

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


# --- happy path: extract → evaluate(converge) → finish --------------------


@pytest.mark.asyncio
async def test_converge_in_one_iteration() -> None:
    cand = _candidate()
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_finish([cand]),
        ],
        extract=[_extract_payload(cand)],
    )

    iterations, terminated, reason, finals = await run_policy_extract_agent(
        backend,
        chunk="Refunds over $500 must be approved by a manager.",
        domain="ecommerce",
    )

    assert terminated is True
    assert reason == "converge"
    assert len(iterations) == 1
    assert iterations[0].eval is not None
    assert iterations[0].eval.decision == "converge"
    assert iterations[0].prompt_hint == ""
    assert len(finals) == 1
    assert finals[0].name == "approve-large-refunds"

    # One extraction, no judge calls (rule-1/2 didn't trigger; rule-3
    # is iter-2-only; default rule path returned converge → judge skipped
    # because we passed no judge_backend).
    assert backend.extract_calls == 1


# --- retry-then-converge: deterministic rule 1 (empty + keywords) ---------


@pytest.mark.asyncio
async def test_retry_then_converge_via_rule_1() -> None:
    """Iter 1 returns empty drafts on a chunk with policy keywords →
    `evaluate_coverage` rule 1 returns retry → agent re-calls
    `extract_policies` with a hint → returns a real candidate → converge.
    """
    cand = _candidate()
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),               # turn 1
            _agent_call("evaluate_coverage"),              # turn 2
            _agent_call(                                    # turn 3 (retry)
                "extract_policies",
                {"hint": "policy-imperative chunk had no candidates"},
            ),
            _agent_call("evaluate_coverage"),              # turn 4
            _agent_finish([cand]),                          # turn 5
        ],
        extract=[
            _extract_payload(),       # iter 1 — empty
            _extract_payload(cand),   # iter 2 — recovers
        ],
    )

    iterations, _terminated, reason, finals = await run_policy_extract_agent(
        backend,
        chunk="All purchase orders over $1000 shall require approval.",
        max_iter=2,
    )

    assert reason == "converge"
    assert len(iterations) == 2
    assert iterations[0].drafts == []
    assert iterations[0].eval is not None
    assert iterations[0].eval.decision == "retry"
    # Iter 2 carries the hint the agent passed.
    assert iterations[1].prompt_hint != ""
    assert iterations[1].eval is not None
    assert iterations[1].eval.decision == "converge"
    assert len(finals) == 1
    assert backend.extract_calls == 2


# --- judge flips converge → retry on iter 1 ------------------------------


@pytest.mark.asyncio
async def test_judge_flips_iter1_converge_to_retry() -> None:
    """Iter 1 has a candidate → deterministic rules say converge → judge
    backend wired → judge flags a missed policy → eval becomes retry →
    iter 2 extract recovers → converge.
    """
    cand1 = _candidate(name="rule_a", condition="A", action="X")
    cand2 = _candidate(name="rule_b", condition="B", action="Y")
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call(
                "extract_policies", {"hint": "B condition not captured"}
            ),
            _agent_call("evaluate_coverage"),
            _agent_finish([cand1, cand2]),
        ],
        extract=[
            _extract_payload(cand1),
            _extract_payload(cand1, cand2),
        ],
        judge=[
            '{"missed": ["B condition not captured"]}',
            # iter 2 judge skipped because it's the LAST iteration —
            # `policy_extract_agent` skips judge on `completed >= max_iter`
        ],
    )

    iterations, _terminated, reason, finals = await run_policy_extract_agent(
        backend,
        chunk="If A then X. If B then Y.",
        max_iter=2,
        judge_backend=backend,  # same backend powers both
    )

    assert reason == "converge"
    assert len(iterations) == 2
    # Iter 1 verdict was flipped by the judge.
    assert iterations[0].eval is not None
    assert iterations[0].eval.decision == "retry"
    assert "B condition" in iterations[0].eval.coverage_concerns[0]
    assert "judge flagged" in iterations[0].eval.rationale
    assert backend.judge_calls == 1
    # Iter 2 did NOT call the judge (last iter — budget elision).
    assert len(finals) == 2


# --- boilerplate chunk: extract returns 0, agent finishes with [] ---------


@pytest.mark.asyncio
async def test_boilerplate_chunk_finishes_empty() -> None:
    """No policy keywords + empty drafts → rule 1 returns converge with
    no concerns → agent finishes with `[]`. Mirrors the GitLab handbook
    chunks 0/22 (page header / footer) recall=0 narrative.
    """
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_finish([]),
        ],
        extract=[_extract_payload()],
    )

    iterations, terminated, reason, finals = await run_policy_extract_agent(
        backend,
        chunk="Welcome to the team handbook. Last edited 2026-01-01.",
    )

    assert terminated is True
    assert reason == "converge"
    assert len(iterations) == 1
    assert iterations[0].drafts == []
    assert finals == []


# --- first-extract parse error propagates --------------------------------


@pytest.mark.asyncio
async def test_first_extraction_parse_error_propagates() -> None:
    """Same envelope as `/v1/policy/extract` — when the FIRST extraction
    can't parse, route handler returns 502 (tested in route file). Here
    we just verify the exception surfaces from `run_policy_extract_agent`
    so the route's `except PolicyExtractParseError` clause keeps firing.
    """
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            # Agent never gets a chance to act on the obs because the
            # exception propagates out of run_policy_extract_agent
            # *after* the agent_loop returns. The agent_loop itself
            # catches the exception in the dispatcher and turns it
            # into an obs (see agent_loop.py "tools are user code"),
            # so it would normally terminate via parse_error or
            # max_iter. We provide enough agent turns for either.
            _agent_finish([]),
        ],
        extract=["this is not json at all"],
    )

    with pytest.raises(PolicyExtractParseError):
        await run_policy_extract_agent(
            backend,
            chunk="anything",
        )


# --- max_iter=1: single-pass equivalence ---------------------------------


@pytest.mark.asyncio
async def test_max_iter_1_single_pass() -> None:
    """With max_iter=1 the judge gate (`completed < max_iter`) ensures
    the judge backend is NOT invoked even if wired — burning budget on
    a verdict the agent can't act on would be wasted.
    """
    cand = _candidate()
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_finish([cand]),
        ],
        extract=[_extract_payload(cand)],
    )

    iterations, _t, reason, finals = await run_policy_extract_agent(
        backend,
        chunk="Refunds must be approved.",
        max_iter=1,
        judge_backend=backend,
    )

    assert reason == "converge"
    assert len(iterations) == 1
    assert backend.extract_calls == 1
    assert backend.judge_calls == 0  # judge correctly skipped on last iter
    assert len(finals) == 1


# --- finish without preceding evaluate: in_flight is preserved -----------


@pytest.mark.asyncio
async def test_finish_without_evaluate_records_inflight() -> None:
    """If the agent skips evaluate_coverage and goes straight to finish
    after extract, we still want the iteration in the trace — it
    happened, it just lacks a formal eval verdict. We synthesize a
    placeholder converge so the trace stays well-formed.
    """
    cand = _candidate()
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_finish([cand]),  # straight to finish, skipping evaluate
        ],
        extract=[_extract_payload(cand)],
    )

    iterations, _t, reason, _f = await run_policy_extract_agent(
        backend,
        chunk="Anything.",
    )

    assert reason == "converge"
    assert len(iterations) == 1
    assert iterations[0].drafts[0].name == "approve-large-refunds"
    assert iterations[0].eval is not None
    assert iterations[0].eval.decision == "converge"
    assert "without evaluating" in iterations[0].eval.rationale


# --- images forwarded into the extract backend call ----------------------


@pytest.mark.asyncio
async def test_images_forwarded_to_extract_call() -> None:
    """The `images` field carries through the agent layer and lands on
    the `backend.complete(images=...)` keyword the extractor uses.
    Phase D's smoke depends on this.
    """
    cand = _candidate()
    img = "data:image/png;base64,iVBORw0KGgoAAAA="
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_finish([cand]),
        ],
        extract=[_extract_payload(cand)],
    )

    await run_policy_extract_agent(
        backend,
        chunk="Refunds must be approved.",
        images=[img],
    )

    # `last_extract_images` is set only when an extract-prefix call
    # arrives (the agent-loop call doesn't pass images), so this
    # assertion isolates the contract.
    assert backend.last_extract_images == [img]
