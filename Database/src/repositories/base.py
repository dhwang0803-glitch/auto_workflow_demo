"""Repository ABCs — PLAN_01 §4.

These interfaces are the only contract `API_Server` and `Execution_Engine`
depend on. Postgres implementations land in PLAN_02.

Status model (ADR-007):
    queued → running → (paused ↔ resumed) → success | failed | rejected | cancelled
`resumed` is transient — `update_status(resumed)` must be followed immediately
by a transition back to `running`. Repository re-entry must be idempotent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

PlanTier = Literal["light", "middle", "heavy"]
ExecutionMode = Literal["serverless", "agent"]
ExecutionStatus = Literal[
    "queued",
    "running",
    "paused",
    "resumed",
    "success",
    "failed",
    "rejected",
    "cancelled",
]


@dataclass
class User:
    id: UUID
    email: str
    plan_tier: PlanTier
    default_execution_mode: ExecutionMode = "serverless"
    external_api_policy: dict = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass
class Workflow:
    id: UUID
    owner_id: UUID
    name: str
    settings: dict
    graph: dict
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Credential:
    id: UUID
    owner_id: UUID
    name: str
    # `encrypted_data` never leaves the credential_store boundary — kept here
    # only for Postgres repo round-tripping. Retrieval returns plaintext dict.
    encrypted_data: bytes = b""
    created_at: datetime | None = None


@dataclass
class Agent:
    id: UUID
    owner_id: UUID
    public_key: str
    gpu_info: dict = field(default_factory=dict)
    last_heartbeat: datetime | None = None
    registered_at: datetime | None = None


@dataclass
class WebhookBinding:
    id: UUID
    workflow_id: UUID
    path: str
    secret: str | None = None
    created_at: datetime | None = None


NodeLogStatus = Literal["running", "success", "failed", "skipped"]

NotificationChannel = Literal["email", "slack"]
NotificationStatus = Literal["queued", "sent", "failed", "bounced"]


@dataclass
class ApprovalNotification:
    """PLAN_04 — one row per send attempt (append-only).

    `recipient` holds a plaintext email address or Slack user id depending
    on `channel`. Stored in plaintext to avoid a JOIN against `users` on
    every dashboard query — GDPR deletion is handled by a separate ops PLAN
    with a targeted `DELETE FROM approval_notifications WHERE recipient=?`.
    """

    id: UUID
    execution_id: UUID
    node_id: str
    recipient: str
    channel: NotificationChannel
    status: NotificationStatus
    attempt: int
    error: dict | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None


@dataclass
class ExecutionNodeLog:
    """PLAN_03 — one row per (execution, node, attempt).

    `attempt` is assigned by the caller (e.g. `Execution_Engine` retry loop).
    The DEFAULT 1 in the DDL exists only for happy-path first-attempt
    convenience; retries MUST pass an explicit, monotonically increasing value.
    """

    id: UUID
    execution_id: UUID
    node_id: str
    attempt: int
    status: NodeLogStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    input: dict | None = None
    output: dict | None = None
    error: dict | None = None
    stdout_uri: str | None = None
    stderr_uri: str | None = None
    model: str | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    cost_usd: float | None = None


@dataclass
class NodeDefinition:
    type: str
    version: str
    schema: dict
    registered_at: datetime | None = None


@dataclass
class Execution:
    id: UUID
    workflow_id: UUID
    status: ExecutionStatus
    execution_mode: ExecutionMode
    started_at: datetime | None = None
    finished_at: datetime | None = None
    node_results: dict = field(default_factory=dict)
    error: dict | None = None
    token_usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: int | None = None
    paused_at_node: str | None = None


class WorkflowRepository(ABC):
    @abstractmethod
    async def get(self, workflow_id: UUID) -> Workflow | None: ...

    @abstractmethod
    async def save(self, workflow: Workflow) -> None: ...

    @abstractmethod
    async def list_by_owner(
        self, owner_id: UUID, *, active_only: bool = True
    ) -> list[Workflow]: ...

    @abstractmethod
    async def delete(self, workflow_id: UUID) -> None: ...


class ExecutionRepository(ABC):
    @abstractmethod
    async def create(self, execution: Execution) -> None: ...

    @abstractmethod
    async def update_status(
        self,
        execution_id: UUID,
        status: ExecutionStatus,
        *,
        error: dict | None = None,
        paused_at_node: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def append_node_result(
        self,
        execution_id: UUID,
        node_id: str,
        result: dict,
        *,
        token_usage: dict | None = None,
        cost_usd: float | None = None,
    ) -> None: ...

    @abstractmethod
    async def finalize(
        self,
        execution_id: UUID,
        *,
        duration_ms: int,
    ) -> None: ...

    @abstractmethod
    async def get(self, execution_id: UUID) -> Execution | None: ...

    @abstractmethod
    async def list_pending_approvals(self, owner_id: UUID) -> list[Execution]: ...


class CredentialStore(ABC):
    """ADR-004 Fernet-at-rest. See `FernetCredentialStore` for the impl."""

    @abstractmethod
    async def store(self, owner_id: UUID, name: str, plaintext: dict) -> UUID: ...

    @abstractmethod
    async def retrieve(self, credential_id: UUID) -> dict: ...

    @abstractmethod
    async def delete(self, credential_id: UUID) -> None: ...


class WebhookRegistry(ABC):
    """Dynamic webhook path ↔ workflow_id mapping.

    `register` mints a fresh `/webhooks/<uuid>` path. `resolve` is on the
    request hot path — Postgres impl must hit the unique index on `path`.
    """

    @abstractmethod
    async def register(
        self, workflow_id: UUID, *, secret: str | None = None
    ) -> WebhookBinding: ...

    @abstractmethod
    async def resolve(self, path: str) -> WebhookBinding | None: ...

    @abstractmethod
    async def unregister(self, path: str) -> None: ...


class ApprovalNotificationRepository(ABC):
    """PLAN_04 — append-only audit trail for ADR-007 approval notifications.

    Send failures are **independent** of the execution state machine: a
    failed SMTP/Slack delivery records `status='failed'` here but does not
    transition the execution out of `paused`. Ops dashboard polls
    `list_undelivered` to surface stuck notifications.
    """

    @abstractmethod
    async def record(self, notification: ApprovalNotification) -> None: ...

    @abstractmethod
    async def list_for_execution(
        self, execution_id: UUID
    ) -> list[ApprovalNotification]: ...

    @abstractmethod
    async def list_undelivered(
        self, *, older_than: timedelta
    ) -> list[ApprovalNotification]: ...


class ExecutionNodeLogRepository(ABC):
    """PLAN_03 — append-only detail log for per-node / per-attempt execution.

    Two-phase write:
      - `record_start` INSERTs a `running` row when a node begins.
      - `record_finish` UPDATEs that same row with terminal state + metrics.

    Partition key (`started_at`) is immutable, so the finish UPDATE never
    moves the row between partitions.
    """

    @abstractmethod
    async def record_start(self, log: ExecutionNodeLog) -> None: ...

    @abstractmethod
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
    ) -> None: ...

    @abstractmethod
    async def list_for_execution(
        self, execution_id: UUID
    ) -> list[ExecutionNodeLog]: ...

    @abstractmethod
    async def summarize_llm_usage(
        self, execution_id: UUID
    ) -> dict[str, dict]: ...


class NodeCatalogRepository(ABC):
    """Runtime node catalog — populated by `Execution_Engine` at startup."""

    @abstractmethod
    async def upsert_many(self, nodes: list[NodeDefinition]) -> None: ...

    @abstractmethod
    async def list_all(self) -> list[NodeDefinition]: ...
