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
from datetime import datetime
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


class NodeCatalogRepository(ABC):
    """Runtime node catalog — populated by `Execution_Engine` at startup."""

    @abstractmethod
    async def upsert_many(self, nodes: list[NodeDefinition]) -> None: ...

    @abstractmethod
    async def list_all(self) -> list[NodeDefinition]: ...
