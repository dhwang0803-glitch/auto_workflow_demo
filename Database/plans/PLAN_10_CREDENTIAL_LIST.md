# PLAN_10 — CredentialStore.list_by_owner (metadata-only)

> 선행: PLAN_09 (PR #47, 머지) — `credentials.type` 컬럼 + `bulk_retrieve`
> 후속 소비자: API_Server `GET /api/v1/credentials` + `GET /credentials/{id}` (다음 PR)

## 목적

API_Server PLAN_07 에서 `GET`/`LIST` 엔드포인트가 `CredentialStore` ABC 미지원으로
deferred 됐다. 본 PR 은 **평문 복호화 없이 메타데이터만** 반환하는 `list_by_owner`
를 추가하여 후속 API 가 credential 선택 UI/프로그래매틱 조회를 만들 수 있게 한다.

## 파일 변경

### 수정
| 파일 | 변경 |
|------|------|
| `auto_workflow_database/repositories/base.py` | `CredentialMetadata` dataclass + `CredentialStore.list_by_owner` ABC 추가 |
| `auto_workflow_database/repositories/credential_store.py` | Fernet 구현 — metadata-only SELECT |
| `tests/fakes.py` | `InMemoryCredentialStore` 에 `created_at` 보관 + `list_by_owner` |
| `tests/test_credential_store.py` | Postgres 통합 테스트 3개 추가 |
| `tests/test_credential_bulk_fake.py` | fake 단위 테스트 3개 추가 |

### 신규
없음 (기존 파일 확장만).

## 구현 상세

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

### 2. ABC 확장

```python
@abstractmethod
async def list_by_owner(self, owner_id: UUID) -> list[CredentialMetadata]:
    """Metadata-only listing for the caller's own credentials.
    Sorted by created_at DESC (most recent first).
    Empty list when the owner has no credentials."""
```

### 3. Fernet 구현

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

- `encrypted_data` 컬럼은 SELECT 에서 빠져도 무방 (필요 없음). SQLAlchemy ORM
  기본은 전체 컬럼 로드지만 metadata 만 투영하므로 불필요한 bytes 반환은 무시 가능.
  성능 최적화 (deferred load) 는 후속.

### 4. InMemory fake 확장

기존 tuple `(owner_id, name, credential_type, plaintext)` 에 `created_at` 추가:
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

`datetime.now(timezone.utc)` 로 memory rule "datetime timezone 통일" 준수.

## 보안 불변식

- 반환 DTO 에 `encrypted_data` 도 plaintext 도 없음 — 후속 API 에서 그대로
  응답 직렬화 해도 안전.
- `owner_id` 필터 강제 — cross-tenant 유출 차단 (bulk_retrieve 와 동일 정책).

## 테스트 전략

### Postgres 통합 (`tests/test_credential_store.py` 추가, skipif DATABASE_URL)
1. `test_list_by_owner_happy` — 3개 저장 후 list 하면 3개 반환 + created_at DESC 정렬 + plaintext 없음
2. `test_list_by_owner_empty` — 등록한 적 없는 user_id → 빈 리스트
3. `test_list_by_owner_ownership_filter` — user A 의 credential 은 user B 조회시 안 보임

### InMemory fake (`tests/test_credential_bulk_fake.py` 추가)
4. `test_fake_list_by_owner_happy`
5. `test_fake_list_by_owner_ordered_by_created_at_desc`
6. `test_fake_list_by_owner_empty`

## 체크리스트

- [ ] `CredentialMetadata` DTO + ABC 메서드
- [ ] Fernet 구현
- [ ] Fake 구현 + created_at 저장
- [ ] Postgres 통합 테스트 3개
- [ ] Fake 단위 테스트 3개
- [ ] 전체 52→58 유지
- [ ] 커밋 → push → PR

## Out of scope

- deferred column load 성능 최적화
- pagination / keyset cursor (현재 사용자당 credentials 수 적을 것으로 기대. 수백 개 도달시 후속)
- `CredentialMetadata` 에 `updated_at` — 현재 UPDATE 흐름 없음 (DELETE + 재등록)
