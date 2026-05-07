"""Unit tests for `app.agents.judge`.

PR-D scope. The judge is the only LLM-touching node we add in PR-D, so
its parser + prompt invariants live here. The graph-side wiring (judge
flips converge → retry, judge skipped on retry decisions, judge skipped
on the last allowed iteration) is exercised in
`test_policy_extract_agent_graph.py` via the same _SequencedBackend
shape PR-A/PR-B already use.
"""
from __future__ import annotations

import json

import pytest

from app.agents.judge import JudgeParseError, judge_extraction
from app.models.skills import SkillDraft


class _StaticBackend:
    """Returns a fixed string for every `complete` call. Records the
    user_message so prompt-formatting invariants can be asserted.
    """

    def __init__(self, response: str) -> None:
        self.response = response
        self.last_system: str = ""
        self.last_user: str = ""
        self.last_max_tokens: int | None = None

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        self.last_system = system
        self.last_user = user_message
        self.last_max_tokens = max_tokens
        return self.response


def _draft(name: str, *, condition: str = "cond", action: str = "act") -> SkillDraft:
    return SkillDraft(name=name, condition=condition, action=action)


# --- happy path ----------------------------------------------------------


async def test_returns_empty_list_when_judge_finds_nothing_missing() -> None:
    backend = _StaticBackend('{"missed": []}')
    out = await judge_extraction(backend, "any chunk", [_draft("p1")])
    assert out == []


async def test_returns_concerns_in_order() -> None:
    backend = _StaticBackend(
        json.dumps({"missed": ["alpha gap", "beta gap", "gamma gap"]})
    )
    out = await judge_extraction(backend, "any chunk", [_draft("p1")])
    assert out == ["alpha gap", "beta gap", "gamma gap"]


async def test_concern_list_is_capped_at_max() -> None:
    """A judge that returns 30 bullets is hallucinating; we cap to
    keep reflect's prompt from getting flooded.
    """
    raw = json.dumps({"missed": [f"concern-{i}" for i in range(20)]})
    backend = _StaticBackend(raw)
    out = await judge_extraction(backend, "any chunk", [_draft("p1")])
    # _MAX_CONCERNS is 5 in app.agents.judge — assert via length not
    # constant import so the test stays portable to small adjustments.
    assert len(out) == 5
    assert out[0] == "concern-0"
    assert out[-1] == "concern-4"


async def test_skips_blank_and_non_string_concerns() -> None:
    backend = _StaticBackend(
        json.dumps(
            {
                "missed": [
                    "valid",
                    "",  # whitespace stripped → drop
                    None,  # non-string → skip
                    {"oops": "object"},  # non-string → skip
                    "another valid",
                ]
            }
        )
    )
    out = await judge_extraction(backend, "any chunk", [_draft("p1")])
    assert out == ["valid", "another valid"]


# --- prompt invariants ---------------------------------------------------


async def test_prompt_carries_chunk_text_and_candidate_summary() -> None:
    backend = _StaticBackend('{"missed": []}')
    drafts = [
        _draft("approve-large-refunds", condition="over $500", action="escalate"),
        _draft("ship-international", condition="EU customer", action="14-day return"),
    ]
    chunk = "Refunds over $500 must be approved."

    await judge_extraction(backend, chunk, drafts)

    # Chunk wraps in fenced block so the model can clearly distinguish
    # the input chunk from the candidate summary.
    assert "```" in backend.last_user
    assert chunk in backend.last_user
    # Each candidate appears with name + condition + action.
    assert "approve-large-refunds" in backend.last_user
    assert "over $500" in backend.last_user
    assert "ship-international" in backend.last_user


async def test_prompt_handles_no_candidate_case_gracefully() -> None:
    backend = _StaticBackend('{"missed": []}')
    await judge_extraction(backend, "Any chunk text.", drafts=[])

    assert "(no candidates produced)" in backend.last_user


async def test_max_tokens_is_short_critique_budget() -> None:
    backend = _StaticBackend('{"missed": []}')
    await judge_extraction(backend, "any chunk", [_draft("p1")])
    # Long judges are hallucinating — keep budget tight.
    assert backend.last_max_tokens == 256


# --- parser robustness ---------------------------------------------------


async def test_rejects_response_without_missed_key() -> None:
    backend = _StaticBackend('{"oops": []}')
    with pytest.raises(JudgeParseError, match="missed"):
        await judge_extraction(backend, "any chunk", [_draft("p1")])


async def test_rejects_response_with_non_list_missed() -> None:
    backend = _StaticBackend('{"missed": "not a list"}')
    with pytest.raises(JudgeParseError, match="must be a list"):
        await judge_extraction(backend, "any chunk", [_draft("p1")])


async def test_tolerates_json_fence() -> None:
    backend = _StaticBackend('```json\n{"missed": ["x"]}\n```')
    out = await judge_extraction(backend, "any chunk", [_draft("p1")])
    assert out == ["x"]


async def test_rejects_unparseable_response() -> None:
    backend = _StaticBackend("not json at all")
    with pytest.raises(JudgeParseError):
        await judge_extraction(backend, "any chunk", [_draft("p1")])
