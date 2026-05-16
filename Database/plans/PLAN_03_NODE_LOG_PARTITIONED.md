# PLAN_03 — Per-node execution log split (with partitioning)

> **Branch**: `Database` · **Drafted**: 2026-04-15 · **Completed**: 2026-04-15 · **Status**: Done
>
> Carries out PLAN_01 §7 risk #3's deferred work: move the "detailed execution
> log" out of `executions.node_results` JSONB into a dedicated table.
> Retry history is captured as N rows per attempt, and monthly RANGE
> partitioning is introduced now to avoid taking on later partition-adoption
> tech debt. Raw stdout / stderr are kept out of the DB — we store only GCS
> URIs.

## 1. Goals

1. New `execution_node_logs` partitioned table — monthly RANGE on `started_at`
2. Promote the 4 LLM-observability essentials (`model`, `tokens_prompt`,
   `tokens_completion`, `cost_usd`) to columns instead of JSONB — for
   aggregate-query performance
3. stdout / stderr are GCS URI references only (`stdout_uri`, `stderr_uri text NULL`)
4. `ExecutionNodeLogRepository` ABC + Postgres / InMemory implementations
5. Create 12 monthly partitions up front; `scripts/roll_partitions.py` run
   once a month keeps the next N months provisioned (the cron itself is the
   deployment side's responsibility)

## 2. Scope

**In**
- DDL: `execution_node_logs` partition parent + 12 monthly partitions (current month + 11 ahead)
- Indexes: `(execution_id, node_id, attempt DESC)`, partial index `(model) WHERE model IS NOT NULL`
- `ExecutionNodeLogRepository` ABC + DTO + ORM
- `PostgresExecutionNodeLogRepository`, `InMemoryExecutionNodeLogRepository`
- `scripts/roll_partitions.py` — monthly partition create-if-missing
- Integration tests: append/list/summarize + partition-routing smoke

**Out (follow-up)**
- GCS uploader implementation — `Execution_Engine` branch responsibility
- Partition retention / deletion policy (90 days / 1 year / unlimited) → separate ops PLAN
- Global log search (grep-like) → revisit once an observability stack (Loki / ELK) is adopted
- LLM-usage dashboard queries — pin down the aggregation patterns first, then consider views / materialized views

## 3. Table design

### 3.1 `execution_node_logs` — partition parent

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid NOT NULL DEFAULT gen_random_uuid()` | |
| `execution_id` | `uuid NOT NULL REFERENCES executions(id) ON DELETE CASCADE` | |
| `node_id` | `text NOT NULL` | Node-instance id from `workflows.graph` |
| `attempt` | `int NOT NULL DEFAULT 1` | 1-based. Increments on each retry |
| `status` | `text NOT NULL` | CHECK `('running','success','failed','skipped')` |
| `started_at` | `timestamptz NOT NULL` | **Partition key** |
| `finished_at` | `timestamptz NULL` | Set on completion |
| `duration_ms` | `integer NULL` | |
| `input` | `jsonb NULL` | Node input snapshot (summarize recommended) |
| `output` | `jsonb NULL` | Node output summary |
| `error` | `jsonb NULL` | `{"type":..., "message":..., "traceback":...}` |
| `stdout_uri` | `text NULL` | `gs://bucket/executions/{exec}/{node}/{attempt}/stdout.log` |
| `stderr_uri` | `text NULL` | Same shape |
| `model` | `text NULL` | LLM nodes only |
| `tokens_prompt` | `integer NULL` | LLM nodes only |
| `tokens_completion` | `integer NULL` | LLM nodes only |
| `cost_usd` | `numeric(10,6) NULL` | LLM nodes only |

**PK**: `(id, started_at)` — Postgres native partitioning requires UNIQUE
constraints to include the partition key. `id` alone can't enforce UNIQUE.

**Partitioning**: `PARTITION BY RANGE (started_at)`. Monthly partitions named
`execution_node_logs_YYYY_MM`.

**FK constraint**: `executions(id) ON DELETE CASCADE` on the partition parent.
Postgres 12+ supports partitioned tables on the referencing side (FK holder).

### 3.2 Indexes

```sql
CREATE INDEX idx_enl_execution
    ON execution_node_logs (execution_id, node_id, attempt DESC);

CREATE INDEX idx_enl_model
    ON execution_node_logs (model)
    WHERE model IS NOT NULL;
```

Postgres 11+ auto-propagates parent-table indexes to all child partitions.

### 3.3 Initial partitions

The migration creates 12 monthly partitions (current month included). The
specific months follow the same create-if-missing logic as
`roll_partitions.py`.

## 4. Repository

### 4.1 ABC + DTO

```python
@dataclass
class ExecutionNodeLog:
    id: UUID
    execution_id: UUID
    node_id: str
    attempt: int
    status: Literal["running","success","failed","skipped"]
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

class ExecutionNodeLogRepository(ABC):
    @abstractmethod
    async def record(self, log: ExecutionNodeLog) -> None: ...
    @abstractmethod
    async def list_for_execution(
        self, execution_id: UUID
    ) -> list[ExecutionNodeLog]: ...
    @abstractmethod
    async def summarize_llm_usage(
        self, execution_id: UUID
    ) -> dict[str, dict]: ...
```

`summarize_llm_usage` return shape:
`{"gpt-4o": {"tokens_prompt": N, "tokens_completion": M, "cost_usd": X, "calls": K}, ...}`

### 4.2 Relationship with `ExecutionRepository`

PLAN_01 §7 risk #3 decision (option A retained):
`executions.node_results` continues to hold **only the latest-attempt summary**.
Detailed logs are owned solely by `execution_node_logs`. The caller
(`Execution_Engine`) writes to both Repositories — this PLAN doesn't enforce
the write path, only provides the Repository signatures.

## 5. Deliverables

| Path | Content |
|------|---------|
| `schemas/003_node_logs_partitioned.sql` | Partition parent + 12 partitions + indexes |
| `migrations/20260501_node_logs_partitioned.sql` | Migration including 003 |
| `src/models/logs.py` | SQLAlchemy ORM (parent table only mapped; partitions routed on the DB side) |
| `src/repositories/base.py` | Adds the `ExecutionNodeLogRepository` ABC + DTO |
| `src/repositories/execution_node_log_repository.py` | Postgres implementation |
| `tests/fakes.py` | Adds `InMemoryExecutionNodeLogRepository` |
| `scripts/roll_partitions.py` | create-if-missing for the next N months |
| `tests/test_execution_node_logs.py` | Integration tests (append/list/summarize + partition-routing smoke) |

## 6. Acceptance criteria

- [x] The 003 migration applies cleanly and creates 12 monthly partitions *(pg_inherits count=12)*
- [x] `pg_inherits` lookup confirms the parent-child relationship
- [x] `record_start`/`record_finish` 2-phase flow + insert 3 attempts; `list_for_execution()` returns them in `(node_id, attempt DESC)` order *(test_two_phase_write_and_retry_ordering)*
- [x] `summarize_llm_usage()` correctly aggregates token / cost / call counts per model *(test_llm_usage_summarization)*
- [x] Two logs with `started_at` in different months land in different partitions
      (verified via `tableoid::regclass`) *(test_rows_land_in_expected_month_partitions)*
- [x] `roll_partitions.py --months 6` is idempotent (create-if-missing)
- [x] `test_schema_loads` reapplies the full schema with 003 included and confirms `execution_node_logs` is present

## Implementation notes (2026-04-15)

- **`timestamptz` mapping**: `Mapped[datetime]` alone makes SQLAlchemy send
  `TIMESTAMP WITHOUT TIME ZONE`. To insert tz-aware Python datetimes, declare
  `DateTime(timezone=True)` explicitly. Hit this once during PLAN_03 work.
- **Multi-statement DDL execution**: `schemas/003` uses a `DO $$ ... $$` block
  that breaks naïve `;`-split. `test_schema_loads` now uses a raw asyncpg
  connection's `.execute()` (simple query protocol) — the SQLAlchemy wrapper
  goes through the prepared-statement path which doesn't allow multi-statements.
- **Partitioned UPDATE path**: the `record_finish` UPDATE WHERE clause must
  specify both `(id, started_at)`. With `id` alone Postgres scans every
  partition (partition pruning fails).

## 7. Risks & open issues

1. **FK integrity cost** — the FK to `executions(id)` on the partitioned
   table does a parent lookup on every INSERT. At high insert volume this
   can become a bottleneck. After observing, reconsider switching to
   "trigger-based validation". MVP keeps the FK.

2. **Partition retention / deletion policy TBD** — unbounded accumulation.
   An ops decision is needed eventually but it's out of scope for PLAN_03.
   We deliberately omit a delete option from `roll_partitions.py` — data
   deletion is an explicit operational event.

3. **`input` / `output` JSONB size** — large payloads inflate partition
   volume. The rule is "summary in JSONB, raw in GCS URI". This is the
   application layer's responsibility; the DB has no CHECK constraint.

4. **`roll_partitions.py` automation** — this PLAN ships only the script.
   The cron / scheduler (e.g., K8s CronJob, GCP Cloud Scheduler) is a
   deployment-side responsibility.

5. **GCS URI validation** — `stdout_uri` / `stderr_uri` are free-form text.
   URI-shape validation (e.g., `gs://` prefix) lives at the application layer.

## 8. Downstream PLAN impact

- **PLAN_04 (notification history)** — can read this table to construct
  alerts like "last 10 failures". No direct coupling.
- **PLAN_05 (Agent re-encryption)** — unrelated.
- **PLAN_06 (RAG)** — past workflow success-log outputs can serve as
  embedding sources. This PLAN's schema doesn't block that use case.
