"""DBQueryNode — execute a Postgres query via asyncpg.

The DSN is assumed to be in `config["connection_url"]` in plaintext, injected via
credential_ref (resolved by the PLAN_08 Worker). The node itself does not know the
credential_id.

Parameters accept only the asyncpg `$N` placeholder — no string interpolation.
"""
from __future__ import annotations

import asyncio

import asyncpg

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class DBQueryNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "db_query"

    async def execute(self, input_data: dict, config: dict) -> dict:
        url = config["connection_url"]
        query = config["query"]
        params = config.get("parameters", [])
        timeout = config.get("timeout_seconds", 30)

        conn = await asyncio.wait_for(
            asyncpg.connect(dsn=url, timeout=timeout),
            timeout=timeout,
        )
        try:
            # Route row-returning statements (SELECT / WITH / ... RETURNING)
            # through fetch() so callers get a uniform list. Other DML goes
            # through execute() whose status string carries affected rows.
            stripped = query.lstrip().lower()
            returns_rows = (
                stripped.startswith(("select", "with"))
                or "returning" in stripped
            )
            if returns_rows:
                rows = await asyncio.wait_for(
                    conn.fetch(query, *params), timeout=timeout
                )
                return {
                    "rows": [dict(r) for r in rows],
                    "row_count": len(rows),
                }
            status = await asyncio.wait_for(
                conn.execute(query, *params), timeout=timeout
            )
            # asyncpg status is "UPDATE 3" / "DELETE 5" / etc.
            affected = int(status.rsplit(" ", 1)[-1]) if status else 0
            return {"rows": [], "row_count": affected}
        finally:
            await conn.close()


registry.register(DBQueryNode)
