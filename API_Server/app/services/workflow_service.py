"""Workflow CRUD orchestration — PLAN_02 (refactor: concrete DomainError subclasses).

Enforces DAG validity (via `dag_validator` which raises `InvalidGraphError`),
per-plan workflow quota, ownership scoping (callers pass the authenticated
user and we never fan out beyond their rows), and soft-delete semantics.
All error paths raise concrete `DomainError` subclasses so routers need no
try/except and the global exception handler maps statuses in one place.
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID, uuid4

import base64
import hmac
import hashlib
import secrets

from auto_workflow_database.repositories.base import (
    Agent,
    AgentRepository,
    CredentialStore,
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
from app.services.credential_service import CredentialService
from app.services.dag_validator import validate_dag
from app.services.wake_worker import WakeWorker

logger = logging.getLogger(__name__)


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
        agent_connections: dict | None = None,
        credential_service: CredentialService | None = None,
        credential_store: CredentialStore | None = None,
        wake_worker: WakeWorker | None = None,
    ) -> None:
        self._repo = repo
        self._exec_repo = execution_repo
        self._s = settings
        self._scheduler = scheduler
        self._webhook_registry = webhook_registry
        self._user_repo = user_repo
        self._agent_repo = agent_repo
        # NOT `agent_connections or {}` — an empty dict is falsy so that
        # pattern would swap in a new dict, disconnecting WorkflowService
        # from the one the WS router appends to. Preserve the shared ref.
        self._agent_connections = agent_connections if agent_connections is not None else {}
        self._credential_service = credential_service
        self._credential_store = credential_store
        self._wake_worker = wake_worker

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

        # Collect credential_ref ids up front — reused for validation AND
        # (agent mode) credential_payloads assembly below.
        ref_ids: list[UUID] = []
        for node in wf.graph.get("nodes", []):
            ref = (node.get("config") or {}).get("credential_ref")
            if ref and "credential_id" in ref:
                ref_ids.append(UUID(ref["credential_id"]))

        # Validate ownership + existence. Plaintext is not retained here —
        # Worker (serverless) or Agent (agent mode) does the final resolution.
        if ref_ids and self._credential_service is not None:
            await self._credential_service.validate_refs(user, ref_ids)

        execution = Execution(
            id=uuid4(),
            workflow_id=wf.id,
            status="queued",
            execution_mode=wf.settings.get(
                "execution_mode", user.default_execution_mode
            ),
        )
        await self._exec_repo.create(execution)

        if execution.execution_mode == "serverless" and self._s.celery_broker_url:
            if self._s.serverless_execution_mode == "inline":
                # ADR-021 §5 stopgap — run the DAG in-process. Bypasses
                # Celery + Worker Pool entirely so environments without a
                # live broker (local dev pre-infra, early staging) still
                # get an end-to-end execute. Google Workspace nodes will
                # error here — they need WorkerContainer.configure() which
                # the Worker runs at startup. Plan_21 Phase 6 removes this
                # branch once the Worker Pool path is proven.
                import src.nodes  # noqa: F401 — triggers node self-registration
                from src.dispatcher.serverless import _execute
                from src.nodes.registry import registry as node_registry

                try:
                    await _execute(
                        str(execution.id),
                        exec_repo=self._exec_repo,
                        wf_repo=self._repo,
                        node_registry=node_registry,
                        credential_store=self._credential_store,
                    )
                except Exception:
                    logger.exception("inline execution failed for %s", execution.id)
            else:  # "celery" — steady-state path
                if self._wake_worker is not None:
                    await self._wake_worker.wake()
                try:
                    from celery import Celery
                    broker = Celery(broker=self._s.celery_broker_url)
                    # Execution_Engine worker.py listens on the workflow_tasks queue;
                    # without this the task sits in the default "celery" queue forever.
                    broker.send_task(
                        "execute_workflow",
                        args=[str(execution.id)],
                        queue="workflow_tasks",
                    )
                except Exception:
                    logger.exception("celery dispatch failed for execution %s", execution.id)
        elif execution.execution_mode == "agent" and self._agent_repo:
            agents = await self._agent_repo.list_by_owner(user.id)
            dispatched = False
            for ag in agents:
                ws = self._agent_connections.get(ag.id)
                if ws is not None:
                    # ADR-013 — re-wrap each credential for THIS agent's
                    # public key. Server plaintext lives only inside this
                    # loop; the WS frame carries only AES-GCM + RSA-OAEP
                    # ciphertext. Agent decrypts in-VPC (EE follow-up PR).
                    credential_payloads: list[dict] = []
                    if ref_ids and self._credential_store is not None:
                        for cid in ref_ids:
                            envelope = await self._credential_store.retrieve_for_agent(
                                cid, agent_public_key_pem=ag.public_key.encode("utf-8"),
                            )
                            credential_payloads.append({
                                "credential_id": str(cid),
                                "wrapped_key": base64.b64encode(envelope.wrapped_key).decode(),
                                "nonce": base64.b64encode(envelope.nonce).decode(),
                                "ciphertext": base64.b64encode(envelope.ciphertext).decode(),
                            })
                    await ws.send_json({
                        "type": "execute",
                        "execution_id": str(execution.id),
                        "workflow_id": str(wf.id),
                        "graph": wf.graph,
                        "credential_payloads": credential_payloads,
                    })
                    dispatched = True
                    break
            if not dispatched:
                await self._exec_repo.update_status(
                    execution.id, "failed",
                    error={"message": "no connected agent"},
                )

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
