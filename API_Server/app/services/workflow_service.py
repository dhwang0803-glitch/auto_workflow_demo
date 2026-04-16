"""Workflow CRUD orchestration — PLAN_02 (refactor: concrete DomainError subclasses).

Enforces DAG validity (via `dag_validator` which raises `InvalidGraphError`),
per-plan workflow quota, ownership scoping (callers pass the authenticated
user and we never fan out beyond their rows), and soft-delete semantics.
All error paths raise concrete `DomainError` subclasses so routers need no
try/except and the global exception handler maps statuses in one place.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import hmac
import hashlib
import secrets

from auto_workflow_database.repositories.base import (
    Agent,
    AgentRepository,
    Execution,
    ExecutionRepository,
    User,
    UserRepository,
    WebhookBinding,
    WebhookRegistry,
    Workflow,
    WorkflowRepository,
)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.errors import AuthenticationError, InvalidGraphError, NotFoundError, QuotaExceededError, WorkflowNotActiveError
from app.models.workflow import (
    ActivateRequest,
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
        self,
        *,
        repo: WorkflowRepository,
        execution_repo: ExecutionRepository,
        settings: Settings,
        scheduler: AsyncIOScheduler | None = None,
        webhook_registry: WebhookRegistry | None = None,
        user_repo: UserRepository | None = None,
        agent_repo: AgentRepository | None = None,
    ) -> None:
        self._repo = repo
        self._exec_repo = execution_repo
        self._s = settings
        self._scheduler = scheduler
        self._webhook_registry = webhook_registry
        self._user_repo = user_repo
        self._agent_repo = agent_repo

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

    # --------------------------------------------------------------- execution

    async def execute_workflow(self, user: User, workflow_id: UUID) -> Execution:
        wf = await self._repo.get(workflow_id)
        if wf is None or wf.owner_id != user.id:
            raise NotFoundError("workflow not found")
        if not wf.is_active:
            raise WorkflowNotActiveError("cannot execute inactive workflow")
        execution = Execution(
            id=uuid4(),
            workflow_id=wf.id,
            status="queued",
            execution_mode=wf.settings.get(
                "execution_mode", user.default_execution_mode
            ),
        )
        await self._exec_repo.create(execution)
        # TODO(Execution_Engine): dispatch based on execution_mode
        return execution

    async def get_execution(self, user: User, execution_id: UUID) -> Execution:
        ex = await self._exec_repo.get(execution_id)
        if ex is None:
            raise NotFoundError("execution not found")
        wf = await self._repo.get(ex.workflow_id)
        if wf is None or wf.owner_id != user.id:
            raise NotFoundError("execution not found")
        return ex

    async def list_executions(
        self,
        user: User,
        workflow_id: UUID,
        *,
        limit: int = 50,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> list[Execution]:
        wf = await self._repo.get(workflow_id)
        if wf is None or wf.owner_id != user.id:
            raise NotFoundError("workflow not found")
        return await self._exec_repo.list_by_workflow(
            workflow_id, limit=limit, cursor=cursor
        )

    # -------------------------------------------------------------- scheduling

    async def activate_workflow(
        self, user: User, workflow_id: UUID, trigger: ActivateRequest
    ) -> Workflow:
        wf = await self._repo.get(workflow_id)
        if wf is None or wf.owner_id != user.id:
            raise NotFoundError("workflow not found")
        if not wf.is_active:
            raise WorkflowNotActiveError("cannot activate inactive workflow")
        if trigger.trigger_type == "cron":
            try:
                apscheduler_trigger = CronTrigger.from_crontab(trigger.cron)
            except ValueError as e:
                raise InvalidGraphError(f"invalid cron expression: {e}") from e
        else:
            apscheduler_trigger = IntervalTrigger(seconds=trigger.interval_seconds)
        if self._scheduler:
            self._scheduler.add_job(
                "app.scheduler:run_scheduled_execution",
                trigger=apscheduler_trigger,
                id=str(workflow_id),
                replace_existing=True,
                kwargs={"workflow_id": str(workflow_id), "owner_id": str(user.id)},
            )
        wf.settings = {**wf.settings, "trigger": trigger.model_dump()}
        await self._repo.save(wf)
        return wf

    async def deactivate_workflow(self, user: User, workflow_id: UUID) -> Workflow:
        wf = await self._repo.get(workflow_id)
        if wf is None or wf.owner_id != user.id:
            raise NotFoundError("workflow not found")
        if not wf.is_active:
            raise WorkflowNotActiveError("cannot deactivate inactive workflow")
        if self._scheduler:
            try:
                self._scheduler.remove_job(str(workflow_id))
            except Exception:
                pass
        wf.settings = {k: v for k, v in wf.settings.items() if k != "trigger"}
        await self._repo.save(wf)
        return wf

    # ---------------------------------------------------------------- webhook

    async def register_webhook(self, user: User, workflow_id: UUID) -> WebhookBinding:
        wf = await self._repo.get(workflow_id)
        if wf is None or wf.owner_id != user.id:
            raise NotFoundError("workflow not found")
        if not wf.is_active:
            raise WorkflowNotActiveError("cannot register webhook on inactive workflow")
        secret = secrets.token_urlsafe(32)
        binding = await self._webhook_registry.register(workflow_id, secret=secret)
        wf.settings = {**wf.settings, "webhook_path": binding.path}
        await self._repo.save(wf)
        return binding

    async def unregister_webhook(self, user: User, workflow_id: UUID) -> None:
        wf = await self._repo.get(workflow_id)
        if wf is None or wf.owner_id != user.id:
            raise NotFoundError("workflow not found")
        path = wf.settings.get("webhook_path")
        if path:
            await self._webhook_registry.unregister(path)
            wf.settings = {k: v for k, v in wf.settings.items() if k != "webhook_path"}
            await self._repo.save(wf)

    async def receive_webhook(self, path: str, body: bytes, signature: str | None) -> Execution:
        binding = await self._webhook_registry.resolve(path)
        if binding is None:
            raise NotFoundError("webhook not found")
        if not signature or not hmac.compare_digest(
            signature,
            hmac.new(binding.secret.encode(), body, hashlib.sha256).hexdigest(),
        ):
            raise AuthenticationError("invalid webhook signature")
        wf = await self._repo.get(binding.workflow_id)
        if wf is None or not wf.is_active:
            raise WorkflowNotActiveError("workflow inactive")
        user = await self._user_repo.get(wf.owner_id)
        return await self.execute_workflow(user, binding.workflow_id)

    # ------------------------------------------------------------------ agent

    async def register_agent(self, user: User, public_key: str, gpu_info: dict) -> Agent:
        agent = Agent(
            id=uuid4(),
            owner_id=user.id,
            public_key=public_key,
            gpu_info=gpu_info,
        )
        await self._agent_repo.register(agent)
        return agent
