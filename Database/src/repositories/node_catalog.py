"""Postgres NodeCatalogRepository — PLAN_02 §4.1.

Idempotent upsert on (type, version). `Execution_Engine` calls this once
at startup to sync its `NodeRegistry` into the DB catalog.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from Database.src.models.core import Node as NodeORM
from Database.src.repositories.base import NodeCatalogRepository, NodeDefinition


def _to_dto(row: NodeORM) -> NodeDefinition:
    return NodeDefinition(
        type=row.type,
        version=row.version,
        schema=row.schema,
        registered_at=row.registered_at,
    )


class PostgresNodeCatalog(NodeCatalogRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def upsert_many(self, nodes: list[NodeDefinition]) -> None:
        if not nodes:
            return
        payload = [
            {"type": n.type, "version": n.version, "schema": n.schema}
            for n in nodes
        ]
        stmt = insert(NodeORM).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["type", "version"],
            set_={"schema": stmt.excluded.schema},
        )
        async with self._sm() as s, s.begin():
            await s.execute(stmt)

    async def list_all(self) -> list[NodeDefinition]:
        async with self._sm() as s:
            result = await s.execute(select(NodeORM))
            return [_to_dto(r) for r in result.scalars().all()]
