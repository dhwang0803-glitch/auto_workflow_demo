"""Execution history router — PLAN_03."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from auto_workflow_database.repositories.base import User

from fastapi import APIRouter, Depends, Query, Request

from app.dependencies import get_current_user
from app.models.execution import ExecutionListResponse, ExecutionResponse
from app.services.workflow_service import WorkflowService

router = APIRouter()


def get_workflow_service(request: Request) -> WorkflowService:
    return request.app.state.workflow_service


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: UUID,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> ExecutionResponse:
    ex = await svc.get_execution(user, execution_id)
    return ExecutionResponse.model_validate(ex)


@router.get(
    "/by-workflow/{workflow_id}",
    response_model=ExecutionListResponse,
)
async def list_executions(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ExecutionListResponse:
    parsed_cursor = None
    if cursor:
        ts_str, id_str = cursor.rsplit("_", 1)
        parsed_cursor = (datetime.fromisoformat(ts_str), UUID(id_str))
    executions = await svc.list_executions(
        user, workflow_id, limit=limit + 1, cursor=parsed_cursor
    )
    has_more = len(executions) > limit
    items = executions[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = f"{last.created_at.isoformat()}_{last.id}"
    return ExecutionListResponse(
        items=[ExecutionResponse.model_validate(e) for e in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )
