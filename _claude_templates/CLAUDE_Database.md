# Database — Claude Code branch guide

> Applied alongside the root `CLAUDE.md` security rules.

## Related documents

- Full architecture: [`docs/context/architecture.md`](../docs/context/architecture.md)
- Credential encryption rationale (ADR-004): [`docs/context/decisions.md`](../docs/context/decisions.md)
- Repository pattern rationale (ADR-006): [`docs/context/decisions.md`](../docs/context/decisions.md)
- File map: [`docs/context/MAP.md`](../docs/context/MAP.md)
- Upstream dependencies (Repository consumers): [`CLAUDE_API_Server.md`](./CLAUDE_API_Server.md), [`CLAUDE_Execution_Engine.md`](./CLAUDE_Execution_Engine.md)

## Module role

**Data Layer** — the persistence layer of the workflow automation engine.
Owns PostgreSQL schema design, Repository implementations, and the encrypted credential store.

`API_Server` and `Execution_Engine` access the DB only through this branch's Repository
interfaces (no direct SQL).

## File layout rules (MANDATORY)

```
Database/
├── schemas/      ← DDL (CREATE TABLE/INDEX) SQL
├── migrations/   ← schema-change history (YYYYMMDD_description.sql)
├── src/          ← Repository implementations (import-only)
│   ├── repositories/
│   │   ├── workflow_repository.py   ← PostgresWorkflowRepository
│   │   ├── execution_repository.py  ← PostgresExecutionRepository
│   │   └── credential_store.py      ← AES-256 encrypted store
│   └── models/   ← SQLAlchemy ORM models
├── scripts/      ← migrate.py, seed.py, validate.py (directly executed)
├── tests/        ← pytest (real DB connection, schema validation)
└── docs/         ← ERD, design docs
```

| File kind | Storage location |
|-----------|------------------|
| `CREATE TABLE`, `CREATE INDEX` | `schemas/` |
| `ALTER TABLE`, column changes | `migrations/YYYYMMDD_*.sql` |
| Repository implementations (import-only) | `src/repositories/` |
| SQLAlchemy ORM models | `src/models/` |
| Migration runner scripts | `scripts/` |
| pytest | `tests/` |

**Do not create files directly under `Database/` or the project root.**

## Tech stack

```python
import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
import asyncpg
from cryptography.fernet import Fernet   # credential encryption
```

- PostgreSQL 16+
- Async driver: `asyncpg` (compatible with FastAPI async)
- ORM: SQLAlchemy 2.0 async

## Core tables

| Table | Description |
|-------|-------------|
| `workflows` | Workflow definitions (nodes/connections stored as JSONB) |
| `executions` | Execution history (status, started_at, finished_at, node_results JSONB) |
| `credentials` | Encrypted credentials (owner_id, name, encrypted_data) |
| `users` | Account information |
| `agents` | Registered Agent metadata (owner_id, public_key, last_heartbeat) |
| `webhook_registry` | Dynamic Webhook path ↔ workflow_id mapping |

## Core indexes

```sql
CREATE INDEX idx_executions_workflow_id ON executions(workflow_id, started_at DESC);
CREATE INDEX idx_workflows_owner ON workflows(owner_id) WHERE is_active = true;
CREATE INDEX idx_webhook_path ON webhook_registry(path);
```

## Repository pattern

`API_Server` depends only on ABC interfaces (`WorkflowRepository`, `ExecutionRepository`,
`CredentialStore`). This branch provides the implementations.
Keep the structure substitutable with `InMemoryWorkflowRepository` for tests.

## Credential encryption rules

- At rest: AES-256 (Fernet) symmetric encryption, key in env var `CREDENTIAL_MASTER_KEY`
- For Agent-mode transmission: **re-encrypt** with the Agent's public key (RSA) before delivery
- Plaintext credentials must **never** appear in logs / DB / responses

## Migration file naming

```
migrations/
├── 20260414_initial_schema.sql
├── 20260420_add_agents_table.sql
└── 20260425_add_webhook_registry.sql
```

## Interfaces

- **Downstream**: `API_Server`, `Execution_Engine` — supplies Repository / CredentialStore implementations
- When schemas change, add history SQL to `migrations/` and notify downstream branches
