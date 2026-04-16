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
from app.models.execution import ExecutionResponse
from app.models.workflow import (
    ActivateRequest,
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


@router.post(
    "/{workflow_id}/execute",
    response_model=ExecutionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_workflow(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> ExecutionResponse:
    ex = await svc.execute_workflow(user, workflow_id)
    return ExecutionResponse.model_validate(ex)


@router.post("/{workflow_id}/activate", response_model=WorkflowResponse)
async def activate_workflow(
    workflow_id: UUID,
    body: ActivateRequest,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    wf = await svc.activate_workflow(user, workflow_id, body)
    return WorkflowResponse.model_validate(wf)


@router.post("/{workflow_id}/deactivate", response_model=WorkflowResponse)
async def deactivate_workflow(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    wf = await svc.deactivate_workflow(user, workflow_id)
    return WorkflowResponse.model_validate(wf)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> Response:
    await svc.soft_delete(user, workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
