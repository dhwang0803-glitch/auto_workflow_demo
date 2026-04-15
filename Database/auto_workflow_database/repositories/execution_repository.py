"""Postgres ExecutionRepository — PLAN_02 §4.2.

Status transitions mirror the InMemory fake. Callers are expected to honor
the ADR-007 state machine; this class enforces field hygiene only (clearing
`paused_at_node` on resume/running, setting it on paused).
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from auto_workflow_database.models.core import Execution as ExecutionORM
from auto_workflow_database.models.core import Workflow as WorkflowORM
from auto_workflow_database.repositories.base import (
    Execution,
    ExecutionRepository,
    ExecutionStatus,
)


def _to_dto(row: ExecutionORM) -> Execution:
    return Execution(
        id=row.id,
        workflow_id=row.workflow_id,
        status=row.status,
        execution_mode=row.execution_mode,
        started_at=row.started_at,
        finished_at=row.finished_at,
        node_results=dict(row.node_results or {}),
        error=dict(row.error) if row.error else None,
        token_usage=dict(row.token_usage or {}),
        cost_usd=float(row.cost_usd),
        duration_ms=row.duration_ms,
        paused_at_node=row.paused_at_node,
    )


class PostgresExecutionRepository(ExecutionRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def create(self, execution: Execution) -> None:
        async with self._sm() as s, s.begin():
            row = ExecutionORM(
                id=execution.id,
                workflow_id=execution.workflow_id,
                status=execution.status,
                execution_mode=execution.execution_mode,
                started_at=execution.started_at,
                finished_at=execution.finished_at,
                node_results=execution.node_results,
                error=execution.error,
                token_usage=execution.token_usage,
                cost_usd=execution.cost_usd,
                duration_ms=execution.duration_ms,
                paused_at_node=execution.paused_at_node,
            )
            s.add(row)

    async def update_status(
        self,
        execution_id: UUID,
        status: ExecutionStatus,
        *,
        error: dict | None = None,
        paused_at_node: str | None = None,
    ) -> None:
        async with self._sm() as s, s.begin():
            row = await s.get(ExecutionORM, execution_id)
            if row is None:
                raise KeyError(f"execution {execution_id} not found")
            row.status = status
            if error is not None:
                row.error = error
            if status == "paused":
                row.paused_at_node = paused_at_node
            elif status in ("resumed", "running"):
                row.paused_at_node = None

    async def append_node_result(
        self,
        execution_id: UUID,
        node_id: str,
        result: dict,
        *,
        token_usage: dict | None = None,
        cost_usd: float | None = None,
    ) -> None:
        async with self._sm() as s, s.begin():
            row = await s.get(ExecutionORM, execution_id)
            if row is None:
                raise KeyError(f"execution {execution_id} not found")
            # JSONB in-place mutation requires flag_modified so SQLAlchemy
            # emits the UPDATE. Otherwise the dict change is invisible.
            row.node_results = {**(row.node_results or {}), node_id: result}
            flag_modified(row, "node_results")
            if token_usage:
                merged = dict(row.token_usage or {})
                for k, v in token_usage.items():
                    merged[k] = merged.get(k, 0) + v
                row.token_usage = merged
                flag_modified(row, "token_usage")
            if cost_usd is not None:
                row.cost_usd = float(row.cost_usd) + cost_usd

    async def finalize(self, execution_id: UUID, *, duration_ms: int) -> None:
        async with self._sm() as s, s.begin():
            row = await s.get(ExecutionORM, execution_id)
            if row is None:
                raise KeyError(f"execution {execution_id} not found")
            row.duration_ms = duration_ms

    async def get(self, execution_id: UUID) -> Execution | None:
        async with self._sm() as s:
            row = await s.get(ExecutionORM, execution_id)
            return _to_dto(row) if row else None

    async def list_pending_approvals(self, owner_id: UUID) -> list[Execution]:
        stmt = (
            select(ExecutionORM)
            .join(WorkflowORM, ExecutionORM.workflow_id == WorkflowORM.id)
            .where(WorkflowORM.owner_id == owner_id)
            .where(ExecutionORM.status == "paused")
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(r) for r in result.scalars().all()]
