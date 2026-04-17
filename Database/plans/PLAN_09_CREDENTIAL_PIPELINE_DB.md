# PLAN_09 — Credential Pipeline (Database 부분)

> 청사진: [`docs/context/PLAN_credential_pipeline.md`](../../docs/context/PLAN_credential_pipeline.md)
> 선행 ADR: ADR-004 (Fernet 저장), ADR-013 (Agent 전송), ADR-016 (파이프라인 분리)
> 후속: `API_Server/plans/PLAN_07_CREDENTIAL_PIPELINE.md`

## 목표

BYO + Per-execution 자격증명 파이프라인의 Database 계층 구현:

1. `credentials.type` 컬럼 + CHECK 제약으로 credential_type 카탈로그 고정
2. `CredentialStore.bulk_retrieve(ids, owner_id)` — execution 트리거 1회 복호화 경로
3. `CredentialStore.store()` 가 `credential_type` 을 함께 저장

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `migrations/20260601_credentials_type_column.sql` | 기존 DB 에 type 컬럼 추가 |

### 수정
| 파일 | 변경 |
|------|------|
| `schemas/002_credentials_agents_webhooks.sql` | CREATE TABLE 에 `type` 컬럼 inline 추가 (fresh install 용) |
| `auto_workflow_database/models/extras.py` | `Credential.type` ORM 필드 추가 |
| `auto_workflow_database/repositories/base.py` | `CredentialStore.store` 시그니처에 `credential_type` kwarg, `bulk_retrieve` ABC 추가 |
| `auto_workflow_database/repositories/credential_store.py` | 두 메서드 구현 |
| `tests/fakes.py` | `InMemoryCredentialStore` 미러 구현 |
| `tests/test_credential_store.py` | Postgres 통합 테스트 추가 (type / bulk_retrieve) |

### 신규 (테스트)
| 파일 | 역할 |
|------|------|
| `tests/test_credential_bulk_fake.py` | `InMemoryCredentialStore.bulk_retrieve` 계약 테스트 (DB 불필요) |

## 구현 상세

### 1. 마이그레이션 (`20260601_credentials_type_column.sql`)

```sql
ALTER TABLE credentials
    ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT 'unknown'
    CHECK (type IN ('smtp', 'postgres_dsn', 'slack_webhook', 'http_bearer', 'unknown'));
```

- Postgres 는 `ADD COLUMN` 과 함께 inline `CHECK` 허용 → 컬럼+제약 원자적 추가.
- `IF NOT EXISTS` 로 fresh install (schemas/002 경로) 와 충돌 없음.

### 2. `schemas/002_credentials_agents_webhooks.sql` 갱신

credentials CREATE TABLE 에 `type` 컬럼 inline 추가. fresh install 시 마이그레이션이 no-op 이 되도록.

### 3. `CredentialStore.store` 시그니처 확장

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

- **위치 인자 순서 유지** — 기존 `store(owner_id, name, plaintext)` 호출 깨지지 않음
- `credential_type` 은 kwarg, 기본값 `"unknown"` — 레거시/마이그레이션 호환

### 4. `CredentialStore.bulk_retrieve` ABC + 구현

```python
async def bulk_retrieve(
    self,
    credential_ids: list[UUID],
    *,
    owner_id: UUID,
) -> dict[UUID, dict]:
    """ownership 필터 후 평문 dict 를 credential_id 로 매핑해 반환.
    요청한 id 중 하나라도 결과에 없으면 KeyError — partial success 금지.
    credential_ids 가 빈 리스트면 빈 dict 반환.
    """
```

**Postgres 구현** (`credential_store.py`):
- 한 번의 `SELECT id, encrypted_data FROM credentials WHERE owner_id = :owner AND id = ANY(:ids)` 로 fetch
- 결과 행 개수 < 요청 id 개수 → `KeyError(f"missing credential(s): {diff}")`
- 각 row 의 `encrypted_data` 를 Fernet 복호화 후 `json.loads`

**InMemory 구현** (`fakes.py`):
- 동일 semantic: ownership 필터 + partial-fail-raises + empty-list-allowed

### 5. 보안 불변식

- `bulk_retrieve` 반환값은 호출자 스코프에서만 존재 — 캐시/로그 금지 (docstring 에 명시)
- ownership 미스매치시 *어느 id 가 자기 것이 아닌지* 에러에 노출하지 않는다 (열거 공격 방지) — 포괄 메시지 `"missing credential(s)"` 로 통일

## 테스트 전략

### Postgres 통합 (`tests/test_credential_store.py` 추가, DATABASE_URL 없으면 skip)
1. `test_store_with_type` — `store(..., credential_type="smtp")` 후 직접 SELECT 로 `type='smtp'` 검증
2. `test_store_default_type_is_unknown` — kwarg 생략 → `type='unknown'`
3. `test_store_rejects_invalid_type` — `credential_type="bogus"` → IntegrityError (CHECK 위반)
4. `test_bulk_retrieve_happy` — 3개 저장 후 bulk_retrieve → 3개 plaintext 일치
5. `test_bulk_retrieve_ownership_filter` — owner A 의 credential 을 owner B 로 조회 → KeyError
6. `test_bulk_retrieve_missing_id_raises` — 존재하지 않는 UUID 섞어서 요청 → KeyError
7. `test_bulk_retrieve_empty_list` — 빈 리스트 → 빈 dict

### InMemory fake (`tests/test_credential_bulk_fake.py`, DB 불필요)
1. `test_fake_store_preserves_type` — credential_type kwarg 저장 확인
2. `test_fake_bulk_retrieve_happy`
3. `test_fake_bulk_retrieve_ownership_filter`
4. `test_fake_bulk_retrieve_missing_raises`
5. `test_fake_bulk_retrieve_empty_list`

## 체크리스트

- [ ] 마이그레이션 SQL + schemas/002 동기 업데이트
- [ ] `Credential` ORM 에 `type` 컬럼
- [ ] `CredentialStore` ABC + Fernet/InMemory 구현
- [ ] Postgres 통합 테스트 7개 (skip on no DB)
- [ ] fake 단위 테스트 5개 (항상 실행)
- [ ] 기존 테스트 호환 (`store(owner_id, name, plaintext)` 호출부 깨지지 않음)
- [ ] 커밋 → push → PR

## Out of scope

- credential rotation / 만료 (Phase 2)
- credential type 별 schema 강제 validation (API_Server 책임)
- audit 테이블 (후속 결정 — 청사진 §1.6 불변식은 로그 기록 형식 아님)
