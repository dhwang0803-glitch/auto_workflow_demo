# PLAN_09 — Credential GET/LIST endpoints

> Predecessor: Database PLAN_10 (PR #55, merged) — `CredentialStore.list_by_owner()` + `CredentialMetadata`
> Merging this PR finalizes BYO credential CRUD (POST / GET-list / GET-one / DELETE).

## Purpose

Adds the credential-lookup endpoints deferred in PLAN_07. Returns metadata
only (no plaintext decryption) — covering the Frontend credential picker,
CLI inspection, and a user listing their own credentials.

## File changes

### Modified
| File | Change |
|------|--------|
| `app/models/credential.py` | Add optional `created_at: datetime \| None = None` to `CredentialResponse` (None on POST, populated on GET/LIST) |
| `app/services/credential_service.py` | Add `list(user)` + `get(user, credential_id)` methods |
| `app/routers/credentials.py` | Add `GET /api/v1/credentials` + `GET /{credential_id}` endpoints |
| `tests/test_credentials.py` | Add 6 GET/LIST integration tests |

### New
None.

## Implementation details

### 1. `CredentialResponse` extension

```python
class CredentialResponse(BaseModel):
    id: UUID
    name: str
    type: str
    created_at: datetime | None = None
```

POST only has the UUID returned by `CredentialStore.store()`, so
`created_at=None`. GET/LIST pass through the value from
`CredentialMetadata`.

### 2. `CredentialService` extension

```python
async def list(self, user: User) -> list[CredentialMetadata]:
    return await self._store.list_by_owner(user.id)

async def get(self, user: User, credential_id: UUID) -> CredentialMetadata:
    # 2 queries — cheap enough at realistic credential counts (<a few hundred).
    # Adding a dedicated get_metadata(id, owner_id) would mean revisiting the
    # Database branch, so skip.
    for row in await self._store.list_by_owner(user.id):
        if row.id == credential_id:
            return row
    raise NotFoundError("credential not found")
```

- `get` is a list + filter — ownership enforcement is built into `list_by_owner`'s WHERE filter
- 1-query optimization waits until a future `CredentialStore.get_metadata` is needed (out of scope)

### 3. Router

```python
@router.get("", response_model=list[CredentialResponse])
async def list_credentials(user, svc) -> list[CredentialResponse]:
    rows = await svc.list(user)
    return [CredentialResponse(
        id=r.id, name=r.name, type=r.type, created_at=r.created_at,
    ) for r in rows]

@router.get("/{credential_id}", response_model=CredentialResponse)
async def get_credential(credential_id, user, svc) -> CredentialResponse:
    r = await svc.get(user, credential_id)
    return CredentialResponse(
        id=r.id, name=r.name, type=r.type, created_at=r.created_at,
    )
```

## Security invariants

- The response DTO carries neither `plaintext` nor `encrypted_data` — `CredentialMetadata` itself has no plaintext field
- Ownership enforcement on `get` is built into the `list_by_owner` filter (SQL WHERE)
- Lookup of another user's credential → `NotFoundError` (not 403 — prevents enumeration)

## Test strategy

### test_credentials.py (6 added, skipif DATABASE_URL)

1. `test_list_credentials_empty_for_new_user` — freshly logged-in user → `[]`
2. `test_list_credentials_returns_created_items` — after creating 3, list returns 3, DESC-sorted, no plaintext field
3. `test_list_credentials_isolated_to_user` — user A's credentials are invisible in user B's list
4. `test_get_credential_by_id` — single-row metadata is correct + no plaintext
5. `test_get_credential_not_owned_404` — looking up another user's credential → 404
6. `test_get_credential_nonexistent_404` — random UUID → 404

## Checklist

- [ ] `CredentialResponse` optional `created_at`
- [ ] `CredentialService.list` + `get`
- [ ] 2 router endpoints
- [ ] 6 tests pass; overall 75 → 81
- [ ] No regression in the existing POST / DELETE tests
- [ ] Commit → push → PR

## Out of scope

- Pagination (currently returns a naked list; revisit on reaching hundreds)
- `updated_at` on `CredentialResponse` — there is no UPDATE flow today
- Credential detail with audit log (who used the credential when) — Phase 2
