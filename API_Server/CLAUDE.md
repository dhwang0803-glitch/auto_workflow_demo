# API_Server — Claude Code branch guide

> Applied alongside the security rules in the root `CLAUDE.md`.

## Module role

**FastAPI Core Server** — the brain of the workflow-automation engine.
Accepts workflow JSON from the Frontend, handles CRUD, DAG scheduling,
trigger watching, and dispatches execution to `Execution_Engine` or the
customer-side Agent.

This is the **Core Layer** in the 4-layer architecture; it orchestrates
`Database` (storage) and `Execution_Engine` (runner).

## File-location rules (MANDATORY)

```
API_Server/
├── app/
│   ├── routers/    ← per-endpoint routers (no direct execution)
│   │   ├── workflows.py    ← CRUD, run trigger
│   │   ├── executions.py   ← execution-history queries
│   │   ├── agents.py       ← agent registration / WebSocket
│   │   └── webhooks.py     ← dynamic webhook trigger intake
│   ├── services/   ← business logic (no direct execution)
│   │   ├── workflow_service.py   ← WorkflowService (orchestrator)
│   │   ├── dag_scheduler.py      ← DAGScheduler (Kahn topological sort)
│   │   ├── trigger_manager.py    ← Webhook / Cron / Polling watchers
│   │   └── agent_manager.py      ← WebSocket Agent connection manager
│   ├── models/     ← Pydantic request/response + WorkflowSchema
│   └── main.py     ← FastAPI app entrypoint (DI wiring)
├── tests/          ← pytest (httpx TestClient)
└── config/         ← per-environment YAML (.env.example included)
```

| File kind | Location |
|-----------|----------|
| REST routers | `app/routers/` |
| Core business logic | `app/services/` |
| Pydantic schemas (`WorkflowSchema`, `NodeConfig`, …) | `app/models/` |
| FastAPI app + `create_app()` DI wiring | `app/main.py` |
| pytest | `tests/` |

**Do not create `.py` files directly at the `API_Server/` root or the
project root.**

## Tech stack

```python
from fastapi import FastAPI, Depends, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlalchemy                 # shared with the Database branch
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # cron triggers
from jose import jwt              # Agent JWT auth
import uvicorn
```

## Core endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/workflows` | Create workflow (cycle check) |
| GET | `/api/v1/workflows/{id}` | Read workflow |
| POST | `/api/v1/workflows/{id}/activate` | Register triggers (activate) |
| POST | `/api/v1/workflows/{id}/execute` | Manual run |
| GET | `/api/v1/executions/{id}` | Read execution history |
| POST | `/api/v1/agents/register` | Register agent (agent_key → JWT) |
| WS | `/api/v1/agents/ws` | Long-lived agent connection (command push, heartbeat) |
| POST | `/webhooks/{workflow_id}/{path}` | Dynamic webhook trigger |

## Execution-mode dispatch

`WorkflowService.execute_workflow()` branches on
`workflow.settings.execution_mode`:

- `"serverless"` → enqueue a task on `Execution_Engine`'s Celery worker
  (Light / Middle users)
- `"agent"` → send over WebSocket via `AgentManager` to the agent in the
  customer's VPC (Heavy users)

## Running locally

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Interfaces

- **Upstream**: Frontend (workflow JSON), Agent (heartbeat / results),
  external webhooks
- **Downstream**:
  - `Database` — workflow / execution-history / credential storage
  - `Execution_Engine` — serverless execution via the Celery queue
  - Agent — `AgentCommand` over WebSocket

## Security notes

- **Never** pass plaintext credentials through router/service code.
  `CredentialStore.retrieve()` is invoked only at execution time.
- When sending to an agent, **encrypt with the agent's public key** so only
  the agent can decrypt.
- Webhook endpoints **must** verify the HMAC signature.
