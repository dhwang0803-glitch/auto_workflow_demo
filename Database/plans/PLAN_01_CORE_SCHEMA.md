# PLAN_01 — Core Schema + Routing Fields (b′)

> **Branch**: `Database` · **Drafted**: 2026-04-14 · **Completed**: 2026-04-15 · **Status**: Done
>
> Locks in the first schema file and the Repository ABC skeleton. Scope is
> the minimum set of fields needed to satisfy ADR-001/007/008.
> `credentials` / `agents` / `webhook_registry` and `gpu_info`-based routing
> (ADR-009, Proposed) are deferred to PLAN_02.

## 1. Goals

1. Lock down the DDL for the 4 core tables `users / workflows / nodes / executions`
2. Define signatures for the 3 Repository ABCs (ADR-006)
3. Include the **minimum user columns** required for `API_Server`'s plan-based
   LLM routing (ADR-008)
4. Reflect the observability / approval state columns ADR-007 imposes on `executions`

## 2. Scope

**In**
- DDL: `users`, `workflows`, `nodes`, `executions`
- User plan tier + default execution mode + external-API policy columns (ADR-008 / ADR-009)
- Approval state machine + LLM observability columns on `executions` (ADR-007)
- 3 Repository ABCs + `InMemory*` test-double signatures
- Initial migration file (`20260414_initial_schema.sql`)

**Out (follow-up PLANs)**
- `credentials`, `agents`, `webhook_registry` → PLAN_02
- `users.gpu_info` and Agent hardware routing (ADR-009) → with `agents` in PLAN_02
- Postgres implementations (`PostgresWorkflowRepository`, etc.) → after PLAN_02
- Detailed execution logs (per-node stdout/stderr, separately stored structured logs) → PLAN_03
- Inference_Service-related schema (if any) → separate PLAN

## 3. Table design

### 3.1 `users` — ADR-001/008 routing basis

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid PK DEFAULT gen_random_uuid()` | |
| `email` | `citext UNIQUE NOT NULL` | |
| `plan_tier` | `text NOT NULL` | CHECK `IN ('light','middle','heavy')` — ADR-008 routing key |
| `default_execution_mode` | `text NOT NULL DEFAULT 'serverless'` | CHECK `IN ('serverless','agent')` — ADR-001 |
| `external_api_policy` | `jsonb NOT NULL DEFAULT '{}'::jsonb` | Org policy (e.g., `{"allow_outbound": false}`). The basis for ADR-009 fallback allow/deny |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |

> `gpu_info` is **out of scope for this PLAN**. ADR-009's body states it is
> *"collected once at Agent boot"*, so it belongs on the `agents` table.
> Handled together with PLAN_02.
>
> **`external_api_policy` finalized spec (2026-04-15)**
>
> The MVP contract has **a single key, `allow_outbound: boolean`**. The
> default on absence is `false` (conservative). `API_Server` trusts only
> this key when deciding ADR-009 external-API fallback eligibility.
>
> Undefined keys are **allowed on write but ignored on read** for forward
> compatibility, with a `WARN` log. Extension keys like domain allow / deny
> lists are added by the PLAN that actually needs the enforcement logic,
> which also updates this section.

### 3.2 `workflows`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid PK` | |
| `owner_id` | `uuid REFERENCES users(id) ON DELETE CASCADE` | |
| `name` | `text NOT NULL` | |
| `settings` | `jsonb NOT NULL` | `{ "execution_mode": "serverless"|"agent", ... }` (ADR-001) |
| `graph` | `jsonb NOT NULL` | Nodes / connections (React Flow format). **Each node definition includes an `output_schema` field** (ADR-007) |
| `is_active` | `boolean NOT NULL DEFAULT true` | |
| `created_at` / `updated_at` | `timestamptz NOT NULL DEFAULT now()` | |

Indexes:
- `CREATE INDEX idx_workflows_owner ON workflows(owner_id) WHERE is_active = true;`

> `output_schema` is not a separate column — it lives inside the `graph`
> JSONB as a node-instance attribute (ADR-007 Decision 1: "the schema
> travels with the data throughout the entire lifecycle in which the
> workflow is serialized as JSON"). The Repository contract documents this
> invariant — there is no DDL-level enforcement.

### 3.3 `nodes` — runtime node catalog

| Column | Type | Notes |
|--------|------|-------|
| `type` | `text` | e.g., `'http.request'`, `'llm'`, `'approval'` |
| `version` | `text` | semver |
| `schema` | `jsonb NOT NULL` | Parameter schema (for Frontend form rendering) |
| `registered_at` | `timestamptz NOT NULL DEFAULT now()` | |

Composite PK `(type, version)`.

> Node instances embedded in the user's graph live in `workflows.graph`;
> this table is the catalog of "node types the engine knows about". It's
> the path the Frontend hits to fetch the available node palette.

### 3.4 `executions` — ADR-007 observability / approval extensions

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid PK` | |
| `workflow_id` | `uuid REFERENCES workflows(id) ON DELETE CASCADE` | |
| `status` | `text NOT NULL` | CHECK `IN ('queued','running','paused','resumed','success','failed','rejected','cancelled')` |
| `execution_mode` | `text NOT NULL` | Frozen at execution time (history preserved across later workflow-config changes) |
| `started_at` | `timestamptz NULL` | |
| `finished_at` | `timestamptz NULL` | |
| `node_results` | `jsonb NOT NULL DEFAULT '{}'::jsonb` | Per-node result summary (detailed logs in PLAN_03) |
| `error` | `jsonb NULL` | `{"node_id":..., "message":...}` |
| `token_usage` | `jsonb NOT NULL DEFAULT '{}'::jsonb` | `{"prompt": N, "completion": M}` or per-model detail — ADR-007 |
| `cost_usd` | `numeric(10,6) NOT NULL DEFAULT 0` | Running total. Local vLLM path is 0 or amortized — ADR-007 |
| `duration_ms` | `integer NULL` | Computed on completion — ADR-007 |
| `paused_at_node` | `text NULL` | Node id where ApprovalNode is waiting — ADR-007 state machine |

Indexes:
- `CREATE INDEX idx_executions_workflow_id ON executions(workflow_id, started_at DESC);`
- `CREATE INDEX idx_executions_paused ON executions(paused_at_node) WHERE status = 'paused';` — Approval Inbox query path

> **State transitions (ADR-007 ApprovalNode)**:
> `queued → running → (paused ↔ resumed) → success | failed | rejected | cancelled`
>
> `resumed` is a transitory state. Repository resume calls must be idempotent.
> The approval-waiting lifetime is **independent** of ADR-005's 30-second
> hard timeout.

## 4. Repository ABCs

`Database/src/repositories/base.py` (new):

```python
from abc import ABC, abstractmethod
from uuid import UUID

class WorkflowRepository(ABC):
    @abstractmethod
    async def get(self, workflow_id: UUID) -> Workflow | None: ...
    @abstractmethod
    async def save(self, workflow: Workflow) -> None: ...
    @abstractmethod
    async def list_by_owner(self, owner_id: UUID, *, active_only: bool = True) -> list[Workflow]: ...
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
    # Signatures only. Real implementation lands in PLAN_02 (ADR-004 Fernet).
    @abstractmethod
    async def store(self, owner_id: UUID, name: str, plaintext: dict) -> UUID: ...
    @abstractmethod
    async def retrieve(self, credential_id: UUID) -> dict: ...
```

The `InMemoryWorkflowRepository` and `InMemoryExecutionRepository` test
doubles ship in `Database/tests/fakes.py`. They must be enough — on their
own — for `API_Server` unit tests to validate plan routing (`users.plan_tier`
→ vLLM / external API) and Approval resume flows.

## 5. Deliverables / files

| Path | Content |
|------|---------|
| `Database/schemas/001_core.sql` | DDL for the 4 tables above + indexes + CHECK constraints |
| `Database/migrations/20260414_initial_schema.sql` | Initial migration including `001_core.sql` |
| `Database/src/models/core.py` | SQLAlchemy ORM (users/workflows/nodes/executions) |
| `Database/src/repositories/base.py` | The ABCs above |
| `Database/tests/fakes.py` | `InMemoryWorkflowRepository`, `InMemoryExecutionRepository` |
| `Database/tests/test_schema_loads.py` | Smoke test that the migration applies to an empty DB |
| `Database/tests/test_status_transitions.py` | Approval state-machine path tests (in-memory) |

## 6. Acceptance criteria

- [x] `psql -f schemas/001_core.sql` applies to an empty DB without errors *(test_schema_loads passes, 2026-04-15)*
- [x] All CHECK constraints (plan_tier, execution_mode, status) reject bad values *(test_schema_loads)*
- [x] The following scenarios pass as in-memory unit tests on `InMemoryExecutionRepository`:
  - [x] queued → running → paused (paused_at_node set) → resumed → success
  - [x] running → failed (error set)
  - [x] paused → rejected
- [x] `API_Server` can make ADR-008 plan-routing decisions using
      `users.plan_tier` + `users.external_api_policy` alone (lookup path exists) — `external_api_policy` key spec locked in
- [x] All Repository methods are async (consistent with ADR-002 FastAPI async)

## 7. Risks & open issues

1. ~~**`external_api_policy` key naming TBD**~~ → **Resolved (2026-04-15)**
   Single key `allow_outbound: boolean`, default `false`, undefined keys
   ignored + WARN. See §3.1.

2. **`nodes` catalog vs `NodeRegistry` sync**
   If `Execution_Engine`'s runtime `NodeRegistry` and the DB `nodes` drift,
   the Frontend can render node types that don't exist. The
   `NodeRegistry → upsert nodes` path at startup is defined within PLAN_02's
   scope.

3. **`executions.node_results` row bloat**
   Accumulating large results in JSONB makes rows fat. For now keep the
   "summary only" rule as a documented convention; detailed-log split storage
   lives in PLAN_03.

4. **`cost_usd` aggregation unit**
   The local vLLM (ADR-008) path has no explicit per-call price. In Phase 1
   record 0; in Phase 2, agree on amortized pricing and decide whether to
   backfill.

5. **Lifetime of the `resumed` state**
   Strictly speaking a transient state. We keep it for implementation
   convenience but the contract spells out that
   Repository `update_status(resumed)` immediately re-transitions to `running`.

## 8. Follow-up PLAN preview

- **PLAN_02** — `credentials` (ADR-004 Fernet) + `agents` (+ `gpu_info`, for ADR-009) + `webhook_registry` + Postgres implementations
- **PLAN_03** — Execution observability detail: per-node split log storage, retry history, Approval-notification dispatch history
