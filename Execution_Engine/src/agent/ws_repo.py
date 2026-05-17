"""WebSocketExecutionRepository — report execution state to the server over WS without a DB.

The Agent runs inside the customer's VPC and has no direct DB access. We implement
executor's ExecutionRepository ABC as WS message sending so that run_workflow() behaves
identically in both serverless and agent modes.
"""
from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from websockets.asyncio.client import ClientConnection

from auto_workflow_database.repositories.base import (
    Execution,
    ExecutionRepository,
    ExecutionStatus,
)


class WebSocketExecutionRepository(ExecutionRepository):

    def __init__(self, ws: ClientConnection, execution: Execution) -> None:
        self._ws = ws
        self._execution = execution

    async def update_status(
        self, execution_id: UUID, status: ExecutionStatus, *,
        error: dict | None = None, paused_at_node: str | None = None,
    ) -> None:
        self._execution.status = status
        if error is not None:
            self._execution.error = error
        await self._ws.send(json.dumps({
            "type": "status_update",
            "execution_id": str(execution_id),
            "status": status,
            "error": error,
        }))

    async def append_node_result(
        self, execution_id: UUID, node_id: str, result: dict, *,
        token_usage: dict | None = None, cost_usd: float | None = None,
    ) -> None:
        self._execution.node_results[node_id] = result
        await self._ws.send(json.dumps({
            "type": "node_result",
            "execution_id": str(execution_id),
            "node_id": node_id,
            "result": result,
        }))

    async def finalize(self, execution_id: UUID, *, duration_ms: int) -> None:
        self._execution.duration_ms = duration_ms
        await self._ws.send(json.dumps({
            "type": "execution_result",
            "execution_id": str(execution_id),
            "duration_ms": duration_ms,
            "node_results": self._execution.node_results,
        }))

    async def create(self, execution: Execution) -> None:
        raise NotImplementedError("agent does not create executions")

    async def get(self, execution_id: UUID) -> Execution | None:
        if execution_id == self._execution.id:
            return self._execution
        return None

    async def list_by_workflow(self, workflow_id, *, limit=50, cursor=None):
        raise NotImplementedError("agent does not list executions")

    async def list_pending_approvals(self, owner_id):
        raise NotImplementedError("agent does not list approvals")
