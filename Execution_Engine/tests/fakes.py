"""InMemory fakes for Execution_Engine tests."""
from auto_workflow_database.repositories.base import (
    AgentCredentialPayload,
    CredentialStore,
    Execution, ExecutionRepository, ExecutionStatus,
    Workflow, WorkflowRepository,
)
from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid4


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


class InMemoryWorkflowRepository(WorkflowRepository):
    """Minimal fake — only methods used by dispatcher tests."""

    def __init__(self) -> None:
        self._store: dict[UUID, Workflow] = {}

    async def get(self, workflow_id: UUID) -> Workflow | None:
        wf = self._store.get(workflow_id)
        return deepcopy(wf) if wf else None

    async def save(self, workflow: Workflow) -> None:
        self._store[workflow.id] = deepcopy(workflow)

    async def list_by_owner(self, owner_id, *, active_only=True):
        return []

    async def delete(self, workflow_id):
        self._store.pop(workflow_id, None)


class InMemoryCredentialStore(CredentialStore):
    """Minimal fake mirroring the Database branch's contract — unit tests only."""

    def __init__(self) -> None:
        # credential_id -> (owner_id, plaintext)
        self._store: dict[UUID, tuple[UUID, dict]] = {}

    async def store(
        self,
        owner_id: UUID,
        name: str,
        plaintext: dict,
        *,
        credential_type: str = "unknown",
    ) -> UUID:
        cid = uuid4()
        self._store[cid] = (owner_id, deepcopy(plaintext))
        return cid

    async def retrieve(self, credential_id: UUID) -> dict:
        return deepcopy(self._store[credential_id][1])

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
            if row is None or row[0] != owner_id:
                continue
            found[cid] = deepcopy(row[1])
        if len(found) != len(set(credential_ids)):
            raise KeyError("missing credential(s)")
        return found

    async def delete(self, credential_id: UUID) -> None:
        self._store.pop(credential_id, None)

    async def list_by_owner(self, owner_id: UUID):
        # Metadata-only response (plaintext must never leak). owner_id filters.
        from datetime import datetime, timezone

        from auto_workflow_database.repositories.base import CredentialMetadata
        out: list[CredentialMetadata] = []
        for cid, (oid, _plaintext) in self._store.items():
            if oid == owner_id:
                out.append(
                    CredentialMetadata(
                        id=cid,
                        name="",
                        type="unknown",
                        created_at=datetime.now(timezone.utc),
                    )
                )
        return out

    async def retrieve_for_agent(
        self,
        credential_id: UUID,
        *,
        agent_public_key_pem: bytes,
    ) -> AgentCredentialPayload:
        raise NotImplementedError("agent path out of scope for PLAN_08 tests")

    async def list_by_owner(self, owner_id: UUID):
        # Metadata-only response (plaintext must never leak). owner_id filters.
        from datetime import datetime, timezone

        from auto_workflow_database.repositories.base import CredentialMetadata
        out: list[CredentialMetadata] = []
        for cid, (oid, _plaintext) in self._store.items():
            if oid == owner_id:
                out.append(
                    CredentialMetadata(
                        id=cid,
                        name="",
                        type="unknown",
                        created_at=datetime.now(timezone.utc),
                    )
                )
        return out

    # ADR-019 — Google OAuth lifecycle. Mirrors the Postgres impl's shape:
    # refresh_token stays in plaintext[ "refresh_token" ] (simulating the
    # Fernet-encrypted encrypted_data column), mutable tokens live on
    # plaintext["oauth_metadata"] (JSONB in prod).
    async def store_google_oauth(
        self, owner_id: UUID, name: str, *, refresh_token: str, oauth_metadata: dict,
    ) -> UUID:
        cid = uuid4()
        self._store[cid] = (
            owner_id,
            {"refresh_token": refresh_token, "oauth_metadata": deepcopy(oauth_metadata)},
        )
        return cid

    async def update_oauth_tokens(
        self, credential_id: UUID, *, access_token: str,
        token_expires_at: datetime, refresh_token: str | None = None,
    ) -> None:
        owner_id, plaintext = self._store[credential_id]
        md = dict(plaintext.get("oauth_metadata") or {})
        md["access_token"] = access_token
        md["token_expires_at"] = token_expires_at.isoformat()
        md.pop("needs_reauth", None)
        plaintext = dict(plaintext)
        plaintext["oauth_metadata"] = md
        if refresh_token is not None:
            plaintext["refresh_token"] = refresh_token
        self._store[credential_id] = (owner_id, plaintext)

    async def mark_needs_reauth(self, credential_id: UUID) -> None:
        owner_id, plaintext = self._store[credential_id]
        md = dict(plaintext.get("oauth_metadata") or {})
        md["needs_reauth"] = True
        plaintext = dict(plaintext)
        plaintext["oauth_metadata"] = md
        self._store[credential_id] = (owner_id, plaintext)
