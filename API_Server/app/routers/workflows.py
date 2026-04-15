"""Workflow CRUD router — PLAN_02. Thin HTTP adapter over WorkflowService."""
from __future__ import annotations

from uuid import UUID

from auto_workflow_database.repositories.base import User

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response

from app.dependencies import get_current_user
from app.models.workflow import (
    WorkflowCreate,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowUpdate,
)
from app.services.workflow_service import WorkflowError, WorkflowService

router = APIRouter()


def get_workflow_service(request: Request) -> WorkflowService:
    return request.app.state.workflow_service


_ERROR_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "invalid_graph": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "quota_exceeded": status.HTTP_403_FORBIDDEN,
}


def _raise_http(e: WorkflowError) -> None:
    raise HTTPException(
        status_code=_ERROR_STATUS.get(e.code, 400), detail=e.message
    )


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
    try:
        wf = await svc.create(user, body)
    except WorkflowError as e:
        _raise_http(e)
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
    try:
        wf = await svc.get_owned(user, workflow_id)
    except WorkflowError as e:
        _raise_http(e)
    return WorkflowResponse.model_validate(wf)


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: UUID,
    body: WorkflowUpdate,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    try:
        wf = await svc.update(user, workflow_id, body)
    except WorkflowError as e:
        _raise_http(e)
    return WorkflowResponse.model_validate(wf)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: UUID,
    user: User = Depends(get_current_user),
    svc: WorkflowService = Depends(get_workflow_service),
) -> Response:
    try:
        await svc.soft_delete(user, workflow_id)
    except WorkflowError as e:
        _raise_http(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
