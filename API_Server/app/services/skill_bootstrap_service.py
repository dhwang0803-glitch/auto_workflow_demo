"""SkillBootstrapService — orchestrates AI_Agent + SkillRepository (PLAN_12 W2-7).

Owns the wizard flow:

1. classify_domain  — proxy to AI_Agent so the frontend can label the user
2. analyze_gaps     — pull missing-policy questions from AI_Agent (no DB write)
3. answer_question  — invoke answer_to_skill on AI_Agent + INSERT a skill
                      with status=pending_review (single audit-paired write)
4. approve / reject — pending_review → active|rejected (in-place transition,
                      not row delete — the row keeps its id so the audit
                      trail in skill_sources stays intact)
5. list / get       — owner-scoped reads for the review UI

Status transition rules (enforced here, not in the repository):

    pending_review ─ approve ─▶ active
    pending_review ─ reject  ─▶ rejected
    everything else            (current status returned, or 409 from caller)

The repository accepts any status; this service is the only place that
decides which transitions are legal for end users.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from auto_workflow_database.repositories.base import (
    Skill,
    SkillRepository,
    SkillStatus,
)

from app.errors import DomainError, NotFoundError
from app.models.skills import (
    AnswerResponse,
    BootstrapResponse,
    DomainClassificationResponse,
    ExtractedSkillBody,
    SkillDraftBody,
    SkillResponse,
)
from app.services.ai_agent_client import AIAgentHTTPBackend


class SkillNotInReviewError(DomainError):
    """Approve/reject is only valid from `pending_review`.

    Any other current status is a 409 — the caller raced or has a stale
    UI. Includes the current status in the message so clients can refresh.
    """

    http_status = 409

    def __init__(self, skill_id: UUID, current_status: str) -> None:
        super().__init__(
            f"skill {skill_id} cannot transition from {current_status!r}"
        )


class SkillBootstrapService:
    def __init__(
        self,
        *,
        ai_agent: AIAgentHTTPBackend,
        skill_repo: SkillRepository,
    ) -> None:
        self._ai = ai_agent
        self._skills = skill_repo

    # --- 1. classify ----------------------------------------------------

    async def classify_domain(self, text: str) -> DomainClassificationResponse:
        return await self._ai.classify_domain(text)

    # --- 2. bootstrap (gap analysis) -----------------------------------

    async def bootstrap(
        self,
        *,
        domain: str,
        session_id: UUID | None,
        extracted_skills: list[ExtractedSkillBody],
    ) -> BootstrapResponse:
        # session_id is round-tripped through the wizard so /answer calls
        # can correlate without server-side session storage. Mint one if
        # the caller didn't.
        sid = session_id or uuid4()
        gaps = await self._ai.analyze_gaps(domain, extracted_skills)
        return BootstrapResponse(
            session_id=sid,
            domain=domain,  # type: ignore[arg-type]
            missing=gaps,
        )

    # --- 3. answer turn → draft skill ----------------------------------

    async def answer_question(
        self,
        *,
        owner_user_id: UUID,
        session_id: UUID,
        domain: str,
        policy_id: str,
        question: str,
        answer: str,
    ) -> AnswerResponse:
        draft = await self._ai.answer_to_skill(
            domain=domain,
            policy_id=policy_id,
            question=question,
            answer=answer,
        )
        # Persist as pending_review immediately. Approve/reject mutates in
        # place (no separate drafts table) — keeps the audit row in
        # skill_sources stable across the transition.
        skill = await self._skills.create(
            owner_user_id=owner_user_id,
            name=draft.name,
            description=draft.description or None,
            condition={"text": draft.condition},
            action={"text": draft.action},
            status="pending_review",
            source_type="conversation",
            source_ref={
                "session_id": str(session_id),
                "policy_id": policy_id,
                "question": question,
                "answer": answer,
            },
        )
        return AnswerResponse(
            session_id=session_id,
            skill_id=skill.id,
            draft=draft,
        )

    # --- 4. approve / reject -------------------------------------------

    async def approve(
        self,
        *,
        owner_user_id: UUID,
        skill_id: UUID,
    ) -> SkillResponse:
        return await self._transition(
            owner_user_id=owner_user_id,
            skill_id=skill_id,
            new_status="active",
        )

    async def reject(
        self,
        *,
        owner_user_id: UUID,
        skill_id: UUID,
    ) -> SkillResponse:
        return await self._transition(
            owner_user_id=owner_user_id,
            skill_id=skill_id,
            new_status="rejected",
        )

    async def _transition(
        self,
        *,
        owner_user_id: UUID,
        skill_id: UUID,
        new_status: SkillStatus,
    ) -> SkillResponse:
        existing = await self._skills.get_owned(owner_user_id, skill_id)
        if existing is None:
            raise NotFoundError(f"skill {skill_id} not found")
        if existing.status != "pending_review":
            raise SkillNotInReviewError(skill_id, existing.status)
        updated = await self._skills.update_status(
            owner_user_id, skill_id, new_status
        )
        # update_status returning None here would be a TOCTOU race (skill
        # was deleted between the get_owned and update_status). Surface as
        # 404 — the row is gone from the user's POV.
        if updated is None:
            raise NotFoundError(f"skill {skill_id} not found")
        return _to_response(updated)

    # --- 5. list / get -------------------------------------------------

    async def list_for_user(
        self,
        owner_user_id: UUID,
        *,
        status: SkillStatus | None = None,
    ) -> list[SkillResponse]:
        rows = await self._skills.list_owned(owner_user_id, status=status)
        return [_to_response(r) for r in rows]

    async def get_for_user(
        self,
        owner_user_id: UUID,
        skill_id: UUID,
    ) -> SkillResponse:
        row = await self._skills.get_owned(owner_user_id, skill_id)
        if row is None:
            raise NotFoundError(f"skill {skill_id} not found")
        return _to_response(row)


def _to_response(skill: Skill) -> SkillResponse:
    # `created_at` / `updated_at` are server-set non-null after a real
    # round-trip; the Optional in the DTO covers the Skill(...) constructor
    # default so we cast to str-aware fields here. If either is missing
    # at this point that's a repository bug, not a user-facing 404.
    assert skill.created_at is not None
    assert skill.updated_at is not None
    return SkillResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        condition=skill.condition,
        action=skill.action,
        scope=skill.scope,
        status=skill.status,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )
