"""Postgres ExecutionNodeLogRepository — PLAN_03.

Two-phase write: `record_start` INSERTs a `running` row, `record_finish`
UPDATEs the same row to a terminal state. The UPDATE matches on
`(id, started_at)` because Postgres partitioned tables require the partition
key in every WHERE that targets a specific row — using `id` alone makes
Postgres scan every partition.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_workflow_database.models.logs import ExecutionNodeLog as NodeLogORM
from auto_workflow_database.repositories.base import (
    ExecutionNodeLog,
    ExecutionNodeLogRepository,
    NodeLogStatus,
)


def _to_dto(row: NodeLogORM) -> ExecutionNodeLog:
    return ExecutionNodeLog(
        id=row.id,
        execution_id=row.execution_id,
        node_id=row.node_id,
        attempt=row.attempt,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        input=dict(row.input) if row.input else None,
        output=dict(row.output) if row.output else None,
        error=dict(row.error) if row.error else None,
        stdout_uri=row.stdout_uri,
        stderr_uri=row.stderr_uri,
        model=row.model,
        tokens_prompt=row.tokens_prompt,
        tokens_completion=row.tokens_completion,
        cost_usd=float(row.cost_usd) if row.cost_usd is not None else None,
    )


class PostgresExecutionNodeLogRepository(ExecutionNodeLogRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def record_start(self, log: ExecutionNodeLog) -> None:
        async with self._sm() as s, s.begin():
            row = NodeLogORM(
                id=log.id,
                execution_id=log.execution_id,
                node_id=log.node_id,
                attempt=log.attempt,
                status=log.status,
                started_at=log.started_at,
                input=log.input,
            )
            s.add(row)

    async def record_finish(
        self,
        log_id: UUID,
        started_at: datetime,
        *,
        status: NodeLogStatus,
        finished_at: datetime,
        duration_ms: int,
        output: dict | None = None,
        error: dict | None = None,
        stdout_uri: str | None = None,
        stderr_uri: str | None = None,
        model: str | None = None,
        tokens_prompt: int | None = None,
        tokens_completion: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        stmt = (
            update(NodeLogORM)
            .where(NodeLogORM.id == log_id)
            .where(NodeLogORM.started_at == started_at)
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=duration_ms,
                output=output,
                error=error,
                stdout_uri=stdout_uri,
                stderr_uri=stderr_uri,
                model=model,
                tokens_prompt=tokens_prompt,
                tokens_completion=tokens_completion,
                cost_usd=cost_usd,
            )
        )
        async with self._sm() as s, s.begin():
            result = await s.execute(stmt)
            if result.rowcount == 0:
                raise KeyError(f"node log {log_id} not found")

    async def list_for_execution(
        self, execution_id: UUID
    ) -> list[ExecutionNodeLog]:
        stmt = (
            select(NodeLogORM)
            .where(NodeLogORM.execution_id == execution_id)
            .order_by(NodeLogORM.node_id, NodeLogORM.attempt.desc())
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(r) for r in result.scalars().all()]

    async def summarize_llm_usage(
        self, execution_id: UUID
    ) -> dict[str, dict]:
        stmt = (
            select(
                NodeLogORM.model,
                func.coalesce(func.sum(NodeLogORM.tokens_prompt), 0),
                func.coalesce(func.sum(NodeLogORM.tokens_completion), 0),
                func.coalesce(func.sum(NodeLogORM.cost_usd), 0),
                func.count(),
            )
            .where(NodeLogORM.execution_id == execution_id)
            .where(NodeLogORM.model.is_not(None))
            .group_by(NodeLogORM.model)
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            out: dict[str, dict] = {}
            for model, prompt, completion, cost, calls in result.all():
                out[model] = {
                    "tokens_prompt": int(prompt),
                    "tokens_completion": int(completion),
                    "cost_usd": float(cost),
                    "calls": int(calls),
                }
            return out
