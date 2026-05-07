"""End-to-end graph tests for `app.agents.policy_extract_agent`.

PR-B scope (PLAN_13 §6) — runs the compiled langgraph against a
sequenced stub backend that returns different JSON per call so each
scenario exercises a specific termination branch.

The four termination paths under test:

  - **converge** — first extraction is good, no retry
  - **max_iter_exhausted** — every retry surfaces empty drafts +
    policy keywords, so eval keeps returning retry until budget runs
  - **no_change** — reflect would feed the same hint a second time
    (eval emits identical coverage_concerns), so the agent bails
  - **rule-3 converge** — drafts identical between iter 1 and iter 2
    (different hint, same model output), so the no-improvement rule
    in eval.py terminates instead of looping further

Tests deliberately don't import `pytest_asyncio` — `asyncio_mode = auto`
in pytest.ini handles the fixture.
"""
from __future__ import annotations

import json
from typing import Any

from app.agents.policy_extract_agent import build_agent
from app.agents.state import AgentState


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


def _payload(*candidates: dict[str, Any]) -> str:
    return json.dumps({"candidates": list(candidates)})


class _SequencedBackend:
    """Returns the next response from `responses` on each `complete` call.

    Exhausting the list raises — that's intentional, so a test that
    expects 2 calls but the agent makes 3 fails loudly instead of
    silently looping on the last response.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0
        # Captures the system prompt of each call so tests can assert
        # reflect's hint actually reached the model on retries.
        self.systems: list[str] = []

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        if self.calls >= len(self._responses):
            raise AssertionError(
                f"backend ran out of responses at call #{self.calls + 1} — "
                f"agent looped further than the test expected"
            )
        out = self._responses[self.calls]
        self.systems.append(system)
        self.calls += 1
        return out


# --- converge on first pass ----------------------------------------------


async def test_converge_on_first_extract() -> None:
    backend = _SequencedBackend([_payload(_candidate())])
    agent = build_agent(backend)

    out = await agent.ainvoke(
        AgentState(chunk="Refunds over $500 must be approved.")
    )

    assert backend.calls == 1
    assert out["terminated"] is True
    assert out["reason"] == "converge"
    assert len(out["iterations"]) == 1
    iter1 = out["iterations"][0]
    assert iter1.eval is not None
    assert iter1.eval.decision == "converge"
    assert iter1.prompt_hint == ""
    # No "Previous pass" leakage on iter 1.
    assert "## Previous pass" not in backend.systems[0]


# --- retry then converge -------------------------------------------------


async def test_retry_then_converge_propagates_hint_to_iter_2() -> None:
    """Iter 1: empty drafts on a chunk with policy keywords → eval rule
    1 retries. Iter 2: backend returns a candidate → eval converges.
    """
    backend = _SequencedBackend(
        [
            _payload(),  # empty
            _payload(_candidate()),  # one solid candidate
        ]
    )
    agent = build_agent(backend)

    out = await agent.ainvoke(
        AgentState(
            chunk="All purchase orders over $1000 shall require VP approval.",
            max_iter=2,
        )
    )

    assert backend.calls == 2
    assert out["terminated"] is True
    assert out["reason"] == "converge"
    assert len(out["iterations"]) == 2

    # Iter 2's system prompt MUST include the "## Previous pass" section
    # populated from iter 1's coverage_concerns. If reflect's hint never
    # reached extract, the reflective behavior is a no-op.
    assert "## Previous pass" not in backend.systems[0]
    assert "## Previous pass" in backend.systems[1]
    assert "policy-imperative" in backend.systems[1]


# --- max_iter exhausted --------------------------------------------------


async def test_max_iter_exhausted_when_retries_dont_help() -> None:
    """Iter 1 empty (rule 1 retry) → iter 2 returns one ambiguous draft
    (rule 2 retry) → max_iter=2 reached → terminate with
    `max_iter_exhausted`. Drafts must differ between iterations so the
    no-improvement rule (rule 3) doesn't short-circuit to converge.
    """
    backend = _SequencedBackend(
        [
            _payload(),  # iter 1: empty → rule 1 retry
            _payload(  # iter 2: ambiguous candidate → rule 2 retry
                _candidate(
                    name="vague-policy",
                    needs_clarification=True,
                    clarification_hint="What exactly is required?",
                )
            ),
        ]
    )
    agent = build_agent(backend)

    out = await agent.ainvoke(
        AgentState(
            chunk="Returns must be approved by a manager.",
            max_iter=2,
        )
    )

    assert backend.calls == 2
    assert out["terminated"] is True
    assert out["reason"] == "max_iter_exhausted"
    assert len(out["iterations"]) == 2
    # Both iterations carry the retry decision (no convergence happened).
    for it in out["iterations"]:
        assert it.eval is not None
        assert it.eval.decision == "retry"


# --- no_change termination -----------------------------------------------


async def test_no_change_terminates_when_reflect_would_repeat_hint() -> None:
    """Reflect's no-progress shortcut: if the new hint would equal the
    one already applied last iteration, terminate before burning
    another extract call.

    Setup: both iter 1 and iter 2 return a candidate with the same
    `(name, clarification_hint)` but DIFFERENT `(condition, action)`.
    That means:
      - rule 3 (drafts identical) does NOT fire (condition/action vary)
      - rule 2 (all clarification) DOES fire on both iters
      - both iters' coverage_concerns format to the same string
        ("- name: hint")
      - iter 2's prompt_hint is exactly that string (from iter 1's
        reflect output), so reflect on iter 2 detects the repeat and
        bails with reason=no_change instead of running iter 3.
    """
    backend = _SequencedBackend(
        [
            _payload(
                _candidate(
                    name="ambiguous-rule",
                    condition="cond-v1",
                    action="act-v1",
                    needs_clarification=True,
                    clarification_hint="What exactly counts?",
                )
            ),
            _payload(
                _candidate(
                    name="ambiguous-rule",  # same name + hint
                    condition="cond-v2",  # different so rule 3 skips
                    action="act-v2",
                    needs_clarification=True,
                    clarification_hint="What exactly counts?",
                )
            ),
        ]
    )
    agent = build_agent(backend)

    out = await agent.ainvoke(
        AgentState(
            chunk="Be careful with sensitive data.",
            max_iter=3,  # would allow iter 3, but reflect bails first
        )
    )

    assert backend.calls == 2
    assert out["terminated"] is True
    assert out["reason"] == "no_change"
    assert len(out["iterations"]) == 2


# --- max_iter=1 single-pass mode -----------------------------------------


async def test_max_iter_1_runs_a_single_pass() -> None:
    """`max_iter=1` mirrors the non-reflective single-shot path —
    self_eval terminates immediately even on a retry decision.
    """
    backend = _SequencedBackend([_payload()])
    agent = build_agent(backend)

    out = await agent.ainvoke(
        AgentState(
            chunk="Refunds must be approved by a manager.",
            max_iter=1,
        )
    )

    assert backend.calls == 1
    assert out["terminated"] is True
    # rule 1 says retry, but max_iter=1 means we terminate immediately.
    assert out["reason"] == "max_iter_exhausted"
    assert len(out["iterations"]) == 1


# --- rule-3 no-improvement converge --------------------------------------


async def test_rule_3_drafts_identical_converges() -> None:
    """Iter 1 returns ambiguous drafts (rule 2 retry). Iter 2 returns
    the same drafts despite the hint. Eval rule 3 fires → converge.
    """
    same_drafts = _payload(
        _candidate(
            name="vague-pii",
            needs_clarification=True,
            clarification_hint="What counts as PII?",
        )
    )
    backend = _SequencedBackend([same_drafts, same_drafts])
    agent = build_agent(backend)

    out = await agent.ainvoke(
        AgentState(
            chunk="Be careful with PII when handling customer records.",
            max_iter=2,
        )
    )

    assert backend.calls == 2
    assert out["terminated"] is True
    assert out["reason"] == "converge"
    iter2 = out["iterations"][1]
    assert iter2.eval is not None
    assert iter2.eval.decision == "converge"
    assert "no progress" in iter2.eval.rationale.lower()


# --- prompt_hint plumbing through service --------------------------------


async def test_prompt_hint_appears_in_extract_call_system_prompt() -> None:
    """Belt-and-suspenders: confirm reflect's bullet-formatted hint
    actually flows through `services.policy_extract._system_prompt` and
    appears verbatim in the second call's system message.
    """
    backend = _SequencedBackend(
        [
            _payload(),  # empty → retry via rule 1
            _payload(_candidate()),  # converge
        ]
    )
    agent = build_agent(backend)

    await agent.ainvoke(
        AgentState(
            chunk="All vendor invoices must be approved by procurement.",
            max_iter=2,
        )
    )

    iter2_system = backend.systems[1]
    # The hint format is "- <concern>" per _format_hint
    assert "- the chunk uses policy-imperative language" in iter2_system
    # And the section header from policy_extract._system_prompt
    assert "## Previous pass" in iter2_system


