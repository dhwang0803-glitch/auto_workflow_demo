"""Agent command handler — execute 커맨드 수신 → run_workflow() 호출."""
from __future__ import annotations

import logging
from uuid import UUID

from websockets.asyncio.client import ClientConnection

from auto_workflow_database.repositories.base import Execution

from src.agent.ws_repo import WebSocketExecutionRepository
from src.nodes.registry import NodeRegistry
from src.runtime.executor import run_workflow

logger = logging.getLogger(__name__)


async def handle_execute(
    ws: ClientConnection,
    msg: dict,
    node_registry: NodeRegistry,
) -> None:
    execution_id = msg["execution_id"]
    graph = msg["graph"]
    execution = Execution(
        id=UUID(execution_id),
        workflow_id=UUID(msg.get("workflow_id", execution_id)),
        status="queued",
        execution_mode="agent",
    )
    ws_repo = WebSocketExecutionRepository(ws, execution)
    await run_workflow(graph, execution, ws_repo, node_registry)
