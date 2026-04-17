# PLAN_07 — Credential Pipeline (API_Server 부분)

> 청사진: [`docs/context/PLAN_credential_pipeline.md`](../../docs/context/PLAN_credential_pipeline.md)
> 선행: Database PLAN_09 (머지됨 PR #47) — `CredentialStore.bulk_retrieve` + `type` 컬럼 준비됨
> 후속: Execution_Engine PLAN_08 — Worker 가 credential_ref 를 실제로 해소하여 노드 config 에 평문 주입

## 목적

BYO 자격증명 CRUD API 제공 + workflow 실행 트리거 시 credential_ref **validation** 수행
(ownership + 존재 확인). **평문 주입은 이 PR 범위 밖** — Worker/Agent 가 담당 (블루프린트
§1.6 보안 불변식 "평문은 broker/DB 를 거치지 않는다" 준수).

## 스코프 조정 (2026-04-17 결정)

블루프린트 §2 ② 은 "API_Server 가 execute_workflow 에서 credential_ref 해소" 로 기술했으나,
실제 구조상 Celery args 에는 `execution_id` 만 전달되고 Worker 가 DB 에서 graph 를 재조회한다.
API_Server 에서 평문을 주입해도 Worker 에 전달되지 않음. 또한 평문이 Redis broker 를 거치는
것은 §1.6 보안 불변식 1번 위반.

**조정된 책임 분배:**
- **API_Server (이 PR)**: credential CRUD + execute_workflow 에서 **validation only**.
  `bulk_retrieve(ids, owner_id)` 로 ownership+존재 확인 후 반환값 즉시 폐기. 실제 평문 주입은 수행하지 않음.
- **Execution_Engine PLAN_08 (다음 PR)**: WorkerContainer 에 CredentialStore 주입.
  `_execute()` 가 노드 실행 직전에 credential_ref 해소 → 노드에 평문 config 전달.

이 분배가 블루프린트 §2 ③ ("~10 LOC" 로 허용된 범위) 를 정식 PLAN 으로 승격시킨다.
청사진 갱신은 별도 docs PR.

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `app/models/credential.py` | Pydantic — `CredentialCreate`, `CredentialResponse`, `CredentialType` Literal |
| `app/services/credential_service.py` | `CredentialService` — create/delete + execute 시 validation |
| `app/routers/credentials.py` | `POST /api/v1/credentials`, `DELETE /api/v1/credentials/{id}` |
| `tests/test_credentials.py` | 라우터 통합 테스트 |
| `tests/test_credential_execute_validation.py` | execute_workflow credential_ref validation 테스트 |

### 수정
| 파일 | 변경 |
|------|------|
| `app/config.py` | `credential_master_key: str` 추가 (Fernet base64) |
| `app/container.py` | `FernetCredentialStore` 인스턴스화 + `CredentialService` 조립 |
| `app/main.py` | `credentials_router` 등록 + `credential_service` 를 `app.state` 에 노출 |
| `app/services/workflow_service.py` | `execute_workflow` 가 `CredentialStore` 받고 credential_ref validation 수행 |
| `tests/conftest.py` | Settings fixture 에 `credential_master_key` 추가 |
| `.env.example` | `CREDENTIAL_MASTER_KEY` 변수 안내 |

### 범위 밖 (명시적)
- `GET /api/v1/credentials` (list) + `GET /api/v1/credentials/{id}` (metadata) — Database
  `list_by_owner()` 메서드가 없어 `CredentialStore` ABC 확장 필요. 별도 Database PR 에서 추가 후 follow-up.
- Worker 측 credential_ref → config 머지 (Execution_Engine PLAN_08 담당)

## 구현 상세

### 1. `CredentialCreate` / `CredentialResponse` (`app/models/credential.py`)

```python
CredentialType = Literal["smtp", "postgres_dsn", "slack_webhook", "http_bearer"]

class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: CredentialType
    plaintext: dict  # type-specific validation 은 §1.2 카탈로그 기반 확장 여지

class CredentialResponse(BaseModel):
    id: UUID
    name: str
    type: str
```

- `plaintext` 는 요청 body 에만 존재, 응답에는 없음
- `type` 은 `"unknown"` 을 포함하지 않음 — 공개 API 로 생성되는 것은 반드시 카탈로그 내 타입
- 타입별 key 검증 (e.g. `smtp` 는 host/port/user/password 필수) 은 현재 PR 에서 skip — 엄격 검증 추가는 Phase 2 (frontend UX 결정 후)

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
            # credentials_owner_name_uq 충돌
            raise DuplicateNameError("credential name already used") from e

    async def delete(self, user: User, credential_id: UUID) -> None:
        # bulk_retrieve 로 ownership 검증 (존재+소유 둘 다 확인)
        # 반환된 평문은 즉시 버림 (함수 지역 스코프)
        try:
            await self._store.bulk_retrieve([credential_id], owner_id=user.id)
        except KeyError:
            raise NotFoundError("credential not found")
        await self._store.delete(credential_id)

    async def validate_refs(
        self, user: User, credential_ids: list[UUID]
    ) -> None:
        """execute_workflow 용 — credential_ref validation. 평문 즉시 폐기.
        누락된 id 가 하나라도 있으면 NotFoundError (enumeration 방지)."""
        if not credential_ids:
            return
        try:
            await self._store.bulk_retrieve(credential_ids, owner_id=user.id)
        except KeyError:
            raise NotFoundError("credential not found")
```

### 3. 라우터 (`app/routers/credentials.py`)

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

`workflow_service.execute_workflow` 시작부에 삽입:

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

- 실패 시 `NotFoundError` → 404 (enumeration 방지, owner ≠ user 와 id 존재 안 함 구분 안 함)
- execute 자체가 생성되지 않음 — validation 이 create(execution) 보다 먼저

### 5. DuplicateNameError 에러 클래스

`app/errors.py` 에 추가:

```python
class DuplicateNameError(DomainError):
    """409 — unique constraint on (owner_id, name)."""
    http_status = 409
```

## 테스트 전략

### test_credentials.py (라우터 E2E, skipif DATABASE_URL)

1. `test_create_credential_returns_201_with_id` — POST 성공, 응답에 plaintext 없음
2. `test_create_credential_with_unknown_type_422` — Pydantic Literal 검증
3. `test_create_duplicate_name_409` — UNIQUE (owner, name) 충돌
4. `test_delete_credential_204` — DELETE 성공
5. `test_delete_credential_not_owned_404` — 타 유저 credential → NotFoundError
6. `test_delete_credential_nonexistent_404`

### test_credential_execute_validation.py (execute_workflow E2E)

1. `test_execute_with_valid_credential_ref_queued` — credential 미리 등록 후 workflow 에 ref → 202
2. `test_execute_with_nonexistent_credential_ref_404`
3. `test_execute_with_other_users_credential_ref_404` — cross-tenant enumeration 방지
4. `test_execute_with_no_credential_refs_works` — 기존 path 회귀 확인

## 체크리스트

- [ ] Settings `credential_master_key`
- [ ] AppContainer `CredentialStore` + `CredentialService`
- [ ] Pydantic credential 모델
- [ ] CredentialService (create + delete + validate_refs)
- [ ] credentials 라우터 (POST + DELETE)
- [ ] workflow_service.execute_workflow validation 삽입
- [ ] DuplicateNameError 에러 클래스
- [ ] main.py 라우터 등록 + app.state 노출
- [ ] 테스트 10 pass (기존 62 + 10 = 72)
- [ ] 커밋 → push → PR

## Out of scope

- `GET /credentials` (list) — Database `list_by_owner()` 선행 필요
- `GET /credentials/{id}` — 동일
- Worker 측 credential_ref → 평문 주입 (Execution_Engine PLAN_08)
- credential type 별 dict key 엄격 검증 (Phase 2)
- credential rotation/만료
