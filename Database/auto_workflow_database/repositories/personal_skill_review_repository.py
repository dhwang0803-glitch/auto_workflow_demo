"""Postgres PersonalSkillReviewRepository — PLAN_14 PR-G.

Append-only log of `(user_id, suggestion_hash, action)` decisions. PR-G
calls `list_rejected_hashes` before invoking the personalization agent
so a previously-rejected proposal short-circuits propose+judge inside
AI_Agent (`drop_reason="hash_previously_rejected"`).

Schema lives in `schemas/006_personalization.sql` and was migrated when
PLAN_14 PR-A (#171) landed; the ORM model is in
`auto_workflow_database/models/skills.py::PersonalSkillReview`.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_workflow_database.models.skills import (
    PersonalSkillReview as PersonalSkillReviewORM,
)
from auto_workflow_database.repositories.base import (
    PersonalSkillReview,
    PersonalSkillReviewAction,
    PersonalSkillReviewRepository,
)


def _to_dto(row: PersonalSkillReviewORM) -> PersonalSkillReview:
    return PersonalSkillReview(
        id=row.id,
        user_id=row.user_id,
        suggestion_hash=row.suggestion_hash,
        action=row.action,  # type: ignore[arg-type]
        rejection_reason=row.rejection_reason,
        created_at=row.created_at,
    )


class PostgresPersonalSkillReviewRepository(PersonalSkillReviewRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def record(
        self,
        *,
        user_id: UUID,
        suggestion_hash: str,
        action: PersonalSkillReviewAction,
        rejection_reason: str | None = None,
    ) -> PersonalSkillReview:
        async with self._sm() as s, s.begin():
            row = PersonalSkillReviewORM(
                user_id=user_id,
                suggestion_hash=suggestion_hash,
                action=action,
                rejection_reason=rejection_reason,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return _to_dto(row)

    async def list_rejected_hashes(self, user_id: UUID) -> list[str]:
        # DISTINCT at the DB so a user who's rejected the same hash twice
        # (rare but legal — different sessions with the same diff) doesn't
        # pad the wire payload to AI_Agent.
        stmt = (
            select(PersonalSkillReviewORM.suggestion_hash)
            .where(PersonalSkillReviewORM.user_id == user_id)
            .where(PersonalSkillReviewORM.action == "reject")
            .distinct()
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [h for (h,) in result.all()]

    async def list_by_user(
        self, user_id: UUID
    ) -> list[PersonalSkillReview]:
        stmt = (
            select(PersonalSkillReviewORM)
            .where(PersonalSkillReviewORM.user_id == user_id)
            .order_by(PersonalSkillReviewORM.created_at.desc())
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(r) for r in result.scalars().all()]
