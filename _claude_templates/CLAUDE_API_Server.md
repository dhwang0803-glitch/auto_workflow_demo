# API_Server — Claude Code branch guide

> Applied alongside the root `CLAUDE.md` security rules.

## Related documents

- Full architecture / 4-layer flow: [`docs/context/architecture.md`](../docs/context/architecture.md)
- Decision rationale (choosing FastAPI, Celery, etc.): [`docs/context/decisions.md`](../docs/context/decisions.md)
- File / directory map: [`docs/context/MAP.md`](../docs/context/MAP.md)
- Downstream dependencies: [`CLAUDE_Database.md`](./CLAUDE_Database.md), [`CLAUDE_Execution_Engine.md`](./CLAUDE_Execution_Engine.md)
- Upstream dependencies: [`CLAUDE_Frontend.md`](./CLAUDE_Frontend.md)

## Module role

**FastAPI Core Server** — the brain of the workflow automation engine.
Receives workflow JSON from the Frontend, orchestrates CRUD, DAG scheduling,
trigger monitoring, and dispatch of executions to the Execution_Engine and Agents.

Owns the **Core Layer** of the 4-layer architecture, orchestrating `Database` (storage)
and `Execution_Engine` (runtime).

## File layout rules (MANDATORY)

```
API_Server/
├── app/
│   ├── routers/    ← per-endpoint routers (not directly executed)
│   │   ├── workflows.py    ← CRUD, execution trigger
│   │   ├── executions.py   ← execution-history lookup
│   │   ├── agents.py       ← Agent registration / WebSocket
│   │   └── webhooks.py     ← dynamic Webhook trigger receiver
│   ├── services/   ← business logic (not directly executed)
│   │   ├── workflow_service.py   ← WorkflowService (orchestrator)
│   │   ├── dag_scheduler.py      ← DAGScheduler (Kahn topological sort)
│   │   ├── trigger_manager.py    ← Webhook/Cron/Polling monitoring
│   │   └── agent_manager.py      ← WebSocket Agent connection management
│   ├── models/     ← Pydantic requests/responses + WorkflowSchema
│   └── main.py     ← FastAPI app entry point (DI assembly)
├── tests/          ← pytest (httpx TestClient)
└── config/         ← per-environment yaml (includes .env.example)
```

| File kind | Storage location |
|-----------|------------------|
| REST routers | `app/routers/` |
| Core business logic | `app/services/` |
| Pydantic schemas (`WorkflowSchema`, `NodeConfig`, etc.) | `app/models/` |
| FastAPI app + `create_app()` DI assembly | `app/main.py` |
| pytest | `tests/` |

**Do not create `.py` files directly under `API_Server/` or the project root.**

## Tech stack

```python
from fastapi import FastAPI, Depends, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlalchemy                 # shared with the Database branch
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # Cron triggers
from jose import jwt              # Agent JWT auth
import uvicorn
```

## Core endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/workflows` | Create a workflow (cycle check) |
| GET | `/api/v1/workflows/{id}` | Fetch a workflow |
| POST | `/api/v1/workflows/{id}/activate` | Register triggers (activate) |
| POST | `/api/v1/workflows/{id}/execute` | Manual execution |
| GET | `/api/v1/executions/{id}` | Fetch execution history |
| POST | `/api/v1/agents/register` | Register an Agent (agent_key → JWT) |
| WS | `/api/v1/agents/ws` | Persistent Agent connection (command push, heartbeat) |
| POST | `/webhooks/{workflow_id}/{path}` | Dynamic Webhook trigger |

## Execution-mode dispatch

`WorkflowService.execute_workflow()` branches on `workflow.settings.execution_mode`:

- `"serverless"` → queue a task to the `Execution_Engine`'s Celery Worker (Light/Middle users)
- `"agent"` → send to the customer's VPC Agent over WebSocket via `AgentManager` (Heavy users)

## Run

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Interfaces

- **Upstream**: Frontend (receives workflow JSON), Agent (receives heartbeat / results), external Webhooks
- **Downstream**:
  - `Database` — workflow / execution history / credential store
  - `Execution_Engine` — delegate serverless execution via Celery queue
  - Agent — send AgentCommand over WebSocket

## Security notes

- Credentials are **never** passed to router/service code as plaintext.
  `CredentialStore.retrieve()` is called only at execution time.
- When sent to an Agent, **encrypt with the public key** beforehand (only the Agent can decrypt).
- Webhook endpoints require HMAC signature verification.
