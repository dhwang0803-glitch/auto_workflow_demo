# PLAN_04 — Approval notification audit trail

> **Branch**: `Database` · **Drafted**: 2026-04-15 · **Completed**: 2026-04-15 · **Status**: Done
>
> Persists where / when / with what outcome ADR-007's `ApprovalNode` 2-track
> notifications (email + slack) were actually dispatched. PLAN_04 does not
> cover "dispatch logic" — only **"how to store dispatch history"**. Actual
> dispatch is owned by `API_Server` or a separate worker, which records its
> attempts through this PLAN's Repository.

## 1. Goals

1. New `approval_notifications` table — one append-only row per attempt as an audit trail
2. `ApprovalNotificationRepository` ABC + Postgres/InMemory implementations
3. A partial-index path to power the undelivered (`queued`/`failed`) dashboard query
4. **The inbox (read path) is out of scope** — the inbox is just an `executions WHERE status='paused'` query, not its own table

## 2. Scope

**In**
- DDL: `approval_notifications` (simple table, no partitioning)
- Repository ABC + DTO + ORM + Postgres implementation + InMemory double
- Integration tests: append attempt / list undelivered / list by execution
- CHECK constraint matching ADR-007's channels (`email`, `slack`)

**Out (follow-up / other branches)**
- **Actual dispatch logic** — `API_Server` or a separate worker. This PLAN owns only the recording path
- **`NotificationChannel` adapters** — SMTP / Slack API calls. `API_Server` responsibility
- **Dispatch retry policy** — which branch owns it is a separate discussion
- **Operations dashboard UI** — Frontend responsibility. This PLAN only secures the query path
- **Partitioning** — not needed per the volume analysis (see §3)
- **Email / Slack ID reuse / GDPR delete policy** — discussed separately in an ops PLAN

## 3. Volume analysis (basis for the partitioning decision)

| Axis | Assumption | Value |
|---|---|---|
| Customers (MVP–Phase 1) | | 100 |
| Workflows per customer | | 30 |
| ApprovalNode adoption ratio | | 15% |
| Daily executions per approval node | | ~5 on average |
| **Daily approval events** | | ~2K |
| Avg notification rows per event | email + slack + 1 retry | 3 |
| **Annual `approval_notifications` rows** | | ~2.2M |
| Row size (incl. JSONB) | | ~300 B |
| **Annual table volume** | | ~0.7 GB |

**Conclusion**: a simple table + indexes (no partitioning) is enough.
ADR-011's "partition pre-emptively" philosophy applies to tables whose
volume actually balloons — like `execution_node_logs`, which multiplies
by N nodes per execution. This table is O(1–5) per event, so a simple
structure handles it until row counts reach the tens of millions
(≈10+ years of accumulation).

## 4. Table design

### 4.1 `approval_notifications`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid PK DEFAULT gen_random_uuid()` | |
| `execution_id` | `uuid NOT NULL REFERENCES executions(id) ON DELETE CASCADE` | |
| `node_id` | `text NOT NULL` | Same value as `paused_at_node` |
| `recipient` | `text NOT NULL` | Plain email address or Slack user id. **Stored in plaintext for query-throughput reasons** (GDPR deletion is a separate PLAN) |
| `channel` | `text NOT NULL` | CHECK `IN ('email','slack')` — ADR-007 2-track |
| `status` | `text NOT NULL` | CHECK `IN ('queued','sent','failed','bounced')` |
| `attempt` | `integer NOT NULL DEFAULT 1` | Explicitly passed in by the caller (which owns the retry loop) |
| `error` | `jsonb NULL` | Provider response / error message on failure |
| `sent_at` | `timestamptz NULL` | Only populated when `status='sent'` |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | Row-creation (attempt-start) timestamp |

### 4.2 Indexes

```sql
-- (a) Execution detail lookup: all notification history for a given execution / node
CREATE INDEX idx_approval_notif_execution
    ON approval_notifications (execution_id, node_id, created_at DESC);

-- (b) Undelivered dashboard: track only rows still in queued or failed
CREATE INDEX idx_approval_notif_undelivered
    ON approval_notifications (created_at)
    WHERE status IN ('queued', 'failed');
```

The partial index (b) is the key: it only contains "still-undelivered" rows,
so the dashboard query (`WHERE status IN ('queued','failed') AND created_at < now() - interval`)
finishes with a very small index scan.

### 4.3 Dispatch failure ↔ Approval state-machine separation

Dispatch failure does **not** affect `executions.status`. Reasoning:
- (+) If a transient SMTP / Slack outage propagated into workflow-execution state,
  notification-infra failures would balloon into automation failures.
- (+) Dispatch failures are recorded as this table's `status='failed'`, and ops
  handle them out-of-band through the undelivered dashboard (partial-index path).
- (−) Extreme scenario (every channel permanently fails) → approval waits go
  unnoticed → the ops dashboard's "undelivered notifications older than 24 h"
  alarm is the escalation route. That alarm itself is out of scope for this PLAN.

## 5. Repository

### 5.1 DTO

```python
@dataclass
class ApprovalNotification:
    id: UUID
    execution_id: UUID
    node_id: str
    recipient: str
    channel: Literal["email", "slack"]
    status: Literal["queued", "sent", "failed", "bounced"]
    attempt: int
    error: dict | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None
```

### 5.2 ABC

```python
class ApprovalNotificationRepository(ABC):
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
```

- `record` — append-only. The caller mints a fresh id per attempt.
- `list_for_execution` — for the execution detail view / audit log. Ordered by `(node_id, created_at DESC)`.
- `list_undelivered(older_than=timedelta(hours=24))` — ops dashboard. Partial-index path.

## 6. Deliverables

| Path | Content |
|------|---------|
| `schemas/004_approval_notifications.sql` | Table + CHECK + 2 indexes |
| `migrations/20260515_approval_notifications.sql` | Migration that includes 004 |
| `src/models/notifications.py` | SQLAlchemy ORM |
| `src/repositories/base.py` | Adds the ABC + DTO |
| `src/repositories/approval_notification_repository.py` | Postgres implementation |
| `tests/fakes.py` | Adds `InMemoryApprovalNotificationRepository` |
| `tests/test_approval_notifications.py` | Integration tests (append / list / undelivered filter) |
| `tests/test_schema_loads.py` | Adds `approval_notifications` to the expected-tables set |

## 7. Acceptance criteria

- [x] The 004 migration applies cleanly *(2026-04-15)*
- [x] `record()` appends and `list_for_execution()` returns DESC-ordered rows *(test_append_and_list_for_execution)*
- [x] `list_undelivered(older_than=timedelta(hours=1))` excludes fresh / sent rows and returns only old queued/failed *(test_list_undelivered_filters_by_age_and_status)*
- [x] CHECK constraints reject bad `channel` / `status` values *(test_check_constraints_reject_bad_values)*
- [x] `test_schema_loads` re-applies the full schema with 004 included and verifies `approval_notifications` exists

## 8. Open issues

1. **GDPR handling for `recipient`** — plaintext email storage is a
   performance-first decision. The deletion-request handling path is defined
   in the ops PLAN. The current DDL is sufficient for
   `DELETE FROM ... WHERE recipient = ?` alone.
2. **Slack user id vs email distinction** — the same `recipient` column mixes
   two formats. The current rule is to branch on `channel` at query time;
   if more structure is needed, promote it to JSONB in a future PLAN.
3. **Ownership of the `attempt` counter** — same principle as ADR-011: the
   caller (the dispatch worker) manages it in its own loop. The DB merely
   stores the value as supplied.
4. **Retention / deletion policy** — unbounded accumulation. At ~0.7 GB/year
   this is ignorable for now. A separate PLAN once volume crosses 10 GB.

## 9. Downstream PLAN impact

- **PLAN_05 (Agent re-encryption)** — unrelated.
- **Operations / dashboard PLAN** — polls `list_undelivered` on this table to fire alarms.
- **Actual dispatch logic** — `API_Server` or a worker. Receives this Repository via DI and calls `record()` on every dispatch attempt.
