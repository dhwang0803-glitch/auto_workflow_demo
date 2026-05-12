"""PLAN_14 PR-D + PR-E — pydantic shapes for the personalization agent.

PR-D introduced the agent's output shapes (proposal / judgment /
outcome). PR-E extended the file with the HTTP request/response wire
shapes for `/v1/personalization/extract_from_diff`. Keeping both in
one module mirrors `models/agents.py` where the policy_extract route's
wire shapes sit next to the agent-state shapes.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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


class PersonalizationExtractRequest(BaseModel):
    """POST body for /v1/personalization/extract_from_diff.

    `v1` and `v2` are workflow payloads in the same shape API_Server's
    `WorkflowGraph` serializes to (`{"nodes": [...], "edges": [...]}`,
    matching `workflow_revisions.payload` JSONB). We validate `nodes`
    is a list to reject obvious malformed input at the route boundary;
    the diff function itself is lenient on missing fields per node and
    won't crash, but a request with `nodes: null` is almost certainly
    a caller bug.

    `rejected_hashes` carries the caller's user-scoped record of
    suggestion_hash strings the user has previously dismissed. Passing
    them in (rather than having AI_Agent look them up) keeps this
    endpoint stateless — Database access stays on the API_Server side
    per `AI_Agent/CLAUDE.md`.

    `user_id` is plumbed for logging only; the agent's behavior is
    identical regardless of caller, and user-scoping happens entirely
    via `rejected_hashes`.
    """

    v1: dict[str, Any] = Field(...)
    v2: dict[str, Any] = Field(...)
    rejected_hashes: list[str] = Field(default_factory=list)
    user_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _check_payload_shape(self) -> "PersonalizationExtractRequest":
        for name, payload in (("v1", self.v1), ("v2", self.v2)):
            nodes = payload.get("nodes")
            if not isinstance(nodes, list):
                raise ValueError(f"{name}.nodes must be a list")
        return self


class PersonalizationExtractResponse(BaseModel):
    """Response body for /v1/personalization/extract_from_diff.

    `outcome` carries the propose+judge result the caller (API_Server
    PR-G) uses to decide whether to persist a candidate row.

    `diff` is `WorkflowDiff.to_dict()` — surfaced on the wire so PR-G
    / Frontend can show "what changed" without re-running the diff in
    a second process. The payload is bounded by the workflow size,
    which is already capped by API_Server's WorkflowGraph validation.

    `diff_signature` is the same string used inside `suggestion_hash`
    — caller can inspect it for ad-hoc dedupe without recomputing.

    `langsmith_run_id` is the UUID the route minted for the run when
    LangSmith tracing was active at request time; clients paste it
    into the LangSmith UI search the same way `/v1/policy/extract_reflective`
    does.
    """

    outcome: PersonalizationOutcome
    diff: dict[str, Any]
    diff_signature: str
    langsmith_run_id: str | None = None


__all__ = [
    "DropReason",
    "PersonalizationExtractRequest",
    "PersonalizationExtractResponse",
    "PersonalizationJudgment",
    "PersonalizationOutcome",
    "PersonalizationProposal",
]
