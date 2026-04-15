"""Postgres WorkflowRepository — PLAN_02 §4.2."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from Database.src.models.core import Workflow as WorkflowORM
from Database.src.repositories.base import Workflow, WorkflowRepository


def _to_dto(row: WorkflowORM) -> Workflow:
    return Workflow(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        settings=row.settings,
        graph=row.graph,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresWorkflowRepository(WorkflowRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def get(self, workflow_id: UUID) -> Workflow | None:
        async with self._sm() as s:
            row = await s.get(WorkflowORM, workflow_id)
            return _to_dto(row) if row else None

    async def save(self, workflow: Workflow) -> None:
        async with self._sm() as s, s.begin():
            row = await s.get(WorkflowORM, workflow.id)
            if row is None:
                row = WorkflowORM(
                    id=workflow.id,
                    owner_id=workflow.owner_id,
                    name=workflow.name,
                    settings=workflow.settings,
                    graph=workflow.graph,
                    is_active=workflow.is_active,
                )
                s.add(row)
            else:
                row.name = workflow.name
                row.settings = workflow.settings
                row.graph = workflow.graph
                row.is_active = workflow.is_active

    async def list_by_owner(
        self, owner_id: UUID, *, active_only: bool = True
    ) -> list[Workflow]:
        stmt = select(WorkflowORM).where(WorkflowORM.owner_id == owner_id)
        if active_only:
            stmt = stmt.where(WorkflowORM.is_active.is_(True))
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(r) for r in result.scalars().all()]

    async def delete(self, workflow_id: UUID) -> None:
        async with self._sm() as s, s.begin():
            row = await s.get(WorkflowORM, workflow_id)
            if row is not None:
                await s.delete(row)
