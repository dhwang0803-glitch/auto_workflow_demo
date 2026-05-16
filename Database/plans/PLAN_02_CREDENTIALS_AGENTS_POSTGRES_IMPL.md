# PLAN_02 — Credentials + Agents + Webhooks + Postgres Repository implementations

> **Branch**: `Database` · **Drafted**: 2026-04-15 · **Completed**: 2026-04-15 · **Status**: Done
>
> PLAN_01 nailed down the 4 core tables + Repository ABCs + InMemory doubles.
> PLAN_02 fills in (1) the remaining 3 tables, (2) the real Postgres
> Repository implementations, and (3) Fernet credential encryption — so
> `API_Server` can run plan routing and Webhook receipt against a real DB
> rather than a test double.

## 1. Goals

1. Add the `credentials` / `agents` / `webhook_registry` DDL (ADR-004, ADR-009)
2. Implement `PostgresWorkflowRepository`, `PostgresExecutionRepository`, `FernetCredentialStore`
3. By design `users.gpu_info` moves to `agents` — collected at Agent boot and stored as `agents.gpu_info` JSONB (ADR-009)
4. Define the `NodeRegistry → nodes` upsert path (run at Execution_Engine startup)
5. Webhook dynamic-path resolution: add a `webhook_registry.path → workflow_id` lookup interface

## 2. Scope

**In**
- DDL: `credentials`, `agents`, `webhook_registry`
- `agents.gpu_info jsonb` — basis for ADR-009 hardware routing
- `FernetCredentialStore` — `CREDENTIAL_MASTER_KEY` env var, AES-256 (Fernet)
- 3 Postgres implementations (`asyncpg` + SQLAlchemy 2.0 async session)
- 1 `WebhookRegistry` Repository ABC + InMemory / Postgres implementations
- `NodeCatalogRepository` ABC + upsert_many path
- Integration tests (`DATABASE_URL` required): happy path for each Repository

**Out (follow-up)**
- Agent public-key management + credential re-encryption (RSA) → PLAN_03 or separate
- Approval notification dispatch history → PLAN_03
- Agent heartbeat → Agent-side PLAN

## 3. Table design

### 3.1 `credentials` — ADR-004 Fernet

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid PK DEFAULT gen_random_uuid()` | |
| `owner_id` | `uuid REFERENCES users(id) ON DELETE CASCADE` | |
| `name` | `text NOT NULL` | User-assigned name (e.g., `"slack-bot-token"`) |
| `encrypted_data` | `bytea NOT NULL` | Fernet ciphertext. **Never store plaintext** |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |

`UNIQUE (owner_id, name)`.

> Plaintext credentials exist only as the return value of
> `FernetCredentialStore.retrieve()` — they must not be included in logs
> or response bodies. The re-encryption-with-Agent-public-key path for
> Agent-mode transport is PLAN_03's scope.

### 3.2 `agents` — ADR-009 hardware routing

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid PK DEFAULT gen_random_uuid()` | |
| `owner_id` | `uuid REFERENCES users(id) ON DELETE CASCADE` | |
| `public_key` | `text NOT NULL` | RSA PEM. Used for credential re-encryption |
| `gpu_info` | `jsonb NOT NULL DEFAULT '{}'::jsonb` | Collected once at Agent boot — ADR-009 |
| `last_heartbeat` | `timestamptz NULL` | |
| `registered_at` | `timestamptz NOT NULL DEFAULT now()` | |

Index: `CREATE INDEX idx_agents_owner ON agents(owner_id);`

> **`gpu_info` schema must be agreed upon** — at minimum the 3 keys
> `{"vendor": "nvidia"|"amd"|"cpu_only", "vram_gb": number, "backend":
> "vllm"|"ktransformers"|null}` are fixed. ADR-009's
> KTransformers CPU-only routing decision uses `backend=="ktransformers"` directly.

### 3.3 `webhook_registry` — dynamic webhook routing

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid PK` | |
| `workflow_id` | `uuid REFERENCES workflows(id) ON DELETE CASCADE` | |
| `path` | `text UNIQUE NOT NULL` | `/webhooks/<uuid>` shape |
| `secret` | `text NULL` | For HMAC verification |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |

Index: `CREATE INDEX idx_webhook_path ON webhook_registry(path);`

## 4. Repository implementations

### 4.1 Additional ABCs

```python
class WebhookRegistry(ABC):
    @abstractmethod
    async def register(self, workflow_id: UUID, *, secret: str | None = None) -> str: ...
    @abstractmethod
    async def resolve(self, path: str) -> UUID | None: ...
    @abstractmethod
    async def unregister(self, path: str) -> None: ...

class NodeCatalogRepository(ABC):
    @abstractmethod
    async def upsert_many(self, nodes: list[NodeDefinition]) -> None: ...
    @abstractmethod
    async def list_all(self) -> list[NodeDefinition]: ...
```

### 4.2 Postgres implementations

| File | Class |
|------|-------|
| `src/repositories/workflow_repository.py` | `PostgresWorkflowRepository` |
| `src/repositories/execution_repository.py` | `PostgresExecutionRepository` |
| `src/repositories/credential_store.py` | `FernetCredentialStore` |
| `src/repositories/webhook_registry.py` | `PostgresWebhookRegistry` + `InMemoryWebhookRegistry` |
| `src/repositories/node_catalog.py` | `PostgresNodeCatalog` + `InMemoryNodeCatalog` |

Shared pattern:
- Constructor arg: `sessionmaker: async_sessionmaker[AsyncSession]` — the engine is injected by `API_Server`
- All methods async; transactions auto-commit internally (`async with session.begin():`)
- ORM-object ↔ `base.py` dataclass DTO conversion factored into private helpers

### 4.3 Fernet key loading

```python
# src/repositories/credential_store.py
class FernetCredentialStore(CredentialStore):
    def __init__(self, sessionmaker, *, master_key: bytes):
        self._f = Fernet(master_key)
        self._sm = sessionmaker
```

`master_key` is loaded from `os.environ["CREDENTIAL_MASTER_KEY"]` at
`API_Server` boot. Tests use ephemeral keys from `Fernet.generate_key()`.

## 5. Deliverables

| Path | Content |
|------|---------|
| `schemas/002_credentials_agents_webhooks.sql` | DDL for the 3 tables above |
| `migrations/20260420_credentials_agents_webhooks.sql` | Migration including 002 |
| `src/models/extras.py` | SQLAlchemy ORM (`Credential`, `Agent`, `WebhookRegistry`, `NodeDefinition`) |
| `src/repositories/{workflow,execution,credential_store,webhook_registry,node_catalog}.py` | Postgres implementations |
| `src/repositories/base.py` updates | Adds `WebhookRegistry`, `NodeCatalogRepository` ABCs |
| `tests/test_postgres_repositories.py` | Integration tests requiring `DATABASE_URL` |
| `tests/test_credential_store.py` | Fernet round-trip + missing-key failure case |

## 6. Acceptance criteria

- [x] `python scripts/migrate.py` applies the 002 migration cleanly *(2026-04-15)*
- [x] PLAN_01 state-machine scenarios pass against a real DB via `PostgresExecutionRepository` *(test_postgres_repositories)*
- [x] `FernetCredentialStore.store → retrieve` round-trip yields the same plaintext *(test_credential_store)*
- [x] Loading with the wrong key raises `InvalidToken` *(test_wrong_key_rejects_ciphertext)*
- [x] `PostgresWebhookRegistry.resolve` reads via the index *(unique index on `webhook_registry.path`)*
- [x] `PostgresNodeCatalog.upsert_many` is idempotent on `(type, version)` *(test_node_catalog_upsert_idempotent)*

## 7. Open issues

1. ~~**`agents.gpu_info` JSONB key spec**~~ → **MVP locked in (2026-04-15)**
   The 3 keys `{vendor, vram_gb, backend}` are documented in a comment on
   the 002 DDL. The Agent-side PLAN can extend the schema forward-compatibly
   (undefined keys are allowed on storage).
2. **Fernet key rotation** — MVP uses a single key. The transition path to
   MultiFernet is post-PLAN_03. Today, swapping `CREDENTIAL_MASTER_KEY`
   breaks decryption of existing credentials — call this out in deployment notes.
3. **Records without `webhook_registry.secret`** — whether HMAC verification
   is mandatory is decided in `API_Server`'s Webhook-receipt PLAN. For now,
   NULL is allowed.
4. **When `NodeRegistry ↔ nodes` syncs** — agreed: once at `Execution_Engine`
   startup. Hot-swapping node plugins at runtime is not supported.

## 8. Implementation notes (2026-04-15)

- **`test_schema_loads` is destructive**: this test runs `DROP SCHEMA public CASCADE`
  and reapplies every `schemas/*.sql`. When you add a new DDL file you must
  also update this test's `expected` table set — otherwise subsequent
  integration tests break with "table not found" (we actually hit this once
  during PLAN_02 implementation).
- **In-place JSONB mutation**: `PostgresExecutionRepository.append_node_result`
  marks changes with `flag_modified()`. Without it, SQLAlchemy never issues
  an UPDATE and the change silently disappears.

## 9. Follow-up PLAN preview

The original "PLAN_03 integration scope" is being split by concern into 3
smaller PLANs (2026-04-15):

- **PLAN_03** — Execution observability detail: per-node log storage (`execution_node_logs`)
- **PLAN_04** — Approval notification dispatch history: to whom / when / which channel
- **PLAN_05** — Agent-public-key-based credential re-encryption transport (ADR-004 Agent path)
- **PLAN_06** — RAG: embedding columns for user workflows / templates (pgvector is already installed; only the migration is needed)
