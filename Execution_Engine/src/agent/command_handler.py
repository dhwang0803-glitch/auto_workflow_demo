"""Agent command handler — execute 커맨드 수신 → run_workflow() 호출."""
from __future__ import annotations

import logging
from uuid import UUID, uuid4

from websockets.asyncio.client import ClientConnection

from auto_workflow_database.repositories.base import Execution

from src.agent.credential_client import (
    PreDecryptedCredentialStore,
    decrypt_payloads,
)
from src.agent.ws_repo import WebSocketExecutionRepository
from src.nodes.registry import NodeRegistry
from src.runtime.credentials import (
    graph_has_credential_refs,
    resolve_credential_refs,
)
from src.runtime.executor import run_workflow

logger = logging.getLogger(__name__)


async def handle_execute(
    ws: ClientConnection,
    msg: dict,
    node_registry: NodeRegistry,
    *,
    agent_private_key_pem: bytes | None = None,
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

    # PLAN_10 — if the graph references credentials, decrypt the
    # payloads bundled by API_Server (PLAN_08) and inject plaintext
    # into config before run_workflow sees it. Plaintext lives only
    # inside this function's scope.
    if graph_has_credential_refs(graph):
        payloads = msg.get("credential_payloads") or []
        if not payloads or agent_private_key_pem is None:
            await ws_repo.update_status(
                execution.id, "failed",
                error={"message": "credential resolution failed"},
            )
            return
        try:
            decrypted = decrypt_payloads(payloads, agent_private_key_pem)
            store = PreDecryptedCredentialStore(decrypted)
            # owner_id is ignored by PreDecryptedStore — server filtered.
            graph = await resolve_credential_refs(graph, store, owner_id=uuid4())
        except Exception:
            await ws_repo.update_status(
                execution.id, "failed",
                error={"message": "credential resolution failed"},
            )
            return

    await run_workflow(graph, execution, ws_repo, node_registry)
