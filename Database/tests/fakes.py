"""In-memory Repository fakes — PLAN_01 §4.

These exist so `API_Server` unit tests can exercise plan routing
(`users.plan_tier` → vLLM vs external API) and the Approval resume flow
without spinning up Postgres.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from uuid import uuid4

from datetime import datetime, timedelta, timezone

import json

from auto_workflow_database.crypto.hybrid import hybrid_encrypt
from auto_workflow_database.repositories.base import (
    Agent,
    AgentCredentialPayload,
    AgentRepository,
    ApprovalNotification,
    ApprovalNotificationRepository,
    CredentialMetadata,
    CredentialStore,
    Execution,
    ExecutionNodeLog,
    ExecutionNodeLogRepository,
    ExecutionRepository,
    ExecutionStatus,
    NodeCatalogRepository,
    NodeDefinition,
    NodeLogStatus,
    PlanTier,
    User,
    UserRepository,
    WebhookBinding,
    WebhookRegistry,
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

    async def list_by_workflow(
        self,
        workflow_id: UUID,
        *,
        limit: int = 50,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> list[Execution]:
        rows = [
            deepcopy(ex)
            for ex in self._store.values()
            if ex.workflow_id == workflow_id
        ]
        rows.sort(key=lambda e: (e.created_at or datetime.min, e.id), reverse=True)
        if cursor is not None:
            created_at, eid = cursor
            rows = [
                r for r in rows
                if (r.created_at or datetime.min, r.id) < (created_at, eid)
            ]
        return rows[:limit]

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


class InMemoryCredentialStore(CredentialStore):
    """Unit-test double. Skips encryption entirely — never use in prod."""

    def __init__(self) -> None:
        # Rows hold owner_id, name, credential_type, plaintext payload,
        # created_at, and (for google_oauth) oauth_metadata.
        self._store: dict[UUID, dict] = {}

    async def store(
        self,
        owner_id: UUID,
        name: str,
        plaintext: dict,
        *,
        credential_type: str = "unknown",
    ) -> UUID:
        cid = uuid4()
        self._store[cid] = {
            "owner_id": owner_id,
            "name": name,
            "type": credential_type,
            "plaintext": deepcopy(plaintext),
            "created_at": datetime.now(timezone.utc),
            "oauth_metadata": None,
        }
        return cid

    async def retrieve(self, credential_id: UUID) -> dict:
        row = self._store[credential_id]
        result = deepcopy(row["plaintext"])
        if row["oauth_metadata"] is not None:
            result["oauth_metadata"] = deepcopy(row["oauth_metadata"])
        return result

    async def bulk_retrieve(
        self,
        credential_ids: list[UUID],
        *,
        owner_id: UUID,
    ) -> dict[UUID, dict]:
        if not credential_ids:
            return {}
        found: dict[UUID, dict] = {}
        for cid in credential_ids:
            row = self._store.get(cid)
            if row is None or row["owner_id"] != owner_id:
                continue
            found[cid] = deepcopy(row["plaintext"])
        if len(found) != len(set(credential_ids)):
            raise KeyError("missing credential(s)")
        return found

    async def list_by_owner(
        self, owner_id: UUID
    ) -> list[CredentialMetadata]:
        rows = [
            CredentialMetadata(
                id=cid, name=row["name"], type=row["type"],
                created_at=row["created_at"],
            )
            for cid, row in self._store.items()
            if row["owner_id"] == owner_id
        ]
        rows.sort(key=lambda m: m.created_at, reverse=True)
        return rows

    async def delete(self, credential_id: UUID) -> None:
        self._store.pop(credential_id, None)

    async def retrieve_for_agent(
        self,
        credential_id: UUID,
        *,
        agent_public_key_pem: bytes,
    ) -> AgentCredentialPayload:
        plaintext = await self.retrieve(credential_id)
        return hybrid_encrypt(
            json.dumps(plaintext).encode("utf-8"), agent_public_key_pem
        )

    # ADR-019 — Google OAuth2 lifecycle

    async def store_google_oauth(
        self,
        owner_id: UUID,
        name: str,
        *,
        refresh_token: str,
        oauth_metadata: dict,
    ) -> UUID:
        cid = uuid4()
        self._store[cid] = {
            "owner_id": owner_id,
            "name": name,
            "type": "google_oauth",
            "plaintext": {"refresh_token": refresh_token},
            "created_at": datetime.now(timezone.utc),
            "oauth_metadata": deepcopy(oauth_metadata),
        }
        return cid

    async def update_oauth_tokens(
        self,
        credential_id: UUID,
        *,
        access_token: str,
        token_expires_at: datetime,
        refresh_token: str | None = None,
    ) -> None:
        row = self._store.get(credential_id)
        if row is None:
            raise KeyError(f"credential {credential_id} not found")
        md = dict(row["oauth_metadata"] or {})
        md["access_token"] = access_token
        md["token_expires_at"] = token_expires_at.isoformat()
        md.pop("needs_reauth", None)
        row["oauth_metadata"] = md
        if refresh_token is not None:
            row["plaintext"] = {"refresh_token": refresh_token}

    async def mark_needs_reauth(self, credential_id: UUID) -> None:
        row = self._store.get(credential_id)
        if row is None:
            raise KeyError(f"credential {credential_id} not found")
        md = dict(row["oauth_metadata"] or {})
        md["needs_reauth"] = True
        row["oauth_metadata"] = md

    # Test-only peek — returns the stored credential_type without going
    # through ABC. Lets tests assert the `type` was persisted correctly.
    def _peek_type(self, credential_id: UUID) -> str:
        return self._store[credential_id]["type"]


class InMemoryWebhookRegistry(WebhookRegistry):
    def __init__(self) -> None:
        self._by_path: dict[str, WebhookBinding] = {}

    async def register(
        self, workflow_id: UUID, *, secret: str | None = None
    ) -> WebhookBinding:
        binding = WebhookBinding(
            id=uuid4(),
            workflow_id=workflow_id,
            path=f"/webhooks/{uuid4()}",
            secret=secret,
        )
        self._by_path[binding.path] = binding
        return deepcopy(binding)

    async def resolve(self, path: str) -> WebhookBinding | None:
        b = self._by_path.get(path)
        return deepcopy(b) if b else None

    async def unregister(self, path: str) -> None:
        self._by_path.pop(path, None)


class InMemoryExecutionNodeLogRepository(ExecutionNodeLogRepository):
    def __init__(self) -> None:
        self._store: dict[UUID, ExecutionNodeLog] = {}

    async def record_start(self, log: ExecutionNodeLog) -> None:
        if log.id in self._store:
            raise ValueError(f"log {log.id} already started")
        self._store[log.id] = deepcopy(log)

    async def record_finish(
        self,
        log_id: UUID,
        started_at: datetime,
        *,
        status: NodeLogStatus,
        finished_at: datetime,
        duration_ms: int,
        output: dict | None = None,
        error: dict | None = None,
        stdout_uri: str | None = None,
        stderr_uri: str | None = None,
        model: str | None = None,
        tokens_prompt: int | None = None,
        tokens_completion: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        log = self._store.get(log_id)
        if log is None or log.started_at != started_at:
            raise KeyError(f"node log {log_id} not found")
        log.status = status
        log.finished_at = finished_at
        log.duration_ms = duration_ms
        log.output = output
        log.error = error
        log.stdout_uri = stdout_uri
        log.stderr_uri = stderr_uri
        log.model = model
        log.tokens_prompt = tokens_prompt
        log.tokens_completion = tokens_completion
        log.cost_usd = cost_usd

    async def list_for_execution(
        self, execution_id: UUID
    ) -> list[ExecutionNodeLog]:
        rows = [
            deepcopy(l)
            for l in self._store.values()
            if l.execution_id == execution_id
        ]
        rows.sort(key=lambda r: (r.node_id, -r.attempt))
        return rows

    async def summarize_llm_usage(
        self, execution_id: UUID
    ) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for l in self._store.values():
            if l.execution_id != execution_id or l.model is None:
                continue
            bucket = out.setdefault(
                l.model,
                {
                    "tokens_prompt": 0,
                    "tokens_completion": 0,
                    "cost_usd": 0.0,
                    "calls": 0,
                },
            )
            bucket["tokens_prompt"] += l.tokens_prompt or 0
            bucket["tokens_completion"] += l.tokens_completion or 0
            bucket["cost_usd"] += l.cost_usd or 0.0
            bucket["calls"] += 1
        return out


class InMemoryApprovalNotificationRepository(ApprovalNotificationRepository):
    def __init__(self) -> None:
        self._store: dict[UUID, ApprovalNotification] = {}

    async def record(self, notification: ApprovalNotification) -> None:
        if notification.id in self._store:
            raise ValueError(f"notification {notification.id} already recorded")
        n = deepcopy(notification)
        if n.created_at is None:
            n.created_at = datetime.now(timezone.utc)
        self._store[n.id] = n

    async def list_for_execution(
        self, execution_id: UUID
    ) -> list[ApprovalNotification]:
        rows = [
            deepcopy(n)
            for n in self._store.values()
            if n.execution_id == execution_id
        ]
        rows.sort(key=lambda r: (r.node_id, r.created_at or datetime.min), reverse=False)
        # Secondary desc on created_at within same node_id
        rows.sort(
            key=lambda r: (r.node_id, -(r.created_at.timestamp() if r.created_at else 0))
        )
        return rows

    async def list_undelivered(
        self, *, older_than: timedelta
    ) -> list[ApprovalNotification]:
        cutoff = datetime.now(timezone.utc) - older_than
        return sorted(
            (
                deepcopy(n)
                for n in self._store.values()
                if n.status in ("queued", "failed")
                and n.created_at is not None
                and n.created_at < cutoff
            ),
            key=lambda r: r.created_at,
        )


class InMemoryNodeCatalog(NodeCatalogRepository):
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], NodeDefinition] = {}

    async def upsert_many(self, nodes: list[NodeDefinition]) -> None:
        for n in nodes:
            self._store[(n.type, n.version)] = deepcopy(n)

    async def list_all(self) -> list[NodeDefinition]:
        return [deepcopy(n) for n in self._store.values()]


class InMemoryUserRepository(UserRepository):
    """Unit-test double for API_Server auth flow.

    Keeps `password_hash` in a side map so the public `User` DTO never
    carries it, matching the Postgres repository's isolation rule.
    """

    def __init__(self) -> None:
        self._by_id: dict[UUID, User] = {}
        self._hash_by_email: dict[str, bytes] = {}

    async def create(
        self,
        *,
        email: str,
        password_hash: bytes,
        plan_tier: PlanTier = "light",
    ) -> User:
        if any(u.email == email for u in self._by_id.values()):
            raise ValueError(f"email {email} already registered")
        user = User(
            id=uuid4(),
            email=email,
            plan_tier=plan_tier,
            is_verified=False,
            created_at=datetime.now(timezone.utc),
        )
        self._by_id[user.id] = user
        self._hash_by_email[email] = password_hash
        return deepcopy(user)

    async def get(self, user_id: UUID) -> User | None:
        user = self._by_id.get(user_id)
        return deepcopy(user) if user else None

    async def get_by_email(self, email: str) -> User | None:
        for user in self._by_id.values():
            if user.email == email:
                return deepcopy(user)
        return None

    async def get_password_hash(self, email: str) -> bytes | None:
        return self._hash_by_email.get(email)

    async def mark_verified(self, user_id: UUID) -> None:
        user = self._by_id.get(user_id)
        if user is not None:
            user.is_verified = True


class InMemoryAgentRepository(AgentRepository):
    def __init__(self) -> None:
        self._store: dict[UUID, Agent] = {}

    async def register(self, agent: Agent) -> None:
        self._store[agent.id] = deepcopy(agent)

    async def get(self, agent_id: UUID) -> Agent | None:
        a = self._store.get(agent_id)
        return deepcopy(a) if a else None

    async def update_heartbeat(self, agent_id: UUID) -> None:
        a = self._store.get(agent_id)
        if a:
            a.last_heartbeat = datetime.now(timezone.utc)

    async def list_by_owner(self, owner_id: UUID) -> list[Agent]:
        return [deepcopy(a) for a in self._store.values() if a.owner_id == owner_id]
