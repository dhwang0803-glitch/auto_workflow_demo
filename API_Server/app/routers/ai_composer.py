"""AI Composer router (PLAN_02 §4) — non-stream half.

The streaming branch (`?stream=true`) lands in PR B together with Redis-backed
session storage. For now the endpoint returns the full ComposeResponse JSON
in a single round trip, which the Frontend's PR C ChatPanel consumes.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from auto_workflow_database.repositories.base import User
from fastapi import APIRouter, Depends, Query

from app.dependencies import get_ai_composer_service, get_current_user
from app.models.ai_composer import ComposeRequest, ComposeResponse
from app.services.ai_composer_service import AIComposerService

router = APIRouter()


@router.post("/compose", response_model=ComposeResponse)
async def compose(
    payload: ComposeRequest,
    stream: bool = Query(default=False),
    user: User = Depends(get_current_user),
    service: AIComposerService = Depends(get_ai_composer_service),
) -> ComposeResponse:
    # PR B will branch here on `stream=true` to return a StreamingResponse.
    # PR A only handles JSON-once; tell callers explicitly so they don't
    # silently get wrong wire shape.
    if stream:
        from app.errors import DomainError

        class _NotImplementedYet(DomainError):
            http_status = 501

        raise _NotImplementedYet(
            "SSE streaming lands in PLAN_02 PR B; call with stream=false"
        )

    current_dag = (
        payload.current_dag.model_dump() if payload.current_dag else None
    )
    result = await service.compose(
        user_id=user.id,
        message=payload.message,
        current_dag=current_dag,
    )
    return ComposeResponse(
        session_id=payload.session_id or uuid4(),
        result=result,
    )
