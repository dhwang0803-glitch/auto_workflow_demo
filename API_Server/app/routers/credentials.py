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
from pydantic import BaseModel

from app.dependencies import get_current_user
from app.errors import NotFoundError
from app.models.credential import CredentialCreate, CredentialResponse
from app.routers.oauth_google import (
    AuthorizeResponse,
    OAuthFlowError,
    build_authorize_url,
)
from app.services.credential_service import CredentialService
from app.services.google_oauth_client import GoogleOAuthClient
from app.services.oauth_state import OAuthStateSigner

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


class ReauthRequest(BaseModel):
    return_to: str | None = None


@router.post("/{credential_id}/reauth", response_model=AuthorizeResponse)
async def reauth_credential(
    credential_id: UUID,
    body: ReauthRequest,
    request: Request,
    user: User = Depends(get_current_user),
) -> AuthorizeResponse:
    """ADR-019 re-consent — starts a fresh Google flow for an existing
    google_oauth credential. The callback updates tokens in place via
    `update_oauth_tokens` and clears any `needs_reauth` flag."""
    settings = request.app.state.settings
    signer: OAuthStateSigner = request.app.state.oauth_state_signer
    client: GoogleOAuthClient | None = request.app.state.google_oauth_client
    if client is None:
        # Matches the behaviour of /authorize — can't start a flow when
        # the Google client isn't configured.
        raise OAuthFlowError("google oauth is not configured")

    store = request.app.state.credential_store
    try:
        creds = await store.bulk_retrieve([credential_id], owner_id=user.id)
    except KeyError:
        raise NotFoundError("credential not found")

    plaintext = creds[credential_id]
    metadata = plaintext.get("oauth_metadata") or {}
    scopes = metadata.get("scopes") or metadata.get("granted_scopes") or []
    if not scopes:
        raise OAuthFlowError("credential has no recorded scopes to reauth")

    # list_by_owner is cheap (one user's rows) and we need the name back
    # so the state roundtrips the same data as first-time authorize.
    name = ""
    for row in await store.list_by_owner(user.id):
        if row.id == credential_id:
            name = row.name
            break

    state = signer.issue(
        user.id,
        credential_name=name,
        scopes=list(scopes),
        return_to=body.return_to,
        existing_credential_id=credential_id,
    )
    return AuthorizeResponse(
        authorize_url=build_authorize_url(
            settings=settings, state=state, scopes=list(scopes)
        )
    )
