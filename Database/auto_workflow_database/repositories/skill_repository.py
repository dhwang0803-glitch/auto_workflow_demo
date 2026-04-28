"""Postgres SkillRepository — PLAN_12 W2-7."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_workflow_database.models.skills import Skill as SkillORM
from auto_workflow_database.models.skills import SkillSource as SkillSourceORM
from auto_workflow_database.repositories.base import (
    Skill,
    SkillRepository,
    SkillScope,
    SkillSourceType,
    SkillStatus,
)


def _to_dto(row: SkillORM) -> Skill:
    return Skill(
        id=row.id,
        owner_user_id=row.owner_user_id,
        name=row.name,
        description=row.description,
        condition=row.condition,
        action=row.action,
        scope=row.scope,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresSkillRepository(SkillRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def create(
        self,
        *,
        owner_user_id: UUID,
        name: str,
        condition: dict,
        action: dict,
        description: str | None = None,
        scope: SkillScope = "workspace",
        status: SkillStatus = "pending_review",
        source_type: SkillSourceType | None = None,
        source_ref: dict | None = None,
    ) -> Skill:
        if (source_type is None) != (source_ref is None):
            raise ValueError(
                "source_type and source_ref must both be set or both be None"
            )

        async with self._sm() as s, s.begin():
            row = SkillORM(
                owner_user_id=owner_user_id,
                name=name,
                description=description,
                condition=condition,
                action=action,
                scope=scope,
                status=status,
            )
            s.add(row)
            # Need the server-side defaults (id, timestamps) before either
            # constructing the source row (which references skill_id) or
            # returning the DTO.
            await s.flush()

            if source_type is not None and source_ref is not None:
                s.add(
                    SkillSourceORM(
                        skill_id=row.id,
                        source_type=source_type,
                        source_ref=source_ref,
                    )
                )
            # Refresh so server defaults (created_at/updated_at) populate
            # before the session commits and the row falls out of scope.
            await s.refresh(row)
            return _to_dto(row)

    async def get_owned(
        self, owner_user_id: UUID, skill_id: UUID
    ) -> Skill | None:
        async with self._sm() as s:
            row = await s.get(SkillORM, skill_id)
            if row is None or row.owner_user_id != owner_user_id:
                return None
            return _to_dto(row)

    async def list_owned(
        self,
        owner_user_id: UUID,
        *,
        status: SkillStatus | None = None,
    ) -> list[Skill]:
        stmt = (
            select(SkillORM)
            .where(SkillORM.owner_user_id == owner_user_id)
            .order_by(SkillORM.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(SkillORM.status == status)
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(r) for r in result.scalars().all()]

    async def update_status(
        self,
        owner_user_id: UUID,
        skill_id: UUID,
        new_status: SkillStatus,
    ) -> Skill | None:
        async with self._sm() as s, s.begin():
            row = await s.get(SkillORM, skill_id)
            if row is None or row.owner_user_id != owner_user_id:
                return None
            row.status = new_status
            # Use server-side NOW() (not Python datetime.now) so created_at
            # and updated_at share a single clock — local-machine clock
            # skew vs Cloud SQL would otherwise produce updated_at <
            # created_at on a fast UPDATE-after-INSERT.
            row.updated_at = func.now()
            await s.flush()
            await s.refresh(row)
            return _to_dto(row)
