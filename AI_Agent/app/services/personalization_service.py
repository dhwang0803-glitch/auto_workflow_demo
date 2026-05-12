"""PLAN_14 PR-E — orchestration for /v1/personalization/extract_from_diff.

One thin function that wraps PR-C's diff and PR-D's agent. AI_Agent
does NOT write to the database — `AI_Agent/CLAUDE.md` keeps the
boundary strict: metering and persistence live in API_Server. PR-E's
response carries the agent outcome + diff so API_Server (PR-G) can
decide what to persist (candidate row, review queue, etc.).

Stateless on the request side too — the caller passes the
`rejected_hashes` it has on record for this user, so AI_Agent never
needs to know who is who. That makes the route trivially testable in
isolation and keeps the user-scoping invariant (PLAN_14 §4.6: "사용자
A 의 personal skill 이 사용자 B 검색 풀에 절대 들어가지 않음") under
API_Server's direct control rather than threaded through HTTP.
"""
from __future__ import annotations

import logging
from typing import Any

from app.agents.personalization_agent import (
    diff_signature,
    run_personalization_agent,
)
from app.backends.protocols import LLMBackend
from app.models.personalization import PersonalizationExtractResponse
from app.services.workflow_diff import diff_workflow

logger = logging.getLogger(__name__)


async def extract_personalization_from_diff(
    backend: LLMBackend,
    *,
    v1: dict[str, Any],
    v2: dict[str, Any],
    rejected_hashes: list[str] | None = None,
    user_id: str | None = None,
) -> PersonalizationExtractResponse:
    """Compute diff, run propose+judge, package the response.

    The route layer mints `langsmith_run_id` and stamps it after this
    call returns — keeping it out of the service signature means
    services don't depend on the global tracing env, and the function
    stays a pure-data orchestrator.
    """
    diff = diff_workflow(v1, v2)
    outcome = await run_personalization_agent(
        backend,
        diff,
        v1_payload=v1,
        rejected_hashes=rejected_hashes,
    )
    if user_id is not None and outcome.accepted:
        # Structured log line for observability — operators can grep
        # the Modal logs by user_id to see how often a given user is
        # producing candidates. The hash lets them dedupe in a query
        # without joining against the DB.
        logger.info(
            "personalization_candidate_accepted user_id=%s suggestion_hash=%s",
            user_id,
            outcome.suggestion_hash,
        )
    return PersonalizationExtractResponse(
        outcome=outcome,
        diff=diff.to_dict(),
        diff_signature=diff_signature(diff),
        langsmith_run_id=None,
    )


__all__ = ["extract_personalization_from_diff"]
