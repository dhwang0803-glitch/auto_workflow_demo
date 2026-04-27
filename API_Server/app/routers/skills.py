"""Skill-bootstrap router (PLAN_12 W2-7).

Six endpoints for the Persona A wizard + skill review:

| Method | Path                            | Purpose |
|--------|---------------------------------|---------|
| POST   | /classify_domain                | free-text → domain category |
| POST   | /bootstrap                      | gap analysis for a (domain, declared skills) pair |
| POST   | /answer                         | one wizard turn → pending_review skill row |
| GET    | /                               | list owner's skills, optional status filter |
| GET    | /{id}                           | single owner-scoped fetch |
| POST   | /{id}/approve                   | pending_review → active |
| POST   | /{id}/reject                    | pending_review → rejected |

Domain errors raised by SkillBootstrapService bubble up to the global
DomainError handler in `app.main`. AI_Agent transport errors (httpx
HTTPStatusError) are converted to 502 here — they represent upstream
failures the user cannot fix by retry-with-different-input.
"""
from __future__ import annotations

from uuid import UUID

import httpx
from auto_workflow_database.repositories.base import User
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import get_current_user
from app.models.skills import (
    AnswerRequest,
    AnswerResponse,
    BootstrapRequest,
    BootstrapResponse,
    ClassifyDomainRequest,
    DomainClassificationResponse,
    SkillListResponse,
    SkillResponse,
    SkillStatusLiteral,
)
from app.services.skill_bootstrap_service import SkillBootstrapService

router = APIRouter()


def get_skill_bootstrap_service(request: Request) -> SkillBootstrapService:
    svc = request.app.state.skill_bootstrap_service
    if svc is None:
        # ai_agent_base_url unset — feature is not configured for this env.
        raise HTTPException(
            status_code=503,
            detail="skill bootstrap not configured (ai_agent_base_url unset)",
        )
    return svc


def _wrap_upstream(exc: httpx.HTTPStatusError) -> HTTPException:
    """Map an AI_Agent error to a 502 the user can act on.

    AI_Agent's 502 (parse error) and 422 (caller bug — but caller is us)
    both signal "the model did not give us a usable answer right now".
    Surface as a single 502 so frontend can offer a retry without
    distinguishing between LLM glitches and our own validation gaps.
    """
    return HTTPException(
        status_code=502,
        detail=f"ai_agent error {exc.response.status_code}",
    )


@router.post("/classify_domain", response_model=DomainClassificationResponse)
async def classify_domain(
    payload: ClassifyDomainRequest,
    user: User = Depends(get_current_user),  # auth required even though
                                              # this doesn't touch the DB —
                                              # keeps wizard entry behind
                                              # the same gate as the rest
    svc: SkillBootstrapService = Depends(get_skill_bootstrap_service),
) -> DomainClassificationResponse:
    try:
        return await svc.classify_domain(payload.text)
    except httpx.HTTPStatusError as exc:
        raise _wrap_upstream(exc) from exc


@router.post("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    payload: BootstrapRequest,
    user: User = Depends(get_current_user),
    svc: SkillBootstrapService = Depends(get_skill_bootstrap_service),
) -> BootstrapResponse:
    try:
        return await svc.bootstrap(
            domain=payload.domain,
            session_id=payload.session_id,
            extracted_skills=payload.extracted_skills,
        )
    except httpx.HTTPStatusError as exc:
        raise _wrap_upstream(exc) from exc


@router.post("/answer", response_model=AnswerResponse)
async def answer(
    payload: AnswerRequest,
    user: User = Depends(get_current_user),
    svc: SkillBootstrapService = Depends(get_skill_bootstrap_service),
) -> AnswerResponse:
    try:
        return await svc.answer_question(
            owner_user_id=user.id,
            session_id=payload.session_id,
            domain=payload.domain,
            policy_id=payload.policy_id,
            question=payload.question,
            answer=payload.answer,
        )
    except httpx.HTTPStatusError as exc:
        raise _wrap_upstream(exc) from exc


@router.get("", response_model=SkillListResponse)
async def list_skills(
    status: SkillStatusLiteral | None = Query(default=None),
    user: User = Depends(get_current_user),
    svc: SkillBootstrapService = Depends(get_skill_bootstrap_service),
) -> SkillListResponse:
    skills = await svc.list_for_user(user.id, status=status)
    return SkillListResponse(skills=skills)


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: UUID,
    user: User = Depends(get_current_user),
    svc: SkillBootstrapService = Depends(get_skill_bootstrap_service),
) -> SkillResponse:
    return await svc.get_for_user(user.id, skill_id)


@router.post("/{skill_id}/approve", response_model=SkillResponse)
async def approve_skill(
    skill_id: UUID,
    user: User = Depends(get_current_user),
    svc: SkillBootstrapService = Depends(get_skill_bootstrap_service),
) -> SkillResponse:
    return await svc.approve(owner_user_id=user.id, skill_id=skill_id)


@router.post("/{skill_id}/reject", response_model=SkillResponse)
async def reject_skill(
    skill_id: UUID,
    user: User = Depends(get_current_user),
    svc: SkillBootstrapService = Depends(get_skill_bootstrap_service),
) -> SkillResponse:
    return await svc.reject(owner_user_id=user.id, skill_id=skill_id)
