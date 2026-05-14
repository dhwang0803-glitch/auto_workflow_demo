"""HITL personalization router — PLAN_14 PR-G.

Four endpoints:

| Method | Path                              | Purpose |
|--------|-----------------------------------|---------|
| POST   | /extract_from_diff                | Frontend trigger after workflow save |
| GET    | /candidates                       | pending_review personal skill list |
| POST   | /candidates/{id}/activate         | pending_review → active |
| POST   | /candidates/{id}/reject           | archive + record reject hash |

AI_Agent transport errors (`httpx.HTTPStatusError`) surface as 502 the
same way `routers/skills.py` does — the user can't recover by retry with
different input, so a single status code is enough.
"""
from __future__ import annotations

from uuid import UUID

import httpx
from auto_workflow_database.repositories.base import User
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import get_current_user
from app.models.personalization import (
    ExtractFromDiffRequest,
    ExtractFromDiffResponse,
    PersonalCandidateListResponse,
    PersonalCandidateResponse,
    RejectCandidateRequest,
)
from app.services.personalization_service import PersonalizationService

router = APIRouter()


def get_personalization_service(request: Request) -> PersonalizationService:
    svc = request.app.state.personalization_service
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="personalization not configured (ai_agent_base_url unset)",
        )
    return svc


def _wrap_upstream(exc: httpx.HTTPStatusError) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=f"ai_agent error {exc.response.status_code}",
    )


@router.post("/extract_from_diff", response_model=ExtractFromDiffResponse)
async def extract_from_diff(
    payload: ExtractFromDiffRequest,
    user: User = Depends(get_current_user),
    svc: PersonalizationService = Depends(get_personalization_service),
) -> ExtractFromDiffResponse:
    try:
        return await svc.extract_from_diff(
            owner_user_id=user.id,
            workflow_id=payload.workflow_id,
        )
    except httpx.HTTPStatusError as exc:
        raise _wrap_upstream(exc) from exc


@router.get("/candidates", response_model=PersonalCandidateListResponse)
async def list_candidates(
    status: str = "pending_review",
    user: User = Depends(get_current_user),
    svc: PersonalizationService = Depends(get_personalization_service),
) -> PersonalCandidateListResponse:
    """List personal-skill candidates by status.

    `status='pending_review'` (default) drives the "Suggested from your
    edits" card stack; `status='active'` is what the Frontend share UI
    iterates over to render "Share with team" buttons (PR-J). Other
    values are rejected so the wire stays narrow.
    """
    if status == "pending_review":
        rows = await svc.list_pending_candidates(user.id)
    elif status == "active":
        rows = await svc.list_active_personal_candidates(user.id)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported status filter: {status}",
        )
    return PersonalCandidateListResponse(candidates=rows)


@router.post(
    "/candidates/{candidate_id}/activate",
    response_model=PersonalCandidateResponse,
)
async def activate_candidate(
    candidate_id: UUID,
    user: User = Depends(get_current_user),
    svc: PersonalizationService = Depends(get_personalization_service),
) -> PersonalCandidateResponse:
    return await svc.activate_candidate(
        owner_user_id=user.id, candidate_id=candidate_id
    )


@router.post(
    "/candidates/{candidate_id}/reject",
    response_model=PersonalCandidateResponse,
)
async def reject_candidate(
    candidate_id: UUID,
    payload: RejectCandidateRequest,
    user: User = Depends(get_current_user),
    svc: PersonalizationService = Depends(get_personalization_service),
) -> PersonalCandidateResponse:
    return await svc.reject_candidate(
        owner_user_id=user.id,
        candidate_id=candidate_id,
        reason=payload.reason,
    )


@router.post(
    "/candidates/{candidate_id}/share",
    response_model=PersonalCandidateResponse,
)
async def share_candidate(
    candidate_id: UUID,
    user: User = Depends(get_current_user),
    svc: PersonalizationService = Depends(get_personalization_service),
) -> PersonalCandidateResponse:
    """PR-J — promote one of the caller's active personal skills to the
    workspace pool. The returned DTO has `status='active'` and the
    Frontend re-fetches the active list to drop the row from the
    "Share" lane on success.
    """
    return await svc.share_candidate(
        owner_user_id=user.id, candidate_id=candidate_id
    )
