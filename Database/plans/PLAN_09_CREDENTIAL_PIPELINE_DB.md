# PLAN_09 — Credential pipeline (Database portion)

> Blueprint: [`docs/context/PLAN_credential_pipeline.md`](../../docs/context/PLAN_credential_pipeline.md)
> Predecessor ADRs: ADR-004 (Fernet storage), ADR-013 (Agent transport), ADR-016 (pipeline split)
> Follow-up: `API_Server/plans/PLAN_07_CREDENTIAL_PIPELINE.md`

## Goals

Database-layer implementation of the BYO + per-execution credential pipeline:

1. `credentials.type` column + CHECK constraint to lock in the credential_type catalog
2. `CredentialStore.bulk_retrieve(ids, owner_id)` — the single-shot decryption path on execution trigger
3. `CredentialStore.store()` now persists `credential_type` alongside the row

## File changes

### New
| File | Role |
|------|------|
| `migrations/20260601_credentials_type_column.sql` | Adds the type column to existing DBs |

### Modified
| File | Change |
|------|--------|
| `schemas/002_credentials_agents_webhooks.sql` | Inline-add the `type` column on CREATE TABLE (for fresh installs) |
| `auto_workflow_database/models/extras.py` | Add the `Credential.type` ORM field |
| `auto_workflow_database/repositories/base.py` | Add `credential_type` kwarg to `CredentialStore.store`, plus the `bulk_retrieve` ABC |
| `auto_workflow_database/repositories/credential_store.py` | Implement both methods |
| `tests/fakes.py` | Mirror the changes on `InMemoryCredentialStore` |
| `tests/test_credential_store.py` | Add Postgres integration tests (type / bulk_retrieve) |

### New (tests)
| File | Role |
|------|------|
| `tests/test_credential_bulk_fake.py` | Contract tests for `InMemoryCredentialStore.bulk_retrieve` (no DB required) |

## Implementation details

### 1. Migration (`20260601_credentials_type_column.sql`)

```sql
ALTER TABLE credentials
    ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT 'unknown'
    CHECK (type IN ('smtp', 'postgres_dsn', 'slack_webhook', 'http_bearer', 'unknown'));
```

- Postgres allows inline `CHECK` on `ADD COLUMN` → column + constraint added atomically.
- `IF NOT EXISTS` keeps fresh installs (via the schemas/002 path) conflict-free.

### 2. `schemas/002_credentials_agents_webhooks.sql` update

Inline-add the `type` column to the credentials CREATE TABLE so that on a
fresh install the migration becomes a no-op.

### 3. `CredentialStore.store` signature extension

```python
async def store(
    self,
    owner_id: UUID,
    name: str,
    plaintext: dict,
    *,
    credential_type: str = "unknown",
) -> UUID: ...
```

- **Positional argument order is preserved** — existing `store(owner_id, name, plaintext)` callers don't break
- `credential_type` is a kwarg defaulting to `"unknown"` — legacy / migration compatible

### 4. `CredentialStore.bulk_retrieve` ABC + implementation

```python
async def bulk_retrieve(
    self,
    credential_ids: list[UUID],
    *,
    owner_id: UUID,
) -> dict[UUID, dict]:
    """Apply the ownership filter and return plaintext dicts keyed by credential_id.
    If any requested id is missing from the result, raise KeyError — no partial success.
    An empty credential_ids list returns an empty dict.
    """
```

**Postgres implementation** (`credential_store.py`):
- Single fetch: `SELECT id, encrypted_data FROM credentials WHERE owner_id = :owner AND id = ANY(:ids)`
- If the result row count is less than the requested id count → `KeyError(f"missing credential(s): {diff}")`
- Fernet-decrypt each row's `encrypted_data` and `json.loads` it

**InMemory implementation** (`fakes.py`):
- Same semantics: ownership filter + partial-fail-raises + empty-list-allowed

### 5. Security invariants

- The return value of `bulk_retrieve` only lives in the caller's scope — never cache or log it (called out in the docstring)
- On ownership mismatch, do *not* leak which id wasn't yours (enumeration-attack defense) — the catch-all message `"missing credential(s)"` keeps it uniform

## Test strategy

### Postgres integration (`tests/test_credential_store.py`, skip without DATABASE_URL)
1. `test_store_with_type` — `store(..., credential_type="smtp")`, then verify via direct SELECT that `type='smtp'`
2. `test_store_default_type_is_unknown` — omit the kwarg → `type='unknown'`
3. `test_store_rejects_invalid_type` — `credential_type="bogus"` → IntegrityError (CHECK violation)
4. `test_bulk_retrieve_happy` — store 3, bulk_retrieve → 3 plaintexts match
5. `test_bulk_retrieve_ownership_filter` — owner B asking for owner A's credentials → KeyError
6. `test_bulk_retrieve_missing_id_raises` — mix in a non-existent UUID → KeyError
7. `test_bulk_retrieve_empty_list` — empty list → empty dict

### InMemory fake (`tests/test_credential_bulk_fake.py`, no DB required)
1. `test_fake_store_preserves_type` — verify the credential_type kwarg is stored
2. `test_fake_bulk_retrieve_happy`
3. `test_fake_bulk_retrieve_ownership_filter`
4. `test_fake_bulk_retrieve_missing_raises`
5. `test_fake_bulk_retrieve_empty_list`

## Checklist

- [ ] Migration SQL + schemas/002 kept in sync
- [ ] `Credential` ORM gains the `type` column
- [ ] `CredentialStore` ABC + Fernet/InMemory implementations
- [ ] 7 Postgres integration tests (skip on no DB)
- [ ] 5 fake unit tests (always run)
- [ ] Backward compatibility (`store(owner_id, name, plaintext)` callers don't break)
- [ ] Commit → push → PR

## Out of scope

- Credential rotation / expiry (Phase 2)
- Per-credential-type schema validation enforcement (API_Server's responsibility)
- An audit table (TBD — blueprint §1.6 invariants are not log-format prescriptions)
