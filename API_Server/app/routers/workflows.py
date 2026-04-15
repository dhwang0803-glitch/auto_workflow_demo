"""Workflow CRUD router — PLAN_02 (refactor: DomainError auto-mapping).

Thin HTTP adapter over `WorkflowService`. Errors raised by the service
bubble up to the global `DomainError` handler in `app.main` — no
try/except or status-code tables here.
"""
from __future__ import annotations

from uuid import UUID

from auto_workflow_database.repositories.base import User

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import Response

from app.dependencies import get_current_user
from app.models.workflow import (
    WorkflowCreate,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowUpdate,
)
from app.services.workflow_service import WorkflowService

router = APIRouter()


def get_workflow_service(request: Request) -> WorkflowService:
    return request.app.state.workflow_service


@router.post(
    "",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow(
    body: WorkflowCreate,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    wf = await svc.create(user, body)
    return WorkflowResponse.model_validate(wf)


@router.get("", response_model=WorkflowListResponse)
async def list_workflows(
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowListResponse:
    return await svc.list_for_user(user)


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    wf = await svc.get_owned(user, workflow_id)
    return WorkflowResponse.model_validate(wf)


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: UUID,
    body: WorkflowUpdate,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    wf = await svc.update(user, workflow_id, body)
    return WorkflowResponse.model_validate(wf)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> Response:
    await svc.soft_delete(user, workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
