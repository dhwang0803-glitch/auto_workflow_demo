"""Postgres SkillRepository — PLAN_12 W2-7."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from auto_workflow_database.models.skills import Skill as SkillORM
from auto_workflow_database.models.skills import SkillSource as SkillSourceORM
from auto_workflow_database.repositories.base import (
    Skill,
    SkillProvenance,
    SkillRepository,
    SkillScope,
    SkillSourceType,
    SkillStatus,
)


def _to_dto(row: SkillORM, source_ref: dict | None = None) -> Skill:
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
        source_ref=source_ref,
        user_id=row.user_id,
        source=row.source,  # type: ignore[arg-type]
        suggestion_hash=row.suggestion_hash,
    )


async def _fetch_latest_source_ref(
    session: AsyncSession, skill_id: UUID
) -> dict | None:
    """Return the most-recent skill_sources.source_ref for a skill, or None.

    Used by read paths (get_owned, list_owned, update_status) to pull
    provenance alongside the skill row. skill_sources is append-only, so
    "latest by extracted_at" is the canonical record.
    """
    stmt = (
        select(SkillSourceORM.source_ref)
        .where(SkillSourceORM.skill_id == skill_id)
        .order_by(SkillSourceORM.extracted_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


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
        user_id: UUID | None = None,
        source: SkillProvenance = "docs",
        suggestion_hash: str | None = None,
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
                user_id=user_id,
                source=source,
                suggestion_hash=suggestion_hash,
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
            return _to_dto(row, source_ref)

    async def get_owned(
        self, owner_user_id: UUID, skill_id: UUID
    ) -> Skill | None:
        async with self._sm() as s:
            row = await s.get(SkillORM, skill_id)
            if row is None or row.owner_user_id != owner_user_id:
                return None
            source_ref = await _fetch_latest_source_ref(s, skill_id)
            return _to_dto(row, source_ref)

    async def list_owned(
        self,
        owner_user_id: UUID,
        *,
        status: SkillStatus | None = None,
        scope: SkillScope | None = None,
    ) -> list[Skill]:
        # Correlated subquery pulls the most-recent skill_sources.source_ref
        # per skill in the same round-trip — avoids N+1 across the list.
        src_subq = (
            select(SkillSourceORM.source_ref)
            .where(SkillSourceORM.skill_id == SkillORM.id)
            .order_by(SkillSourceORM.extracted_at.desc())
            .limit(1)
            .correlate(SkillORM)
            .scalar_subquery()
        )
        stmt = (
            select(SkillORM, src_subq.label("source_ref"))
            .where(SkillORM.owner_user_id == owner_user_id)
            .order_by(SkillORM.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(SkillORM.status == status)
        if scope is not None:
            stmt = stmt.where(SkillORM.scope == scope)
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(row, src) for row, src in result.all()]

    async def list_workspace_active(
        self, *, limit: int = 50
    ) -> list[Skill]:
        # Mirrors list_owned's correlated subquery so each row carries its
        # most recent source_ref without a second query.
        src_subq = (
            select(SkillSourceORM.source_ref)
            .where(SkillSourceORM.skill_id == SkillORM.id)
            .order_by(SkillSourceORM.extracted_at.desc())
            .limit(1)
            .correlate(SkillORM)
            .scalar_subquery()
        )
        stmt = (
            select(SkillORM, src_subq.label("source_ref"))
            .where(SkillORM.scope == "workspace")
            .where(SkillORM.status == "active")
            .order_by(SkillORM.created_at.desc())
            .limit(limit)
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(row, src) for row, src in result.all()]

    async def list_personal_suggestion_hashes(
        self, user_id: UUID
    ) -> list[str]:
        stmt = (
            select(SkillORM.suggestion_hash)
            .where(SkillORM.user_id == user_id)
            .where(SkillORM.scope == "user")
            .where(SkillORM.suggestion_hash.isnot(None))
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [h for (h,) in result.all() if h]

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
            source_ref = await _fetch_latest_source_ref(s, skill_id)
            return _to_dto(row, source_ref)

    async def share_to_workspace(
        self,
        owner_user_id: UUID,
        skill_id: UUID,
    ) -> Skill | None:
        async with self._sm() as s, s.begin():
            row = await s.get(SkillORM, skill_id)
            if row is None or row.owner_user_id != owner_user_id:
                return None
            if row.scope == "workspace":
                return None  # already shared — caller maps to 409

            # Capture the original user_id before we null it so the audit
            # row records who first wrote the pattern.
            original_user_id = row.user_id

            row.scope = "workspace"
            # `skills_user_scope_chk` requires NULL user_id when scope='workspace'.
            row.user_id = None
            row.updated_at = func.now()

            # Append a new SkillSource audit row capturing the share event.
            # The append-only model means previous source rows (the
            # `observation` row PR-G wrote at extract time) survive — the
            # latest source_ref query just surfaces the most recent one.
            s.add(
                SkillSourceORM(
                    skill_id=row.id,
                    source_type="observation",
                    source_ref={
                        "shared_by_user_id": str(original_user_id)
                        if original_user_id is not None
                        else None,
                        "shared_from_personal": True,
                    },
                )
            )

            await s.flush()
            await s.refresh(row)
            source_ref = await _fetch_latest_source_ref(s, skill_id)
            return _to_dto(row, source_ref)
