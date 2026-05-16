# PLAN_06 — Execution list support (Database)

> **Branch**: `Database` · **Drafted**: 2026-04-16 · **Status**: Draft
>
> Provides the keyset-paginated `ExecutionRepository.list_by_workflow`
> method (plus the schema changes it depends on) consumed by API_Server
> PLAN_03 (`GET /workflows/{id}/executions`). A small extension.

## 1. Goals

1. Add the immutable **`created_at`** timestamp column to the `executions` table
2. Add the keyset index `(workflow_id, created_at DESC, id DESC)`
3. Add `list_by_workflow` to the `ExecutionRepository` ABC
4. Implement the same contract on the Postgres and InMemory backends
5. Tests

## 2. Scope

**In**
- `migrations/20260416_executions_created_at.sql`
- `schemas/001_core.sql` synced
- `models/core.py` Execution ORM — `created_at` column + index
- `repositories/base.py` Execution DTO — `created_at` field + ABC method
- `repositories/execution_repository.py` — Postgres `list_by_workflow`
- `tests/fakes.py` — InMemory `list_by_workflow`
- `tests/test_postgres_repositories.py` (or a new file) — keyset tests

**Out**
- API_Server routes/services — owned by PLAN_03
- Auto-assigning `created_at` on Execution creation — handled by `DEFAULT now()` in the DB
- DB partitioning — separate PLAN (traffic data not yet available)

## 3. Keyset pagination specification

```python
async def list_by_workflow(
    self,
    workflow_id: UUID,
    *,
    limit: int = 50,
    cursor: tuple[datetime, UUID] | None = None,
) -> list[Execution]:
```

- Sort: `created_at DESC, id DESC` (newest first; id breaks ties on simultaneous creation)
- cursor: `(created_at, id)` pair — `WHERE (created_at, id) < (?, ?)`
- First page: `cursor=None`
- Next-page cursor: the last row's `(created_at, id)`
- Wrapping the response as `{items, next_cursor, has_more}` is API_Server's
  responsibility (PLAN_03)

## 4. Avoid function sprawl

- Inline cursor unpacking (2–3 lines) inside `list_by_workflow`. No `_parse_cursor` helper.
- The Postgres implementation is a single `select` statement.
- Beyond a one-line `created_at` mapping in `_to_dto`, keep edits to existing code minimal.

## 5. Tests

1. **First page** — 5 rows, limit=3 → returns 3
2. **Cursor continuation** — cursor from the first page's last row → returns the remaining 2
3. **Empty result** — non-existent workflow_id → empty list
4. **Tiebreaker** — two rows with identical `created_at` → stable ordering via id DESC

## 6. Acceptance criteria

- [ ] After the migration runs, all existing tests still pass (no regression)
- [ ] The 4 new keyset tests pass
- [ ] The InMemory fake honors the same behavioral contract
- [ ] `created_at` is set as `NOT NULL DEFAULT now()` (auto-populated on `Execution.create`)
