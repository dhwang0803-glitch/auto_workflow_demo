"""Re-export Database InMemory fakes for Execution_Engine tests."""
from auto_workflow_database.repositories.base import Execution, ExecutionRepository, ExecutionStatus
from copy import deepcopy
from uuid import UUID


class InMemoryExecutionRepository(ExecutionRepository):
    """Minimal fake — only methods used by executor tests."""

    def __init__(self) -> None:
        self._store: dict[UUID, Execution] = {}

    async def create(self, execution: Execution) -> None:
        self._store[execution.id] = deepcopy(execution)

    async def update_status(
        self, execution_id: UUID, status: ExecutionStatus, *,
        error: dict | None = None, paused_at_node: str | None = None,
    ) -> None:
        ex = self._store[execution_id]
        ex.status = status
        if error is not None:
            ex.error = error

    async def append_node_result(
        self, execution_id: UUID, node_id: str, result: dict, *,
        token_usage: dict | None = None, cost_usd: float | None = None,
    ) -> None:
        self._store[execution_id].node_results[node_id] = result

    async def finalize(self, execution_id: UUID, *, duration_ms: int) -> None:
        self._store[execution_id].duration_ms = duration_ms

    async def get(self, execution_id: UUID) -> Execution | None:
        ex = self._store.get(execution_id)
        return deepcopy(ex) if ex else None

    async def list_by_workflow(self, workflow_id, *, limit=50, cursor=None):
        return []

    async def list_pending_approvals(self, owner_id):
        return []
