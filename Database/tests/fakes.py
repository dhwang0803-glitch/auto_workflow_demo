"""In-memory Repository fakes — PLAN_01 §4.

These exist so `API_Server` unit tests can exercise plan routing
(`users.plan_tier` → vLLM vs external API) and the Approval resume flow
without spinning up Postgres.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from Database.src.repositories.base import (
    Execution,
    ExecutionRepository,
    ExecutionStatus,
    Workflow,
    WorkflowRepository,
)


class InMemoryWorkflowRepository(WorkflowRepository):
    def __init__(self) -> None:
        self._store: dict[UUID, Workflow] = {}

    async def get(self, workflow_id: UUID) -> Workflow | None:
        wf = self._store.get(workflow_id)
        return deepcopy(wf) if wf else None

    async def save(self, workflow: Workflow) -> None:
        self._store[workflow.id] = deepcopy(workflow)

    async def list_by_owner(
        self, owner_id: UUID, *, active_only: bool = True
    ) -> list[Workflow]:
        return [
            deepcopy(wf)
            for wf in self._store.values()
            if wf.owner_id == owner_id and (not active_only or wf.is_active)
        ]

    async def delete(self, workflow_id: UUID) -> None:
        self._store.pop(workflow_id, None)


class InMemoryExecutionRepository(ExecutionRepository):
    # Maps execution_id → owning user_id, needed for list_pending_approvals.
    # Populated via the companion workflow repo in tests.
    def __init__(self, workflows: InMemoryWorkflowRepository | None = None) -> None:
        self._store: dict[UUID, Execution] = {}
        self._workflows = workflows

    async def create(self, execution: Execution) -> None:
        if execution.id in self._store:
            raise ValueError(f"execution {execution.id} already exists")
        self._store[execution.id] = deepcopy(execution)

    async def update_status(
        self,
        execution_id: UUID,
        status: ExecutionStatus,
        *,
        error: dict | None = None,
        paused_at_node: str | None = None,
    ) -> None:
        ex = self._require(execution_id)
        ex.status = status
        if error is not None:
            ex.error = error
        if status == "paused":
            ex.paused_at_node = paused_at_node
        elif status in ("resumed", "running"):
            ex.paused_at_node = None

    async def append_node_result(
        self,
        execution_id: UUID,
        node_id: str,
        result: dict,
        *,
        token_usage: dict | None = None,
        cost_usd: float | None = None,
    ) -> None:
        ex = self._require(execution_id)
        ex.node_results[node_id] = result
        if token_usage:
            for k, v in token_usage.items():
                ex.token_usage[k] = ex.token_usage.get(k, 0) + v
        if cost_usd is not None:
            ex.cost_usd += cost_usd

    async def finalize(self, execution_id: UUID, *, duration_ms: int) -> None:
        ex = self._require(execution_id)
        ex.duration_ms = duration_ms

    async def get(self, execution_id: UUID) -> Execution | None:
        ex = self._store.get(execution_id)
        return deepcopy(ex) if ex else None

    async def list_pending_approvals(self, owner_id: UUID) -> list[Execution]:
        if self._workflows is None:
            raise RuntimeError(
                "list_pending_approvals requires a workflow repo to resolve owner_id"
            )
        owned = {
            wf.id
            for wf in self._workflows._store.values()
            if wf.owner_id == owner_id
        }
        return [
            deepcopy(ex)
            for ex in self._store.values()
            if ex.status == "paused" and ex.workflow_id in owned
        ]

    def _require(self, execution_id: UUID) -> Execution:
        ex = self._store.get(execution_id)
        if ex is None:
            raise KeyError(f"execution {execution_id} not found")
        return ex
