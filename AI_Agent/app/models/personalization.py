"""PLAN_14 PR-D — pydantic shapes for the personalization propose+judge agent.

PR-E will add the HTTP request/response shapes on top of these. For
PR-D scope we only need the agent's output shapes — proposal, judgment,
and the combined outcome that PR-E's `personalization_service.py` will
write to the candidates table.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PersonalizationProposal(BaseModel):
    """Output of the propose LLM call.

    `is_noise=True` means the model judged the edit a one-off (label
    rename, typo fix); the caller drops the candidate without paying for
    a judge turn. `hint` is empty in that case.

    `raw` is the unparsed model output, kept so the LangSmith trace tree
    can show the raw payload alongside the parsed fields.
    """

    hint: str = ""
    is_noise: bool = False
    raw: str = ""

    @property
    def is_empty(self) -> bool:
        return self.is_noise or not self.hint.strip()


class PersonalizationJudgment(BaseModel):
    """Output of the judge LLM call."""

    decision: Literal["accept", "reject"]
    reason: str = ""
    raw: str = ""


DropReason = Literal[
    "",
    "empty_diff",
    "empty_proposal",
    "hash_previously_rejected",
    "judge_reject",
]


class PersonalizationOutcome(BaseModel):
    """Final result of one propose+judge run over a single workflow diff.

    `accepted=True` is the signal for PR-E to write a candidate row.
    `accepted=False` means drop; `drop_reason` says why (so PR-E can
    log it for observability without re-running the agent).

    `suggestion_hash` is set whenever the propose stage produced a
    non-empty hint — the hash identifies the (hint, diff_signature) pair
    so PR-E can dedupe across runs even when the judge rejects.
    """

    proposal: PersonalizationProposal
    judgment: PersonalizationJudgment | None = None
    accepted: bool = False
    drop_reason: DropReason = ""
    suggestion_hash: str | None = None


__all__ = [
    "DropReason",
    "PersonalizationJudgment",
    "PersonalizationOutcome",
    "PersonalizationProposal",
]
