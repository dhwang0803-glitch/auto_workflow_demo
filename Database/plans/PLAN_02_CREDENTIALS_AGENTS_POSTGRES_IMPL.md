# PLAN_02 — Credentials + Agents + Webhooks + Postgres Repository 구현

> **브랜치**: `Database` · **작성일**: 2026-04-15 · **완료일**: 2026-04-15 · **상태**: Done
>
> PLAN_01 이 4개 코어 테이블 + Repository ABC + InMemory 더블까지 고정했다.
> PLAN_02 는 (1) 남은 3개 테이블, (2) 실제 Postgres Repository 구현체,
> (3) Fernet 자격증명 암호화까지 채워서 `API_Server` 가 테스트 더블이 아닌
> 실제 DB 로 플랜 라우팅과 Webhook 수신을 돌릴 수 있게 만든다.

## 1. 목표

1. `credentials` / `agents` / `webhook_registry` DDL 추가 (ADR-004, ADR-009)
2. `PostgresWorkflowRepository`, `PostgresExecutionRepository`, `FernetCredentialStore` 구현
3. `users.gpu_info` 는 설계상 `agents` 로 이동 — Agent 부팅 시 수집하여 `agents.gpu_info` JSONB 로 저장 (ADR-009)
4. `NodeRegistry → nodes` upsert 경로 정의 (Execution_Engine 기동 시)
5. Webhook 동적 경로 해상: `webhook_registry.path → workflow_id` 조회 인터페이스 추가

## 2. 범위

**In**
- DDL: `credentials`, `agents`, `webhook_registry`
- `agents.gpu_info jsonb` — ADR-009 하드웨어 라우팅 근거
- `FernetCredentialStore` — `CREDENTIAL_MASTER_KEY` 환경변수, AES-256(Fernet)
- Postgres 구현 3종 (`asyncpg` + SQLAlchemy 2.0 async session)
- `WebhookRegistry` Repository ABC 1종 + InMemory/Postgres 구현
- `NodeCatalogRepository` ABC + upsert_many 경로
- 통합 테스트 (`DATABASE_URL` 필수): Repository 각각 happy path

**Out (후속)**
- Agent 공개키 관리 + 자격증명 재암호화 (RSA) → PLAN_03 또는 별도
- Approval 알림 발송 이력 → PLAN_03
- Agent heartbeat → Agent 쪽 PLAN

## 3. 테이블 설계

### 3.1 `credentials` — ADR-004 Fernet

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `id` | `uuid PK DEFAULT gen_random_uuid()` | |
| `owner_id` | `uuid REFERENCES users(id) ON DELETE CASCADE` | |
| `name` | `text NOT NULL` | 사용자 지정 이름 (예: `"slack-bot-token"`) |
| `encrypted_data` | `bytea NOT NULL` | Fernet ciphertext. **평문 절대 저장 금지** |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |

`UNIQUE (owner_id, name)`.

> 평문 자격증명은 `FernetCredentialStore.retrieve()` 반환값으로만 존재하며,
> 로그/응답 바디에 포함 금지. Agent 모드 전송 시 Agent 공개키로 재암호화
> 하는 경로는 PLAN_03 범위.

### 3.2 `agents` — ADR-009 하드웨어 라우팅

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `id` | `uuid PK DEFAULT gen_random_uuid()` | |
| `owner_id` | `uuid REFERENCES users(id) ON DELETE CASCADE` | |
| `public_key` | `text NOT NULL` | RSA PEM. 자격증명 재암호화용 |
| `gpu_info` | `jsonb NOT NULL DEFAULT '{}'::jsonb` | Agent 부팅 시 1회 수집 — ADR-009 |
| `last_heartbeat` | `timestamptz NULL` | |
| `registered_at` | `timestamptz NOT NULL DEFAULT now()` | |

인덱스: `CREATE INDEX idx_agents_owner ON agents(owner_id);`

> **`gpu_info` 스키마 합의 필요** — 최소한 `{"vendor": "nvidia"|"amd"|"cpu_only",
> "vram_gb": number, "backend": "vllm"|"ktransformers"|null}` 3개 키는 고정.
> ADR-009 KTransformers CPU-only 경로 판단에 `backend=="ktransformers"` 가
> 직접 사용된다.

### 3.3 `webhook_registry` — 동적 Webhook 라우팅

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `id` | `uuid PK` | |
| `workflow_id` | `uuid REFERENCES workflows(id) ON DELETE CASCADE` | |
| `path` | `text UNIQUE NOT NULL` | `/webhooks/<uuid>` 형태 |
| `secret` | `text NULL` | HMAC 검증용 |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |

인덱스: `CREATE INDEX idx_webhook_path ON webhook_registry(path);`

## 4. Repository 구현

### 4.1 추가 ABC

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

### 4.2 Postgres 구현체

| 파일 | 클래스 |
|------|--------|
| `src/repositories/workflow_repository.py` | `PostgresWorkflowRepository` |
| `src/repositories/execution_repository.py` | `PostgresExecutionRepository` |
| `src/repositories/credential_store.py` | `FernetCredentialStore` |
| `src/repositories/webhook_registry.py` | `PostgresWebhookRegistry` + `InMemoryWebhookRegistry` |
| `src/repositories/node_catalog.py` | `PostgresNodeCatalog` + `InMemoryNodeCatalog` |

공통 패턴:
- 생성자 인자: `sessionmaker: async_sessionmaker[AsyncSession]` — 엔진은 `API_Server` 가 주입
- 모든 메서드 async, 내부 트랜잭션 자동 커밋 (`async with session.begin():`)
- ORM 객체 ↔ `base.py` 의 dataclass DTO 변환은 private 헬퍼로 분리

### 4.3 Fernet 키 로딩

```python
# src/repositories/credential_store.py
class FernetCredentialStore(CredentialStore):
    def __init__(self, sessionmaker, *, master_key: bytes):
        self._f = Fernet(master_key)
        self._sm = sessionmaker
```

`master_key` 는 `API_Server` 부팅 시 `os.environ["CREDENTIAL_MASTER_KEY"]`
로부터 로드. 테스트는 `Fernet.generate_key()` 로 임시키 사용.

## 5. 산출물

| 경로 | 내용 |
|------|------|
| `schemas/002_credentials_agents_webhooks.sql` | 위 3 테이블 DDL |
| `migrations/20260420_credentials_agents_webhooks.sql` | 002 포함 마이그레이션 |
| `src/models/extras.py` | SQLAlchemy ORM (`Credential`, `Agent`, `WebhookRegistry`, `NodeDefinition`) |
| `src/repositories/{workflow,execution,credential_store,webhook_registry,node_catalog}.py` | Postgres 구현체 |
| `src/repositories/base.py` 갱신 | `WebhookRegistry`, `NodeCatalogRepository` ABC 추가 |
| `tests/test_postgres_repositories.py` | `DATABASE_URL` 필수 통합 테스트 |
| `tests/test_credential_store.py` | Fernet 왕복 + 키 없음 실패 케이스 |

## 6. 수용 기준

- [x] `python scripts/migrate.py` 가 002 마이그레이션을 깨끗이 적용 *(2026-04-15)*
- [x] `PostgresExecutionRepository` 로 PLAN_01 상태머신 시나리오가 실제 DB 에서 통과 *(test_postgres_repositories)*
- [x] `FernetCredentialStore.store → retrieve` 왕복이 평문 동등 *(test_credential_store)*
- [x] 잘못된 키로 로드 시 `InvalidToken` 발생 *(test_wrong_key_rejects_ciphertext)*
- [x] `PostgresWebhookRegistry.resolve` 가 인덱스 경로로 조회 *(unique index on `webhook_registry.path`)*
- [x] `PostgresNodeCatalog.upsert_many` 가 `(type, version)` 기준 멱등 *(test_node_catalog_upsert_idempotent)*

## 7. 오픈 이슈

1. ~~**`agents.gpu_info` JSONB 키 스펙**~~ → **MVP 확정 (2026-04-15)**
   `{vendor, vram_gb, backend}` 3키를 002 DDL 주석에 명시. Agent 쪽 PLAN 이
   추가 필드를 요구하면 포워드 호환 확장 (미정의 키는 저장 허용).
2. **Fernet 키 로테이션** — MVP 는 단일 키. MultiFernet 로의 전환 경로는
   PLAN_03 이후. 지금은 `CREDENTIAL_MASTER_KEY` 가 바뀌면 기존 자격증명 복호화
   실패 — 배포 노트로 명시.
3. **`webhook_registry.secret` 없는 레코드** — HMAC 검증을 강제할지 말지는
   `API_Server` 의 Webhook 수신 PLAN 에서 결정. 지금은 NULL 허용.
4. **`NodeRegistry ↔ nodes` 싱크 시점** — `Execution_Engine` 기동 시 1회로
   합의. 런타임 중 노드 플러그인 핫스왑은 지원하지 않음.

## 8. 구현 노트 (2026-04-15)

- **`test_schema_loads` 파괴성 주의**: 이 테스트는 `DROP SCHEMA public CASCADE`
  후 모든 `schemas/*.sql` 을 재적용한다. 새 DDL 파일을 추가하면 이 테스트의
  `expected` 테이블 집합을 반드시 갱신해야 한다 — 그렇지 않으면 후속 통합
  테스트에서 "테이블 없음" 로 깨진다 (실제로 PLAN_02 구현 중 한 번 겪음).
- **JSONB in-place 변이**: `PostgresExecutionRepository.append_node_result` 는
  `flag_modified()` 로 변경 마킹. 누락 시 SQLAlchemy 가 UPDATE 를 발행하지
  않아 조용히 사라진다.

## 9. 후속 PLAN 예고

- **PLAN_03** — 실행 관측 상세(노드별 로그 분리 저장), Approval 알림 발송 이력,
  Agent 공개키 기반 자격증명 재암호화 전송
- **PLAN_04** — RAG: 사용자 워크플로우/템플릿 임베딩 컬럼 도입 (pgvector 이미
  설치됨, 마이그레이션만 필요)
