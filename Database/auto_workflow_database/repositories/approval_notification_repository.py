"""Postgres ApprovalNotificationRepository — PLAN_04."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_workflow_database.models.notifications import (
    ApprovalNotification as NotificationORM,
)
from auto_workflow_database.repositories.base import (
    ApprovalNotification,
    ApprovalNotificationRepository,
)


def _to_dto(row: NotificationORM) -> ApprovalNotification:
    return ApprovalNotification(
        id=row.id,
        execution_id=row.execution_id,
        node_id=row.node_id,
        recipient=row.recipient,
        channel=row.channel,
        status=row.status,
        attempt=row.attempt,
        error=dict(row.error) if row.error else None,
        sent_at=row.sent_at,
        created_at=row.created_at,
    )


class PostgresApprovalNotificationRepository(ApprovalNotificationRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def record(self, notification: ApprovalNotification) -> None:
        async with self._sm() as s, s.begin():
            row = NotificationORM(
                id=notification.id,
                execution_id=notification.execution_id,
                node_id=notification.node_id,
                recipient=notification.recipient,
                channel=notification.channel,
                status=notification.status,
                attempt=notification.attempt,
                error=notification.error,
                sent_at=notification.sent_at,
            )
            s.add(row)

    async def list_for_execution(
        self, execution_id: UUID
    ) -> list[ApprovalNotification]:
        stmt = (
            select(NotificationORM)
            .where(NotificationORM.execution_id == execution_id)
            .order_by(
                NotificationORM.node_id,
                NotificationORM.created_at.desc(),
            )
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(r) for r in result.scalars().all()]

    async def list_undelivered(
        self, *, older_than: timedelta
    ) -> list[ApprovalNotification]:
        cutoff = datetime.now(timezone.utc) - older_than
        stmt = (
            select(NotificationORM)
            .where(NotificationORM.status.in_(("queued", "failed")))
            .where(NotificationORM.created_at < cutoff)
            .order_by(NotificationORM.created_at.asc())
        )
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(r) for r in result.scalars().all()]
