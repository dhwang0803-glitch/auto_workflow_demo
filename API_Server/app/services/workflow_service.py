"""Workflow CRUD orchestration — PLAN_02 (refactor: concrete DomainError subclasses).

Enforces DAG validity (via `dag_validator` which raises `InvalidGraphError`),
per-plan workflow quota, ownership scoping (callers pass the authenticated
user and we never fan out beyond their rows), and soft-delete semantics.
All error paths raise concrete `DomainError` subclasses so routers need no
try/except and the global exception handler maps statuses in one place.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from auto_workflow_database.repositories.base import (
    User,
    Workflow,
    WorkflowRepository,
)

from app.config import Settings
from app.errors import NotFoundError, QuotaExceededError
from app.models.workflow import (
    WorkflowCreate,
    WorkflowListResponse,
    WorkflowSummary,
    WorkflowUpdate,
)
from app.services.dag_validator import validate_dag


class WorkflowService:
    # 90% of the cap — any value >= threshold flips approaching_limit.
    _APPROACHING_RATIO = 0.9

    def __init__(
        self, *, repo: WorkflowRepository, settings: Settings
    ) -> None:
        self._repo = repo
        self._s = settings

    # ------------------------------------------------------------------ read

    async def get_owned(self, user: User, workflow_id: UUID) -> Workflow:
        wf = await self._repo.get(workflow_id)
        # 404 (not 403) when the row is missing *or* owned by someone else —
        # enumeration defence, matches PLAN_02 Q3.
        if wf is None or wf.owner_id != user.id or not wf.is_active:
            raise NotFoundError("workflow not found")
        return wf

    async def list_for_user(self, user: User) -> WorkflowListResponse:
        rows = await self._repo.list_by_owner(user.id, active_only=True)
        limit = self._s.workflow_limit_for_tier(user.plan_tier)
        total = len(rows)
        return WorkflowListResponse(
            items=[
                WorkflowSummary(
                    id=w.id,
                    name=w.name,
                    is_active=w.is_active,
                    created_at=w.created_at,
                    updated_at=w.updated_at,
                )
                for w in rows
            ],
            total=total,
            limit=limit,
            plan_tier=user.plan_tier,
            approaching_limit=total >= int(limit * self._APPROACHING_RATIO),
        )

    # ----------------------------------------------------------------- write

    async def create(self, user: User, body: WorkflowCreate) -> Workflow:
        validate_dag(body.graph)  # raises InvalidGraphError (422)

        existing = await self._repo.list_by_owner(user.id, active_only=True)
        limit = self._s.workflow_limit_for_tier(user.plan_tier)
        if len(existing) >= limit:
            raise QuotaExceededError(
                f"workflow limit reached: {limit} workflows for "
                f"{user.plan_tier} tier (plan upgrade available)"
            )

        wf = Workflow(
            id=uuid4(),
            owner_id=user.id,
            name=body.name,
            settings=body.settings,
            graph=body.graph.model_dump(),
            is_active=True,
        )
        await self._repo.save(wf)
        return wf

    async def update(
        self, user: User, workflow_id: UUID, body: WorkflowUpdate
    ) -> Workflow:
        wf = await self.get_owned(user, workflow_id)
        validate_dag(body.graph)

        wf.name = body.name
        wf.settings = body.settings
        wf.graph = body.graph.model_dump()
        await self._repo.save(wf)
        return wf

    async def soft_delete(self, user: User, workflow_id: UUID) -> None:
        wf = await self.get_owned(user, workflow_id)
        wf.is_active = False
        await self._repo.save(wf)
