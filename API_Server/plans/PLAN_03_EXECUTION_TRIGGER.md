# PLAN_03 — Manual execution trigger + Execution history queries (API_Server)

> **Branch**: `API_Server` · **Drafted**: 2026-04-16 · **Status**: Draft
>
> Layers execution triggering and history queries onto the PLAN_02 Workflow
> CRUD foundation. Execution is asynchronous (202 Accepted) — we create an
> `executions` row in `queued` state and immediately return its
> `execution_id`. The actual dispatch (Celery / Agent) belongs to the
> Execution_Engine branch, so we stub it here. Scheduler-based automatic
> execution (activate/deactivate) is split out into PLAN_04.

## 1. Goals

1. `POST /workflows/{id}/execute` — manual execution trigger (202 + execution_id)
2. `GET /executions/{id}` — read a single execution
3. `GET /workflows/{id}/executions` — per-workflow execution list (keyset pagination)
4. Inject `PostgresExecutionRepository` in `main.py` lifespan
5. `execution_mode` dispatch is a **stub** — row creation only, no actual queuing / push

## 2. Scope

**In**
- Pydantic: `ExecutionResponse`, `ExecutionListResponse` (items / next_cursor / has_more)
- `app/services/workflow_service.py` extension — `execute_workflow`, `get_execution`, `list_executions`
- `app/routers/executions.py` (new) — history-query router
- `app/routers/workflows.py` extension — adds `POST /{id}/execute`
- `app/dependencies.py` extension — `get_execution_repo`
- `app/main.py` extension — `PostgresExecutionRepository` lifespan injection + router registration
- `app/errors.py` extension — `WorkflowNotActiveError` (reject execution attempts on an inactive workflow)
- `tests/test_executions.py` (new) — E2E tests

**Out (follow-up PLANs)**
- Scheduler worker + activate/deactivate — **PLAN_04**
- Actual Celery queuing / Agent WebSocket push — **Execution_Engine branch**
- Execution cancel (`POST /executions/{id}/cancel`) — Phase 2
- Per-node execution log detail (`GET /executions/{id}/logs`) — Phase 2

## 3. Predecessor decisions (locked)

| Decision | Locked content | Rationale |
|---|---|---|
| Execution response | Async 202 + `execution_id` | Handles long workflows, natural Execution_Engine separation |
| Pagination | Keyset (`created_at DESC, id DESC`) | Append-only time series; DB support landed in PLAN_06 |
| Scheduler split | Split into PLAN_04 | Separate deployment unit (process); volume too large |
| execution_mode | Stub (`# TODO(Execution_Engine)`) | Celery / Agent not yet implemented |

## 4. Endpoints

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| `POST` | `/api/v1/workflows/{id}/execute` | Manual execution trigger | 202 `ExecutionResponse` |
| `GET` | `/api/v1/executions/{id}` | Single execution | 200 `ExecutionResponse` / 404 |
| `GET` | `/api/v1/workflows/{id}/executions` | Per-workflow execution list | 200 `ExecutionListResponse` |

**Error codes**:

| Condition | HTTP |
|-----------|------|
| Workflow missing / no ownership | 404 |
| Execution attempt on an inactive workflow | 409 Conflict |
| Execution missing | 404 |
| Auth failure | 401 |

## 5. Pydantic schemas

```python
class ExecutionResponse(BaseModel):
    id: UUID
    workflow_id: UUID
    status: str
    execution_mode: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime | None
    error: dict | None

class ExecutionListResponse(BaseModel):
    items: list[ExecutionResponse]
    next_cursor: str | None
    has_more: bool
```

- `next_cursor` is encoded as the string `"{created_at_iso}_{id}"`. The
  router unpacks it inline with `split("_", 1)` — no `_parse_cursor` helper.
- `node_results`, `token_usage`, `cost_usd`, `duration_ms` are excluded
  from the list response (included only on the single-row endpoint). To
  avoid a separate `ExecutionDetailResponse`, mark the fields on the list
  `ExecutionResponse` Optional and use
  `model_config = ConfigDict(from_attributes=True)`.

## 6. Service logic (`WorkflowService` extension)

### execute_workflow(workflow_id, user)
Walk through inside a single function:
1. `workflow_repo.get(workflow_id)` → missing or `owner_id != user.id` → `WorkflowNotFoundError` (404)
2. `workflow.is_active == False` → `WorkflowNotActiveError` (409)
3. Build the DTO `Execution(id=uuid4(), workflow_id=..., status="queued", execution_mode=workflow.settings.get("execution_mode", user.default_execution_mode))`
4. `execution_repo.create(execution)`
5. `# TODO(Execution_Engine): dispatch based on execution_mode`
6. return execution

**Function-sprawl prevention**: no single-use private methods like
`_validate_ownership`, `_check_active`, `_create_execution`. The 5 steps
above run inline.

### get_execution(execution_id, user)
1. `execution_repo.get(execution_id)` → 404 if missing
2. `workflow_repo.get(execution.workflow_id)` → ownership check → 404 on mismatch
3. return execution

### list_executions(workflow_id, user, limit, cursor)
1. Ownership check (`workflow_repo.get` → compare `owner_id`)
2. `execution_repo.list_by_workflow(workflow_id, limit=limit, cursor=cursor)`
3. return executions

## 7. main.py changes

```python
from auto_workflow_database.repositories.execution_repository import (
    PostgresExecutionRepository,
)

# inside lifespan:
execution_repo = PostgresExecutionRepository(sessionmaker)
app.state.execution_repo = execution_repo
app.state.workflow_service = WorkflowService(
    repo=workflow_repo, execution_repo=execution_repo, settings=s
)
```

Add `execution_repo` to the `WorkflowService` constructor. Update the
existing tests' `WorkflowService` fixtures accordingly.

## 8. Function-sprawl-prevention guardrails

- Add 3 methods to `WorkflowService` (`execute_workflow`, `get_execution`,
  `list_executions`). No new `ExecutionService` class — 3 methods can't yet
  justify a standalone class.
- Router-side cursor parsing is 2 inline lines. No `_parse_cursor` /
  `_encode_cursor` helpers.
- Errors `raise` `DomainError` subclasses. No `_raise_*` wrappers (PR #21 principle).
- Pydantic schemas extend existing files under `app/models/` or add at most
  one new file. No `schemas.py` / `request_models.py` / `response_models.py`
  split.

## 9. Tests

1. `test_execute_workflow_creates_queued_execution` — 202 + status=queued
2. `test_execute_workflow_not_owned_returns_404`
3. `test_execute_inactive_workflow_returns_409`
4. `test_get_execution_happy`
5. `test_get_execution_not_owned_returns_404`
6. `test_list_executions_returns_keyset_response`
7. `test_list_executions_cursor_pagination`
8. `test_list_executions_empty`

## 10. Acceptance criteria

- [ ] The 8 new tests pass
- [ ] No regression in the existing 34 API_Server tests (total 42+)
- [ ] Verified that `POST /execute` returns 202 and creates a `queued` row in the DB
- [ ] `GET /executions/{id}` returns after ownership check
- [ ] `GET /workflows/{id}/executions` returns the `{items, next_cursor, has_more}` wrapper
- [ ] Continuing with the cursor for page 2 has no duplicates or gaps
- [ ] 0 single-use private helpers in `WorkflowService`
- [ ] 0 `try/except` in the router (delegated to the DomainError global handler)
- [ ] The `# TODO(Execution_Engine): dispatch based on execution_mode` comment is present

## 11. Downstream impact

- **PLAN_04 (API_Server)** — `POST /workflows/{id}/activate` and
  `/deactivate`. Reuses this PLAN's `WorkflowService` + `execution_repo`
  injection pattern. The APScheduler worker internally calls this PLAN's
  `execute_workflow`.
- **Execution_Engine branch** — first task is to wire `Celery task.delay()`
  or `AgentManager.dispatch()` at the `# TODO` comment site.
- **Frontend** — dashboard "Run" button → `POST /execute` → 202 → poll
  `GET /executions/{id}`. Lists use infinite scroll + keyset cursor.

## 12. Predecessor work (done)

- [x] Database PLAN_06 (PR #25) — `created_at` column, keyset index, `list_by_workflow` method
- [x] Database PLAN_07 (PR #22) — engine resilience + query logging
- [x] API_Server DBAPIError → 503 handler (PR #24)

## 13. Work order

1. Write the PLAN_03 document (this document) ✓
2. Bring main-branch changes (PLAN_06/07 etc.) over to API_Server
3. Add the Pydantic schemas
4. Add `WorkflowNotActiveError` to `app/errors.py`
5. Add the 3 `WorkflowService` methods + an `execution_repo` constructor parameter
6. Add `POST /{id}/execute` to `app/routers/workflows.py`
7. New `app/routers/executions.py` — single + list
8. Extend `app/dependencies.py` + `app/main.py`
9. Write `tests/test_executions.py`
10. Verify tests pass locally
11. Open PR → review → merge
