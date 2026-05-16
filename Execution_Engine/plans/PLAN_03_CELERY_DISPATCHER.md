# PLAN_03 — Celery Dispatcher (Serverless Mode)

> Status: DRAFT
> Branch: `Execution_Engine`
> Predecessors: PLAN_01 (NodeRegistry), PLAN_02 (DAG executor)

## Purpose

Run `execution_mode=serverless` workflows asynchronously via a Celery +
Redis queue. The API_Server creates a `queued` Execution and enqueues a
Celery task → an Execution_Engine worker invokes `run_workflow()`.

## Architecture

```
API_Server                          Execution_Engine
───────────                         ─────────────────
workflow_service                    Celery Worker (scripts/worker.py)
  .execute_workflow()                   │
       │                                │
       ├─ create Execution(queued)      │
       │                                │
       └─ celery.send_task(             │
            "execute_workflow",     ──►  run_workflow_task(execution_id)
            args=[execution_id]          │
          )                              ├─ load workflow graph (DB)
                                         ├─ run_workflow(graph, execution, repo, registry)
                                         └─ (status → success/failed set by executor)
```

## File changes

### New
| File | Role |
|------|------|
| `src/dispatcher/__init__.py` | Empty package |
| `src/dispatcher/serverless.py` | Celery app + `run_workflow_task` |
| `scripts/worker.py` | `celery -A` worker entrypoint |
| `config/celery_config.py` | broker/backend URLs, serializer settings |
| `tests/test_dispatcher.py` | Unit tests (Celery eager mode) |

### Modified
| File | Change |
|------|--------|
| `pyproject.toml` | Add `celery[redis]` dependency |

## Implementation details

### 1. config/celery_config.py
```python
import os

broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
task_serializer = "json"
accept_content = ["json"]
task_acks_late = True
worker_prefetch_multiplier = 1
```

### 2. src/dispatcher/serverless.py

Create the Celery app and define the task. The task is a sync function
that runs the async executor inside `asyncio.run()`.

```python
celery_app = Celery("execution_engine")
celery_app.config_from_object("config.celery_config")

@celery_app.task(name="execute_workflow", bind=True, max_retries=0)
def run_workflow_task(self, execution_id: str):
    asyncio.run(_run(execution_id))

async def _run(execution_id: str):
    # 1. Look up execution + workflow via a DB session
    # 2. Call run_workflow() with the nodes registered in the registry
    # 3. The executor owns status updates (success/failed)
```

### 3. scripts/worker.py
```python
from src.dispatcher.serverless import celery_app
celery_app.worker_main(["worker", "--loglevel=info", "--concurrency=4"])
```

### 4. API_Server integration (handled on the API_Server branch)
In place of the TODO in `workflow_service.py`:
```python
if execution.execution_mode == "serverless":
    from celery import Celery
    broker = Celery(broker=settings.celery_broker_url)
    broker.send_task("execute_workflow", args=[str(execution.id)])
```
→ `send_task` only emits a broker message — no task definition required.
This change is a **separate PR** (API_Server branch).

## Test strategy

Run synchronously with Celery eager mode (`task_always_eager=True`):
1. `test_task_runs_workflow_to_success` — happy graph → status=success
2. `test_task_handles_missing_execution` — unknown execution_id → log error, do not propagate
3. `test_task_handles_node_failure` — failing node → status=failed

DB dependency: use InMemoryRepository (no real DB required).

## Dependency addition

```toml
dependencies = [
    "httpx>=0.27",
    "celery[redis]>=5.3",
    "auto-workflow-database",
]
```

## Checklist

- [ ] Write `config/celery_config.py`
- [ ] `src/dispatcher/serverless.py` — Celery app + task
- [ ] `scripts/worker.py` — worker entrypoint
- [ ] `pyproject.toml` — add celery[redis]
- [ ] Write 3 tests + pass
- [ ] Commit → push → PR

## Follow-ups

- API_Server branch: wire up `send_task` (remove TODO)
- PLAN_04: Agent daemon (execution_mode=agent)
