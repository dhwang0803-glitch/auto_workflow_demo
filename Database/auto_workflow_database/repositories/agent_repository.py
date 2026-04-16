"""Postgres AgentRepository — PLAN_08."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_workflow_database.models.extras import Agent as AgentORM
from auto_workflow_database.repositories.base import Agent, AgentRepository


def _to_dto(row: AgentORM) -> Agent:
    return Agent(
        id=row.id,
        owner_id=row.owner_id,
        public_key=row.public_key,
        gpu_info=dict(row.gpu_info or {}),
        last_heartbeat=row.last_heartbeat,
        registered_at=row.registered_at,
    )


class PostgresAgentRepository(AgentRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def register(self, agent: Agent) -> None:
        async with self._sm() as s, s.begin():
            row = AgentORM(
                id=agent.id,
                owner_id=agent.owner_id,
                public_key=agent.public_key,
                gpu_info=agent.gpu_info,
            )
            s.add(row)

    async def get(self, agent_id: UUID) -> Agent | None:
        async with self._sm() as s:
            row = await s.get(AgentORM, agent_id)
            return _to_dto(row) if row else None

    async def update_heartbeat(self, agent_id: UUID) -> None:
        async with self._sm() as s, s.begin():
            await s.execute(
                update(AgentORM)
                .where(AgentORM.id == agent_id)
                .values(last_heartbeat=datetime.now(timezone.utc))
            )

    async def list_by_owner(self, owner_id: UUID) -> list[Agent]:
        stmt = select(AgentORM).where(AgentORM.owner_id == owner_id)
        async with self._sm() as s:
            result = await s.execute(stmt)
            return [_to_dto(r) for r in result.scalars().all()]
