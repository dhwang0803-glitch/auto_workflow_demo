# Developer Agent Instructions

## Role

Implements the minimum code that passes the tests written by the Test Writer Agent (TDD Green step).
Avoids over-design and adds no unnecessary features.

---

## Implementation principles

1. **Passing tests first**: implement only what is needed to pass the currently failing tests
2. **Minimum implementation**: write the simplest code that passes the tests
3. **Honor the PLAN**: do not stray from the file-location rules and interfaces in the branch's `CLAUDE.md`
4. **Cache external calls**: where possible, route external API calls through a cache layer (prevents duplicate requests)

---

## Implementation file locations

Always follow each branch's `CLAUDE.md` file-location rules.

| Branch | Runnable scripts | Import-only modules | Tests |
|--------|-----------|------|--------|
| `API_Server` | `app/main.py` | `app/routers/`, `app/services/`, `app/models/` | `tests/` |
| `Database` | `scripts/` | `src/repositories/`, `src/models/` | `tests/` |
| `Execution_Engine` | `scripts/worker.py`, `scripts/agent_run.py` | `src/nodes/`, `src/dispatcher/`, `src/runtime/`, `src/agent/` | `tests/` |
| `Frontend` | — | `src/components/`, `src/pages/`, `src/services/` | `tests/` |

**Do not create `.py` / `.ts` files directly at the project root or a branch root.**

---

## Environment variable loading

```python
from dotenv import load_dotenv
import os

load_dotenv('.env')  # .env at the project root

# Load real values without defaults (fail immediately if missing)
DB_URL = os.environ['DATABASE_URL']
REDIS_URL = os.environ['REDIS_URL']
```

**Strictly forbidden**: putting real infrastructure info into a default like `os.getenv("DB_HOST", "10.0.0.1")`.

---

## DB connection (Database-branch Repository implementations)

```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
import os

engine = create_async_engine(
    os.environ['DATABASE_URL'],
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
```

---

## 🗄️ DB-access code rules (MANDATORY — minimize network I/O)

> Every PostgreSQL query is one network round trip.
> Putting a DB query inside a loop hits the N+1 problem and makes the pipeline fatally slow.
> **Before writing the code, plan the number of DB round trips and state it in a comment.**

### ❌ Forbidden pattern — N+1 queries

```python
# strictly forbidden: fetch inside a loop
for workflow_id in workflow_ids:
    row = await session.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
```

### ✅ Correct pattern — batched read + batched INSERT

```python
# DB round-trip plan: 1 SELECT + a handful of batched INSERTs

# 1. fetch all targets at once
rows = await session.execute(
    select(Workflow).where(Workflow.id.in_(workflow_ids))
)
workflows = {w.id: w for w in rows.scalars()}

# 2. pure Python logic (no DB round trip)
results = [compute(workflows[wid]) for wid in workflow_ids]

# 3. batched INSERT (thousands of rows at a time)
await session.execute(insert(ExecutionRecord), results)
await session.commit()
```

### Design decision criteria

| Total DB round trips | Verdict | Action |
|--------------|------|------|
| ≤ ~50 | ✅ Healthy | Implement as is |
| 50–100 | ⚠️ Caution | Consider batch consolidation |
| > 100 | ❌ Redesign | Must remove the in-loop query |

---

## Async code rules (FastAPI + Celery)

1. Write FastAPI routers and services as **`async def`**.
2. Do not call blocking I/O libraries (`requests`, `psycopg2`) directly from async handlers.
   → Use `httpx.AsyncClient`, `asyncpg` / async SQLAlchemy.
3. Run CPU-bound work as a separate Celery task.

---

## Post-implementation self-check

- [ ] No hardcoded API keys, IPs, or passwords
- [ ] try-except + timeout on every external API call
- [ ] Backoff strategy where rate limits apply
- [ ] No DB queries inside loops (no N+1)
- [ ] All credentials go through `CredentialStore.retrieve()`
- [ ] User custom code always runs through the sandbox (CodeExecutionNode)
- [ ] Agent command handler includes `execution_id` idempotency check
