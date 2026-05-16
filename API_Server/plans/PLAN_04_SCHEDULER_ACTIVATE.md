# PLAN_04 — Scheduler worker + Activate / Deactivate (API_Server)

> **Branch**: `API_Server` · **Drafted**: 2026-04-16 · **Status**: Draft
>
> Layers **schedule-based automatic execution** on top of PLAN_03's manual
> execution. APScheduler runs as a separate process so it works safely
> without duplicate firings even in multi-worker environments. The API
> processes only handle job registration / removal; the actual firing is
> done by the Scheduler worker.

## 1. Goals

1. `POST /workflows/{id}/activate` — register a cron/interval trigger
2. `POST /workflows/{id}/deactivate` — remove the trigger
3. Add a `trigger_status` field (active / inactive) to the `GET /workflows/{id}` response
4. Run a separate Scheduler worker process (`python -m app.scheduler`)
5. Persist jobs in the DB via `SQLAlchemyJobStore` — auto-restored on restart

## 2. Scope

**In**
- `app/scheduler.py` (new) — standalone entrypoint, `AsyncIOScheduler` + `SQLAlchemyJobStore`
- `app/services/workflow_service.py` extension — `activate_workflow`, `deactivate_workflow`
- `app/routers/workflows.py` extension — activate / deactivate endpoints
- `app/models/workflow.py` extension — `ActivateRequest` (trigger_type, cron / interval settings)
- `app/config.py` extension — `scheduler_jobstore_url` (default: `database_url` with `+asyncpg` stripped)
- `migrations/` — the `apscheduler_jobs` table is auto-created by APScheduler (`create_all`)
- `tests/test_scheduler.py` (new)

**Out**
- Webhook triggers — PLAN_05
- Agent WebSocket — PLAN_06
- Real Celery / Agent dispatch — Execution_Engine
- Job-execution history dashboard — Phase 2
- Concurrent-activate prevention (distributed lock) — Phase 2 (DB unique constraint is enough for now)

## 3. Architecture

```
┌──────────────┐     add_job / remove_job      ┌─────────────────────┐
│  API_Server  │ ──────────────────────────────▶│  apscheduler_jobs   │
│  (FastAPI)   │     (SQLAlchemyJobStore)       │  (PostgreSQL table) │
└──────────────┘                                └──────────┬──────────┘
                                                           │ poll
                                                ┌──────────▼──────────┐
                                                │  Scheduler Worker   │
                                                │  (python -m         │
                                                │   app.scheduler)    │
                                                └──────────┬──────────┘
                                                           │ direct call
                                                ┌──────────▼──────────┐
                                                │ WorkflowService     │
                                                │ .execute_workflow() │
                                                └─────────────────────┘
```

- API and Scheduler **share the same DB** but are **different processes**
- The Scheduler worker calls `execute_workflow` directly (not an HTTP self-request)
- Even when multiple API workers `add_job`, the single Scheduler worker prevents duplicate firings

## 4. Endpoints

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| `POST` | `/api/v1/workflows/{id}/activate` | Register trigger | 200 `WorkflowResponse` |
| `POST` | `/api/v1/workflows/{id}/deactivate` | Remove trigger | 200 `WorkflowResponse` |

### ActivateRequest body

```python
class ActivateRequest(BaseModel):
    trigger_type: Literal["cron", "interval"]
    cron: str | None = None          # "0 9 * * MON-FRI"
    interval_seconds: int | None = None  # 300
```

**Error codes**:

| Condition | HTTP |
|-----------|------|
| Workflow missing / no ownership | 404 |
| Inactive workflow | 409 |
| Re-activating an already-active workflow | 200 (idempotent, job replaced) |
| `trigger_type=cron` but the `cron` field is missing | 422 |
| Invalid cron expression | 422 |

## 5. Service logic

### activate_workflow(user, workflow_id, trigger)
1. Verify ownership + is_active (existing pattern)
2. Store `workflow.settings["trigger"] = trigger.model_dump()`
3. `add_job` to the APScheduler jobstore (job_id = `str(workflow_id)`, replace_existing=True)
   - trigger_type=cron → `CronTrigger.from_crontab(trigger.cron)`
   - trigger_type=interval → `IntervalTrigger(seconds=trigger.interval_seconds)`
   - func = `_execute_scheduled` (workflow_id, owner_id bound as arguments)
4. return workflow

### deactivate_workflow(user, workflow_id)
1. Verify ownership + is_active
2. `remove_job(str(workflow_id))` from the jobstore — no-op if absent (idempotent)
3. Save `workflow.settings.pop("trigger", None)`
4. return workflow

### _execute_scheduled(workflow_id, owner_id) — called by the Scheduler worker
1. `user = await user_repo.get(owner_id)`
2. `await workflow_service.execute_workflow(user, workflow_id)`
3. On failure log only (don't remove the job — retry on the next schedule)

## 6. Scheduler worker (`app/scheduler.py`)

```python
"""Scheduler worker — run as: python -m app.scheduler"""
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

async def main():
    engine = build_engine()
    sm = build_sessionmaker(engine)
    # Assemble the service layer (same pattern as the API lifespan)
    user_repo = PostgresUserRepository(sm)
    workflow_repo = PostgresWorkflowRepository(sm)
    execution_repo = PostgresExecutionRepository(sm)
    svc = WorkflowService(repo=workflow_repo, execution_repo=execution_repo, settings=Settings())

    jobstore = SQLAlchemyJobStore(url=Settings().scheduler_jobstore_url)
    scheduler = AsyncIOScheduler(jobstores={"default": jobstore})
    scheduler.start()

    # Wait forever — Ctrl+C to terminate
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
```

**Function-sprawl prevention**: assemble + start + wait + terminate inside
the single `main()`. No `_setup_repos` / `_configure_scheduler` helpers.

## 7. API-side JobStore access

To `add_job` / `remove_job` from the API process you need to **access the
JobStore without owning a Scheduler instance**. Two approaches:

**Approach 1**: In the API lifespan, also create an `AsyncIOScheduler` but
don't `start()` it
- `scheduler.add_job(...)` → INSERT into the jobstore
- The Scheduler worker polls and fires
- Pros: use the APScheduler API as-is
- Cons: a Scheduler instance per API process (but lightweight without start)

**Approach 2**: `add_job` directly on `SQLAlchemyJobStore`
- Depends on APScheduler internals; can break across versions

→ **Adopt Approach 1**. Store the un-started Scheduler on
`app.state.scheduler`; services CRUD jobs through it.

## 8. Function-sprawl-prevention guardrails

- `app/scheduler.py` is **a single file with a single `main()` function** — no separate modules / classes
- Add 2 methods to `WorkflowService` (`activate_workflow`, `deactivate_workflow`)
- No new `SchedulerManager` / `TriggerService` classes
- cron parsing is one line via `CronTrigger.from_crontab()` — no wrapper
- Errors reuse existing `DomainError` subclasses (`NotFoundError`, `WorkflowNotActiveError`, `InvalidGraphError` for cron-validation failure)

## 9. Tests

1. `test_activate_cron_happy` — verifies the trigger is stored in settings after activate
2. `test_activate_interval_happy`
3. `test_activate_not_owned_404`
4. `test_activate_inactive_409`
5. `test_activate_invalid_cron_422`
6. `test_deactivate_happy` — verifies the trigger is removed
7. `test_deactivate_already_inactive_is_idempotent`
8. `test_activate_replaces_existing_trigger` — re-activation replaces the job

**Scheduler-worker integration tests are Phase 2** — for now verify only
API-side job registration / removal + settings reflection. The E2E where
the worker actually fires `execute_workflow` lands after Execution_Engine
integration.

## 10. Acceptance criteria

- [ ] The 8 new tests pass
- [ ] No regression in the existing 42 tests (total 50+)
- [ ] Verified that `POST /activate` creates a jobstore row
- [ ] Verified that `POST /deactivate` removes the jobstore row
- [ ] `python -m app.scheduler` can boot the worker (manual check)
- [ ] After worker boot, a cron job actually fires and creates an execution row (manual check)
- [ ] 0 single-use private helpers in `WorkflowService`
- [ ] `app/scheduler.py` is a single file ≤ 50 lines

## 11. Downstream impact

- **PLAN_05 (Webhook)** — Webhook triggers can reuse the activate pattern
  (when `trigger_type: "webhook"` is added, activate registers with WebhookRegistry)
- **Execution_Engine** — E2E is complete once the Scheduler worker's
  `execute_workflow` call wires through to a real `Celery task.delay()`
- **Frontend** — add a "Schedule" tab to the workflow settings panel;
  cron/interval input → `POST /activate`

## 12. Dependencies

- `apscheduler>=3.10` — add to pyproject.toml
- `SQLAlchemyJobStore` uses the sync SQLAlchemy engine (strip `+asyncpg` from `database_url`)
- The `apscheduler_jobs` table is auto-created by APScheduler (no DDL needed)

## 13. Work order

1. Write the PLAN_04 document (this document) ✓
2. Add the `apscheduler` dependency to `pyproject.toml`
3. Add `scheduler_jobstore_url` to `app/config.py`
4. Add `ActivateRequest` to `app/models/workflow.py`
5. Inject the scheduler into `app/services/workflow_service.py` + add activate/deactivate
6. Add endpoints to `app/routers/workflows.py`
7. Inject an un-started Scheduler instance in `app/main.py` lifespan
8. New `app/scheduler.py` — standalone worker entrypoint
9. Write `tests/test_scheduler.py`
10. Verify tests pass locally
11. Manual worker boot + cron firing check
12. Open PR → review → merge
