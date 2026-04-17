"""DBQueryNode — Postgres 쿼리 실행 via asyncpg.

DSN 은 credential_ref 로 주입된 `config["connection_url"]` 에 평문으로 들어있다는
전제 (PLAN_08 Worker 가 해소). 노드 자체는 credential_id 를 모른다.

파라미터는 asyncpg `$N` 플레이스홀더만 허용 — 문자열 interpolation 금지.
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
