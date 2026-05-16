# Database — Claude Code branch guide

> Applied alongside the security rules in the root `CLAUDE.md`.

## Module role

**Data Layer** — the persistence layer of the workflow-automation engine.
Owns PostgreSQL schema design, repository implementations, and the encrypted
credential store.

`API_Server` and `Execution_Engine` reach the database **only** through the
Repository interfaces published by this branch (direct SQL is forbidden).

## File-location rules (MANDATORY)

**Since PLAN_00, `Database` ships as the `auto-workflow-database` Python
package.** Other branches (`API_Server`, `Execution_Engine`) install it
editable with `pip install -e Database/` and import via
`from auto_workflow_database.repositories.base import ...`.
Phase 2 will switch to publishing a wheel through GitHub Packages.

```
Database/
├── pyproject.toml                  ← package metadata + dependencies
├── schemas/                         ← DDL (CREATE TABLE/INDEX) SQL
├── migrations/                      ← schema-change history (YYYYMMDD_description.sql)
├── auto_workflow_database/          ← Python package root
│   ├── repositories/
│   │   ├── workflow_repository.py
│   │   ├── execution_repository.py
│   │   └── credential_store.py
│   ├── models/                      ← SQLAlchemy ORM
│   └── crypto/                      ← hybrid.py (ADR-013)
├── scripts/                         ← migrate.py, roll_partitions.py
├── tests/                           ← pytest
└── plans/                           ← PLAN documents
```

| File kind | Location | Import path |
|-----------|----------|-------------|
| `CREATE TABLE`, `CREATE INDEX` | `schemas/` | — |
| `ALTER TABLE`, column changes | `migrations/YYYYMMDD_*.sql` | — |
| Repository implementations | `auto_workflow_database/repositories/` | `auto_workflow_database.repositories.X` |
| SQLAlchemy ORM models | `auto_workflow_database/models/` | `auto_workflow_database.models.X` |
| Crypto helpers | `auto_workflow_database/crypto/` | `auto_workflow_database.crypto.X` |
| Migration scripts | `scripts/` | (run directly) |
| pytest | `tests/` | — |

**Do not create files directly at the `Database/` root or the project root.**

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
| `users` | Account info |
| `agents` | Registered agent metadata (owner_id, public_key, last_heartbeat) |
| `webhook_registry` | Dynamic webhook path ↔ workflow_id mapping |
| `skills` | PLAN_12 / ADR-022 Skill Bootstrap — codified team policies (condition + action) |
| `skill_sources` | Per-skill provenance (document / conversation / observation) — append-only |
| `skill_applications` | Audit of skill application during compose (workflow_id is not a hard FK) — append-only |
| `policy_documents` | Uploaded SOPs / handbooks. UNIQUE on (owner_user_id, content_hash) prevents duplicates |
| `policy_extractions` | Chunks + BGE-M3 embeddings (`vector(1024)`, HNSW index) |

## Core indexes

```sql
CREATE INDEX idx_executions_workflow_id ON executions(workflow_id, started_at DESC);
CREATE INDEX idx_workflows_owner ON workflows(owner_id) WHERE is_active = true;
CREATE INDEX idx_webhook_path ON webhook_registry(path);
```

## Repository pattern

`API_Server` depends only on the ABC interfaces (`WorkflowRepository`,
`ExecutionRepository`, `CredentialStore`); this branch provides their
implementations. Keep the shape swappable with `InMemoryWorkflowRepository`
for tests.

## Credential-encryption rules

- At rest: AES-256 (Fernet) symmetric encryption; the key lives in the
  `CREDENTIAL_MASTER_KEY` environment variable.
- For Agent mode: re-encrypt with the agent's RSA public key before sending.
- **Never** include plaintext credentials in logs, the database, or HTTP
  responses.

## Migration file naming

```
migrations/
├── 20260414_initial_schema.sql
├── 20260420_add_agents_table.sql
└── 20260425_add_webhook_registry.sql
```

## Interfaces

- **Downstream**: `API_Server`, `Execution_Engine` — receive Repository /
  CredentialStore implementations.
- When the schema changes, add a migration SQL file under `migrations/` and
  notify the downstream branches.
