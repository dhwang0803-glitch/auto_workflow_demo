# PLAN_10 — CredentialStore.list_by_owner (metadata-only)

> Predecessor: PLAN_09 (PR #47, merged) — `credentials.type` column + `bulk_retrieve`
> Downstream consumer: API_Server `GET /api/v1/credentials` + `GET /credentials/{id}` (next PR)

## Purpose

In API_Server PLAN_07 the `GET`/`LIST` endpoints were deferred because the
`CredentialStore` ABC didn't support them. This PR adds `list_by_owner`,
returning **metadata only — no plaintext decryption** — so the follow-up API
can build credential-picker UI and programmatic lookups.

## File changes

### Modified
| File | Change |
|------|--------|
| `auto_workflow_database/repositories/base.py` | Add the `CredentialMetadata` dataclass + the `CredentialStore.list_by_owner` ABC |
| `auto_workflow_database/repositories/credential_store.py` | Fernet implementation — metadata-only SELECT |
| `tests/fakes.py` | Track `created_at` on `InMemoryCredentialStore` + add `list_by_owner` |
| `tests/test_credential_store.py` | 3 new Postgres integration tests |
| `tests/test_credential_bulk_fake.py` | 3 new fake unit tests |

### New
None (existing files extended only).

## Implementation details

### 1. `CredentialMetadata` DTO (`base.py`)

```python
@dataclass
class CredentialMetadata:
    """Plaintext-free view of a credentials row — safe to echo via API.
    DO NOT extend with an `encrypted_data` field; API_Server uses this DTO
    directly as the response shape."""
    id: UUID
    name: str
    type: str
    created_at: datetime
```

### 2. ABC extension

```python
@abstractmethod
async def list_by_owner(self, owner_id: UUID) -> list[CredentialMetadata]:
    """Metadata-only listing for the caller's own credentials.
    Sorted by created_at DESC (most recent first).
    Empty list when the owner has no credentials."""
```

### 3. Fernet implementation

```python
async def list_by_owner(self, owner_id: UUID) -> list[CredentialMetadata]:
    async with self._sm() as s:
        stmt = (
            select(CredentialORM)
            .where(CredentialORM.owner_id == owner_id)
            .order_by(CredentialORM.created_at.desc())
        )
        rows = (await s.execute(stmt)).scalars().all()
    return [
        CredentialMetadata(
            id=r.id, name=r.name, type=r.type, created_at=r.created_at,
        )
        for r in rows
    ]
```

- It's fine to leave `encrypted_data` out of the SELECT (not needed). The
  SQLAlchemy ORM defaults to loading all columns; the extra bytes are
  harmless here since we only project the metadata. Performance tuning
  (deferred load) is follow-up work.

### 4. InMemory fake extension

Extend the existing tuple `(owner_id, name, credential_type, plaintext)`
with `created_at`:
```python
# (owner_id, name, credential_type, plaintext, created_at)
self._store: dict[UUID, tuple[UUID, str, str, dict, datetime]] = {}

async def store(self, owner_id, name, plaintext, *, credential_type="unknown"):
    cid = uuid4()
    self._store[cid] = (owner_id, name, credential_type, deepcopy(plaintext),
                        datetime.now(timezone.utc))
    return cid

async def list_by_owner(self, owner_id):
    rows = [
        CredentialMetadata(id=cid, name=n, type=t, created_at=c)
        for cid, (oid, n, t, _pt, c) in self._store.items()
        if oid == owner_id
    ]
    rows.sort(key=lambda m: m.created_at, reverse=True)
    return rows
```

Using `datetime.now(timezone.utc)` honors the "datetime timezone unified"
memory rule.

## Security invariants

- The returned DTO contains neither `encrypted_data` nor plaintext — safe to
  serialize directly in the follow-up API response.
- `owner_id` filtering is enforced — blocks cross-tenant leakage (same
  policy as `bulk_retrieve`).

## Test strategy

### Postgres integration (`tests/test_credential_store.py`, skipif DATABASE_URL)
1. `test_list_by_owner_happy` — store 3, list returns 3, sorted by created_at DESC, no plaintext
2. `test_list_by_owner_empty` — never-registered user_id → empty list
3. `test_list_by_owner_ownership_filter` — user A's credential is invisible to user B

### InMemory fake (`tests/test_credential_bulk_fake.py`)
4. `test_fake_list_by_owner_happy`
5. `test_fake_list_by_owner_ordered_by_created_at_desc`
6. `test_fake_list_by_owner_empty`

## Checklist

- [ ] `CredentialMetadata` DTO + ABC method
- [ ] Fernet implementation
- [ ] Fake implementation + `created_at` storage
- [ ] 3 Postgres integration tests
- [ ] 3 fake unit tests
- [ ] Overall test count stays 52→58
- [ ] Commit → push → PR

## Out of scope

- Deferred column-load performance tuning
- Pagination / keyset cursor (current per-user credential counts are expected to be small; revisit on hitting hundreds)
- A `updated_at` field on `CredentialMetadata` — there is no UPDATE flow today (DELETE + re-register)
