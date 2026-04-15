"""Postgres WebhookRegistry — PLAN_02 §4.1."""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from Database.src.models.extras import WebhookBinding as WebhookORM
from Database.src.repositories.base import WebhookBinding, WebhookRegistry


def _to_dto(row: WebhookORM) -> WebhookBinding:
    return WebhookBinding(
        id=row.id,
        workflow_id=row.workflow_id,
        path=row.path,
        secret=row.secret,
        created_at=row.created_at,
    )


class PostgresWebhookRegistry(WebhookRegistry):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def register(
        self, workflow_id: UUID, *, secret: str | None = None
    ) -> WebhookBinding:
        path = f"/webhooks/{uuid4()}"
        async with self._sm() as s, s.begin():
            row = WebhookORM(workflow_id=workflow_id, path=path, secret=secret)
            s.add(row)
            await s.flush()
            return _to_dto(row)

    async def resolve(self, path: str) -> WebhookBinding | None:
        stmt = select(WebhookORM).where(WebhookORM.path == path)
        async with self._sm() as s:
            result = await s.execute(stmt)
            row = result.scalar_one_or_none()
            return _to_dto(row) if row else None

    async def unregister(self, path: str) -> None:
        async with self._sm() as s, s.begin():
            await s.execute(delete(WebhookORM).where(WebhookORM.path == path))
