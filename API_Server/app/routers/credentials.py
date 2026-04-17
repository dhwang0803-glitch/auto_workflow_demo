"""Credential CRUD router — PLAN_07.

POST creates + returns metadata (no plaintext echo). DELETE is
ownership-scoped via `CredentialService`. List/GET are deferred until
`CredentialStore.list_by_owner()` lands in a follow-up Database PR.
"""
from __future__ import annotations

from uuid import UUID

from auto_workflow_database.repositories.base import User
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import Response

from app.dependencies import get_current_user
from app.models.credential import CredentialCreate, CredentialResponse
from app.services.credential_service import CredentialService

router = APIRouter()


def get_credential_service(request: Request) -> CredentialService:
    return request.app.state.credential_service


@router.post(
    "",
    response_model=CredentialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_credential(
    body: CredentialCreate,
    user: User = Depends(get_current_user),
    svc: CredentialService = Depends(get_credential_service),
) -> CredentialResponse:
    cid = await svc.create(user, body)
    return CredentialResponse(id=cid, name=body.name, type=body.type)


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(
    user: User = Depends(get_current_user),
    svc: CredentialService = Depends(get_credential_service),
) -> list[CredentialResponse]:
    rows = await svc.list(user)
    return [
        CredentialResponse(
            id=r.id, name=r.name, type=r.type, created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/{credential_id}", response_model=CredentialResponse)
async def get_credential(
    credential_id: UUID,
    user: User = Depends(get_current_user),
    svc: CredentialService = Depends(get_credential_service),
) -> CredentialResponse:
    r = await svc.get(user, credential_id)
    return CredentialResponse(
        id=r.id, name=r.name, type=r.type, created_at=r.created_at,
    )


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    credential_id: UUID,
    user: User = Depends(get_current_user),
    svc: CredentialService = Depends(get_credential_service),
) -> Response:
    await svc.delete(user, credential_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
