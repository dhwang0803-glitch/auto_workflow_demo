# PLAN_07 — Credential pipeline (API_Server portion)

> Blueprint: [`docs/context/PLAN_credential_pipeline.md`](../../docs/context/PLAN_credential_pipeline.md)
> Predecessor: Database PLAN_09 (merged PR #47) — `CredentialStore.bulk_retrieve` + `type` column ready
> Follow-up: Execution_Engine PLAN_08 — Worker actually resolves credential_refs and injects plaintext into node configs

## Purpose

Provides the BYO credential CRUD API + performs credential_ref **validation**
(ownership + existence) on workflow execution trigger. **Plaintext injection
is out of scope for this PR** — Worker/Agent owns it (per blueprint §1.6
security invariant: "plaintext does not pass through broker / DB").

## Scope adjustment (decided 2026-04-17)

Blueprint §2 ② describes "API_Server resolves credential_ref in
execute_workflow", but in practice only `execution_id` is passed in Celery
args and the Worker re-fetches the graph from the DB. Even if API_Server
injects plaintext, it never reaches the Worker. Worse, plaintext passing
through the Redis broker violates §1.6 security invariant #1.

**Adjusted responsibility split:**
- **API_Server (this PR)**: credential CRUD + execute_workflow **validation
  only**. Uses `bulk_retrieve(ids, owner_id)` for ownership + existence
  checks and discards the returned plaintext immediately. Does not perform
  plaintext injection.
- **Execution_Engine PLAN_08 (next PR)**: inject `CredentialStore` into
  `WorkerContainer`. `_execute()` resolves credential_refs just before node
  execution → passes plaintext config to the node.

This split formally promotes blueprint §2 ③ ("~10 LOC allowed") into a
proper PLAN. Blueprint updates land in a separate docs PR.

## File changes

### New
| File | Role |
|------|------|
| `app/models/credential.py` | Pydantic — `CredentialCreate`, `CredentialResponse`, `CredentialType` Literal |
| `app/services/credential_service.py` | `CredentialService` — create/delete + validation on execute |
| `app/routers/credentials.py` | `POST /api/v1/credentials`, `DELETE /api/v1/credentials/{id}` |
| `tests/test_credentials.py` | Router integration tests |
| `tests/test_credential_execute_validation.py` | execute_workflow credential_ref validation tests |

### Modified
| File | Change |
|------|--------|
| `app/config.py` | Add `credential_master_key: str` (Fernet base64) |
| `app/container.py` | Instantiate `FernetCredentialStore` + assemble `CredentialService` |
| `app/main.py` | Register `credentials_router` + expose `credential_service` on `app.state` |
| `app/services/workflow_service.py` | `execute_workflow` now takes a `CredentialStore` and runs credential_ref validation |
| `tests/conftest.py` | Add `credential_master_key` to the Settings fixture |
| `.env.example` | Document the `CREDENTIAL_MASTER_KEY` variable |

### Out of scope (explicit)
- `GET /api/v1/credentials` (list) + `GET /api/v1/credentials/{id}` (metadata) — needs a `list_by_owner()` method that the Database lacks; expand the `CredentialStore` ABC in a separate Database PR, then follow up.
- Worker-side credential_ref → config merge (owned by Execution_Engine PLAN_08)

## Implementation details

### 1. `CredentialCreate` / `CredentialResponse` (`app/models/credential.py`)

```python
CredentialType = Literal["smtp", "postgres_dsn", "slack_webhook", "http_bearer"]

class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: CredentialType
    plaintext: dict  # type-specific validation can extend on top of the §1.2 catalog

class CredentialResponse(BaseModel):
    id: UUID
    name: str
    type: str
```

- `plaintext` only exists in the request body; never in the response
- `type` does not include `"unknown"` — anything created via the public API must be in the catalog
- Per-type key validation (e.g., `smtp` requires host/port/user/password) is skipped in this PR — strict validation lands in Phase 2 (after the Frontend UX is decided)

### 2. `CredentialService` (`app/services/credential_service.py`)

```python
class CredentialService:
    def __init__(self, *, store: CredentialStore) -> None:
        self._store = store

    async def create(self, user: User, body: CredentialCreate) -> UUID:
        try:
            return await self._store.store(
                user.id, body.name, body.plaintext,
                credential_type=body.type,
            )
        except IntegrityError as e:
            # credentials_owner_name_uq conflict
            raise DuplicateNameError("credential name already used") from e

    async def delete(self, user: User, credential_id: UUID) -> None:
        # Use bulk_retrieve for ownership validation (checks both existence and ownership)
        # Throw away the returned plaintext immediately (function-local scope)
        try:
            await self._store.bulk_retrieve([credential_id], owner_id=user.id)
        except KeyError:
            raise NotFoundError("credential not found")
        await self._store.delete(credential_id)

    async def validate_refs(
        self, user: User, credential_ids: list[UUID]
    ) -> None:
        """For execute_workflow — credential_ref validation. Plaintext discarded immediately.
        Raise NotFoundError if any id is missing (enumeration defense)."""
        if not credential_ids:
            return
        try:
            await self._store.bulk_retrieve(credential_ids, owner_id=user.id)
        except KeyError:
            raise NotFoundError("credential not found")
```

### 3. Router (`app/routers/credentials.py`)

```python
@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(body, user, svc) -> CredentialResponse:
    cid = await svc.create(user, body)
    return CredentialResponse(id=cid, name=body.name, type=body.type)

@router.delete("/{credential_id}", status_code=204)
async def delete_credential(credential_id, user, svc) -> Response:
    await svc.delete(user, credential_id)
    return Response(status_code=204)
```

### 4. `execute_workflow` credential_ref validation

Insert at the start of `workflow_service.execute_workflow`:

```python
# Collect credential_ref ids from graph nodes
ids: list[UUID] = []
for node in wf.graph.get("nodes", []):
    ref = (node.get("config") or {}).get("credential_ref")
    if ref and "credential_id" in ref:
        ids.append(UUID(ref["credential_id"]))

if ids:
    await self._credential_service.validate_refs(user, ids)
    # Plaintext is NOT injected here. Worker (Execution_Engine PLAN_08)
    # will resolve credential_refs just before node invocation.
```

- On failure → `NotFoundError` → 404 (anti-enumeration; doesn't distinguish "owner ≠ user" from "id doesn't exist")
- The execution itself isn't created — validation precedes `create(execution)`

### 5. DuplicateNameError error class

Add to `app/errors.py`:

```python
class DuplicateNameError(DomainError):
    """409 — unique constraint on (owner_id, name)."""
    http_status = 409
```

## Test strategy

### test_credentials.py (router E2E, skipif DATABASE_URL)

1. `test_create_credential_returns_201_with_id` — POST succeeds, response has no plaintext
2. `test_create_credential_with_unknown_type_422` — Pydantic Literal validation
3. `test_create_duplicate_name_409` — UNIQUE (owner, name) conflict
4. `test_delete_credential_204` — DELETE succeeds
5. `test_delete_credential_not_owned_404` — another user's credential → NotFoundError
6. `test_delete_credential_nonexistent_404`

### test_credential_execute_validation.py (execute_workflow E2E)

1. `test_execute_with_valid_credential_ref_queued` — register credential first, then workflow with ref → 202
2. `test_execute_with_nonexistent_credential_ref_404`
3. `test_execute_with_other_users_credential_ref_404` — cross-tenant enumeration defense
4. `test_execute_with_no_credential_refs_works` — regression for the existing path

## Checklist

- [ ] Settings `credential_master_key`
- [ ] AppContainer `CredentialStore` + `CredentialService`
- [ ] Pydantic credential models
- [ ] CredentialService (create + delete + validate_refs)
- [ ] credentials router (POST + DELETE)
- [ ] workflow_service.execute_workflow validation insertion
- [ ] DuplicateNameError error class
- [ ] main.py router registration + app.state exposure
- [ ] 10 tests pass (existing 62 + 10 = 72)
- [ ] Commit → push → PR

## Out of scope

- `GET /credentials` (list) — requires Database `list_by_owner()` first
- `GET /credentials/{id}` — same
- Worker-side credential_ref → plaintext injection (Execution_Engine PLAN_08)
- Strict per-type credential dict-key validation (Phase 2)
- Credential rotation / expiry
