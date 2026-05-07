"""Deterministic self-evaluation rules for the policy_extract agent.

PR-A scope (PLAN_13 §6): rules-only. The LLM judge that would extend
`coverage_concerns` from a critic prompt lands in PR-D — the function
signature is shaped so that hook fits without rewriting callers.

The three rules (PLAN_13 §4.3):

  1. drafts empty AND chunk contains imperative policy keywords →
     retry. Reasoning: the model came back blank from a chunk that
     looks like a policy.

  2. drafts non-empty AND every draft has needs_clarification=True →
     retry. Reasoning: the model only emitted ambiguous candidates;
     a re-extract with the clarification hints surfaced may resolve
     them.

  3. iteration > 1 AND drafts identical to the previous iteration's →
     converge. Reasoning: reflect's hint did not change the output, so
     another loop will burn tokens without progress. The graph in PR-B
     marks `AgentState.reason = "no_change"` for this case.

When no rule fires the default is `converge` — without an LLM judge we
have nothing more to say, and a false-positive retry would just waste
a Modal call. PR-D's judge replaces that default with a real critique.
"""
from __future__ import annotations

import re

from app.agents.state import AgentIteration, EvalReport
from app.models.skills import SkillDraft

# Imperative / regulatory verbs that signal a chunk is *trying* to state
# a policy. Tight on purpose — broad words like "if" / "when" produce
# false positives on narrative prose. The handbook fixture's actual
# missed-policy chunks (Phase 0/1/2/3 sweep #8/#12/#15) all match at
# least one of these.
_POLICY_KEYWORD_RE = re.compile(
    r"\b(must|shall|require[ds]?|approval|approve[ds]?|escalat(?:e|ed|es|ion)|"
    r"prohibit(?:ed|s)?|forbidden|mandator(?:y|ily))\b",
    re.IGNORECASE,
)


def _has_policy_keywords(chunk: str) -> bool:
    return bool(_POLICY_KEYWORD_RE.search(chunk))


def _drafts_equal(a: list[SkillDraft], b: list[SkillDraft]) -> bool:
    """Two iterations produced the same drafts.

    Compared as sets of (name, condition, action) tuples so reorder
    alone doesn't count as a change — the agent's job is to find
    policies, not to preserve ordering.
    """
    if len(a) != len(b):
        return False
    key = lambda d: (d.name, d.condition, d.action)  # noqa: E731
    return sorted(map(key, a)) == sorted(map(key, b))


def evaluate(
    chunk: str,
    drafts: list[SkillDraft],
    prior_iterations: list[AgentIteration],
) -> EvalReport:
    """Apply the deterministic rules; return an EvalReport.

    `prior_iterations` is `state.iterations` AS OF entry to self_eval —
    NOT including the iteration currently being evaluated. The graph in
    PR-B is responsible for that sequencing.
    """
    # Rule 3 first: no-improvement is the cheapest signal and trumps
    # everything else (a retry that produces identical drafts means the
    # judge missed something or reflect's hint was inert).
    if prior_iterations and _drafts_equal(drafts, prior_iterations[-1].drafts):
        return EvalReport(
            decision="converge",
            rationale="drafts identical to previous iteration — no progress",
        )

    if not drafts and _has_policy_keywords(chunk):
        return EvalReport(
            decision="retry",
            coverage_concerns=[
                "the chunk uses policy-imperative language (must / shall / "
                "require / approve / escalate) but no candidates were "
                "extracted — re-read the chunk for condition+action pairs"
            ],
            rationale="empty drafts despite policy keywords in chunk",
        )

    if drafts and all(d.needs_clarification for d in drafts):
        hints = [
            f"{d.name}: {d.clarification_hint}"
            for d in drafts
            if d.clarification_hint
        ]
        concerns = hints or [
            "every draft is marked needs_clarification — re-extract using "
            "the chunk's concrete language for condition and action"
        ]
        return EvalReport(
            decision="retry",
            coverage_concerns=concerns,
            rationale="every draft is ambiguous",
        )

    return EvalReport(
        decision="converge",
        rationale="deterministic rules cleared",
    )
