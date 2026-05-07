"""Rule-by-rule tests for `app.agents.eval.evaluate`.

The three deterministic rules + the fall-through default. Each rule has
a positive case (rule fires) and at least one negative case (rule does
NOT fire when something close looks similar).
"""
from __future__ import annotations

from app.agents.eval import evaluate
from app.agents.state import AgentIteration, EvalReport
from app.models.skills import SkillDraft


def _d(
    name: str,
    *,
    condition: str = "cond",
    action: str = "act",
    needs_clarification: bool = False,
    clarification_hint: str = "",
) -> SkillDraft:
    return SkillDraft(
        name=name,
        condition=condition,
        action=action,
        needs_clarification=needs_clarification,
        clarification_hint=clarification_hint,
    )


def _prior(*drafts: SkillDraft) -> list[AgentIteration]:
    return [
        AgentIteration(
            drafts=list(drafts),
            eval=EvalReport(decision="retry", rationale="prior"),
        )
    ]


# --- Rule 3: no-improvement converge --------------------------------------


def test_drafts_unchanged_from_prior_iteration_converges() -> None:
    drafts = [_d("a"), _d("b")]
    report = evaluate("any chunk", drafts, _prior(*drafts))

    assert report.decision == "converge"
    assert "no progress" in report.rationale.lower()


def test_draft_reorder_alone_is_not_a_change() -> None:
    """Reorder doesn't represent the agent finding new policies."""
    a, b = _d("a"), _d("b")
    report = evaluate("any chunk", [b, a], _prior(a, b))
    assert report.decision == "converge"


def test_one_new_draft_breaks_no_improvement_rule() -> None:
    a, b = _d("a"), _d("b")
    report = evaluate("any chunk", [a, b, _d("c")], _prior(a, b))
    # With a real change, the no-improvement rule does NOT fire — the
    # function falls through to its default converge (no judge in PR-A).
    assert report.decision == "converge"
    assert "no progress" not in report.rationale.lower()


# --- Rule 1: empty drafts + policy keywords -------------------------------


def test_empty_drafts_with_policy_keyword_triggers_retry() -> None:
    chunk = "Refunds over $500 must be approved by a manager."
    report = evaluate(chunk, drafts=[], prior_iterations=[])

    assert report.decision == "retry"
    assert report.coverage_concerns
    assert "policy-imperative" in report.coverage_concerns[0]


def test_empty_drafts_without_policy_keywords_converges() -> None:
    """A genuinely policy-free chunk (org structure / contact list)
    legitimately produces zero candidates — no retry.
    """
    chunk = "Our team consists of five engineers based in Seoul."
    report = evaluate(chunk, drafts=[], prior_iterations=[])

    assert report.decision == "converge"


def test_policy_keyword_match_is_case_insensitive() -> None:
    chunk = "ORDERS OVER $1000 SHALL ESCALATE TO LEGAL."
    report = evaluate(chunk, drafts=[], prior_iterations=[])
    assert report.decision == "retry"


def test_word_boundary_avoids_substring_false_positives() -> None:
    """'mustard' contains 'must' — but it isn't a policy keyword. The
    \\b boundaries in the regex prevent that false positive.
    """
    chunk = "We sell mustard at the deli counter."
    report = evaluate(chunk, drafts=[], prior_iterations=[])
    assert report.decision == "converge"


# --- Rule 2: all drafts ambiguous -----------------------------------------


def test_all_drafts_needs_clarification_triggers_retry() -> None:
    drafts = [
        _d("pii", needs_clarification=True, clarification_hint="What counts as PII?"),
        _d("auth", needs_clarification=True, clarification_hint="Who approves?"),
    ]
    report = evaluate("chunk text", drafts, prior_iterations=[])

    assert report.decision == "retry"
    # The hints become the next iteration's prompt fuel.
    assert any("pii" in c.lower() for c in report.coverage_concerns)
    assert any("auth" in c.lower() for c in report.coverage_concerns)


def test_mix_of_clarification_and_clear_drafts_does_not_retry() -> None:
    """A single concrete draft is signal enough that the model isn't
    helpless — no retry even if siblings are flagged.
    """
    drafts = [
        _d("clear", needs_clarification=False),
        _d("vague", needs_clarification=True, clarification_hint="huh?"),
    ]
    report = evaluate("chunk text", drafts, prior_iterations=[])

    assert report.decision == "converge"


def test_all_clarification_without_hints_still_retries_with_generic_concern() -> None:
    drafts = [
        _d("a", needs_clarification=True),
        _d("b", needs_clarification=True),
    ]
    # Note: the SkillDraft validator allows empty hint when
    # needs_clarification is False; the policy_extract parser rejects
    # the True+empty combination, but a hand-built draft (e.g., a future
    # judge that mutates the draft) might still hit this branch — we
    # cover it here so the rule degrades gracefully.
    report = evaluate("chunk text", drafts, prior_iterations=[])

    assert report.decision == "retry"
    assert len(report.coverage_concerns) == 1
    assert "needs_clarification" in report.coverage_concerns[0]


# --- Default branch -------------------------------------------------------


def test_no_rule_fires_returns_converge_with_clear_rationale() -> None:
    drafts = [_d("clear-policy", needs_clarification=False)]
    report = evaluate(
        "Refunds over $500 must be approved.",
        drafts,
        prior_iterations=[],
    )

    assert report.decision == "converge"
    assert report.rationale == "deterministic rules cleared"
    assert report.coverage_concerns == []
