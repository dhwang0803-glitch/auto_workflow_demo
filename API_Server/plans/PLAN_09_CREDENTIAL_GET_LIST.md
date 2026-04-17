# PLAN_09 — Credential GET/LIST endpoints

> 선행: Database PLAN_10 (PR #55, 머지) — `CredentialStore.list_by_owner()` + `CredentialMetadata`
> 본 PR 머지 시 BYO credential CRUD 완결 (POST/GET-list/GET-one/DELETE).

## 목적

PLAN_07 에서 deferred 된 credential 조회 엔드포인트 추가. 평문 복호화 없이
metadata 만 반환하여 Frontend credential picker / CLI 조회 / 사용자 자기
credential 목록 조회 use case 커버.

## 파일 변경

### 수정
| 파일 | 변경 |
|------|------|
| `app/models/credential.py` | `CredentialResponse` 에 `created_at: datetime \| None = None` 옵셔널 추가 (POST 에선 None, GET/LIST 에선 populated) |
| `app/services/credential_service.py` | `list(user)` + `get(user, credential_id)` 메서드 추가 |
| `app/routers/credentials.py` | `GET /api/v1/credentials` + `GET /{credential_id}` 엔드포인트 추가 |
| `tests/test_credentials.py` | GET/LIST 통합 테스트 6개 추가 |

### 신규
없음.

## 구현 상세

### 1. `CredentialResponse` 확장

```python
class CredentialResponse(BaseModel):
    id: UUID
    name: str
    type: str
    created_at: datetime | None = None
```

POST 는 `CredentialStore.store()` 반환 UUID 만 가지므로 `created_at=None`.
GET/LIST 는 `CredentialMetadata` 에서 받은 값 그대로 넘김.

### 2. `CredentialService` 확장

```python
async def list(self, user: User) -> list[CredentialMetadata]:
    return await self._store.list_by_owner(user.id)

async def get(self, user: User, credential_id: UUID) -> CredentialMetadata:
    # 2 queries — 현실적 credential 개수(<수백) 에서 충분히 저렴.
    # 전용 get_metadata(id, owner_id) 추가는 Database branch 재방문이므로 skip.
    for row in await self._store.list_by_owner(user.id):
        if row.id == credential_id:
            return row
    raise NotFoundError("credential not found")
```

- `get` 은 list + filter 방식 — ownership 검증이 `list_by_owner` 의 WHERE 필터에 내장됨
- 1-query 최적화는 향후 `CredentialStore.get_metadata` 추가 필요시 고려 (scope 외)

### 3. 라우터

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

## 보안 불변식

- 응답 DTO 에 `plaintext` / `encrypted_data` 없음 — `CredentialMetadata` 자체에 평문 필드 부재
- `get` 의 ownership 검증은 `list_by_owner` 필터에 내장 (SQL WHERE)
- 타 유저의 credential 조회 → `NotFoundError` (403 아님, enumeration 방지)

## 테스트 전략

### test_credentials.py (추가 6개, skipif DATABASE_URL)

1. `test_list_credentials_empty_for_new_user` — 갓 로그인 유저 → `[]`
2. `test_list_credentials_returns_created_items` — 3개 등록 후 list → 3개 반환, DESC 정렬, plaintext 필드 부재
3. `test_list_credentials_isolated_to_user` — 유저 A 의 credentials 가 유저 B 의 list 에 안 보임
4. `test_get_credential_by_id` — 단건 조회 metadata 정확 + plaintext 없음
5. `test_get_credential_not_owned_404` — 타 유저 credential 조회 → 404
6. `test_get_credential_nonexistent_404` — 랜덤 UUID → 404

## 체크리스트

- [ ] `CredentialResponse` 옵셔널 `created_at`
- [ ] `CredentialService.list` + `get`
- [ ] 라우터 2 엔드포인트
- [ ] 테스트 6개 pass, 전체 75→81
- [ ] 기존 POST/DELETE 테스트 회귀 없음
- [ ] 커밋 → push → PR

## Out of scope

- Pagination (현재 naked list 반환, 수백개 도달시 후속)
- `CredentialResponse` 에 `updated_at` — 현재 UPDATE 흐름 없음
- credential detail with audit log (누가 언제 credential 사용했는지) — Phase 2
