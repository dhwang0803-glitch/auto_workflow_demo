"""Postgres WorkflowRevisionRepository — PLAN_14 PR-Ba (PR-A leftover).

`record` assigns `revision_no` server-side via `SELECT MAX(revision_no) + 1`
inside a single transaction. The 006_personalization.sql UNIQUE constraint
on (workflow_id, revision_no) is the final guard against the SELECT/INSERT
race; under our single-writer save path it never fires in practice, but a
concurrent save against the same workflow_id raises an IntegrityError that
the caller surfaces as 409.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_workflow_database.models.skills import (
    WorkflowRevision as WorkflowRevisionORM,
)
from auto_workflow_database.repositories.base import (
    WorkflowRevision,
    WorkflowRevisionRepository,
    WorkflowRevisionSource,
)

_MAX_LIST_LIMIT = 200


def _to_dto(row: WorkflowRevisionORM) -> WorkflowRevision:
    return WorkflowRevision(
        id=row.id,
        workflow_id=row.workflow_id,
        revision_no=row.revision_no,
        source=row.source,  # type: ignore[arg-type]
        payload=row.payload,
        parent_revision_id=row.parent_revision_id,
        created_at=row.created_at,
        created_by=row.created_by,
    )


class PostgresWorkflowRevisionRepository(WorkflowRevisionRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def record(
        self,
        *,
        workflow_id: UUID,
        source: WorkflowRevisionSource,
        payload: dict,
        parent_revision_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> WorkflowRevision:
        async with self._sm() as s, s.begin():
            next_no_stmt = select(
                func.coalesce(func.max(WorkflowRevisionORM.revision_no), 0) + 1
            ).where(WorkflowRevisionORM.workflow_id == workflow_id)
            next_no = (await s.execute(next_no_stmt)).scalar_one()

            row = WorkflowRevisionORM(
                workflow_id=workflow_id,
                revision_no=next_no,
                source=source,
                payload=payload,
                parent_revision_id=parent_revision_id,
                created_by=created_by,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return _to_dto(row)

    async def get(self, revision_id: UUID) -> WorkflowRevision | None:
        async with self._sm() as s:
            row = await s.get(WorkflowRevisionORM, revision_id)
            return _to_dto(row) if row else None

    async def list_by_workflow(
        self,
        workflow_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkflowRevision]:
        capped = min(max(limit, 1), _MAX_LIST_LIMIT)
        stmt = (
            select(WorkflowRevisionORM)
            .where(WorkflowRevisionORM.workflow_id == workflow_id)
            .order_by(WorkflowRevisionORM.revision_no.desc())
            .limit(capped)
            .offset(max(offset, 0))
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(r) for r in result.scalars().all()]
