# PLAN_01 — Core Schema + Routing Fields (b′)

> **브랜치**: `Database` · **작성일**: 2026-04-14 · **상태**: Draft
>
> 첫 스키마 파일과 Repository ABC 골격을 확정한다. 범위는 ADR-001/007/008 을
> 만족하는 최소 필드까지. `credentials` / `agents` / `webhook_registry` 와
> `gpu_info` 기반 라우팅(ADR-009, Proposed)은 PLAN_02 로 분리.

## 1. 목표

1. `users / workflows / nodes / executions` 4개 핵심 테이블 DDL 확정
2. Repository ABC 3종 시그니처 정의 (ADR-006)
3. `API_Server` 의 플랜 기반 LLM 라우팅(ADR-008) 에 필요한 **최소 사용자 컬럼** 포함
4. ADR-007 이 `executions` 에 강제하는 관측/승인 상태 컬럼 반영

## 2. 범위

**In**
- DDL: `users`, `workflows`, `nodes`, `executions`
- 사용자 플랜 티어 + 기본 실행 모드 + 외부 API 정책 컬럼 (ADR-008 / ADR-009)
- `executions` 의 Approval 상태머신 + LLM 관측 컬럼 (ADR-007)
- Repository ABC 3종 + `InMemory*` 테스트 더블 시그니처
- 초기 마이그레이션 파일 1개 (`20260414_initial_schema.sql`)

**Out (후속 PLAN)**
- `credentials`, `agents`, `webhook_registry` → PLAN_02
- `users.gpu_info` 및 Agent 하드웨어 라우팅(ADR-009) → PLAN_02 의 `agents` 와 함께
- Postgres 구현체 (`PostgresWorkflowRepository` 등) → PLAN_02 이후
- 상세 실행 로그(노드별 stdout/stderr, structured logs 분리 저장) → PLAN_03
- Inference_Service 관련 스키마 (있다면) → 별도 PLAN

## 3. 테이블 설계

### 3.1 `users` — ADR-001/008 라우팅 기반

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `id` | `uuid PK DEFAULT gen_random_uuid()` | |
| `email` | `citext UNIQUE NOT NULL` | |
| `plan_tier` | `text NOT NULL` | CHECK `IN ('light','middle','heavy')` — ADR-008 라우팅 키 |
| `default_execution_mode` | `text NOT NULL DEFAULT 'serverless'` | CHECK `IN ('serverless','agent')` — ADR-001 |
| `external_api_policy` | `jsonb NOT NULL DEFAULT '{}'::jsonb` | 조직 정책(예: `{"allow_outbound": false}`). ADR-009 폴백 허용/금지 판단 근거 |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |

> `gpu_info` 는 **이 PLAN 범위 밖**. ADR-009 본문에 *"Agent 부팅 시 1회 수집"* 로
> 명시되어 있어 `agents` 테이블에 속함. PLAN_02 에서 같이 처리.
>
> `external_api_policy` 는 free-form JSONB 로 시작. 현 시점 합의된 키는
> `allow_outbound: boolean` 하나이며, 다운스트림(`API_Server`)이 다른 키를
> 박기 전 PLAN 에서 확정한다.

### 3.2 `workflows`

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `id` | `uuid PK` | |
| `owner_id` | `uuid REFERENCES users(id) ON DELETE CASCADE` | |
| `name` | `text NOT NULL` | |
| `settings` | `jsonb NOT NULL` | `{ "execution_mode": "serverless"|"agent", ... }` (ADR-001) |
| `graph` | `jsonb NOT NULL` | 노드/커넥션 (React Flow 포맷). **각 노드 정의에 `output_schema` 필드 포함** (ADR-007) |
| `is_active` | `boolean NOT NULL DEFAULT true` | |
| `created_at` / `updated_at` | `timestamptz NOT NULL DEFAULT now()` | |

인덱스:
- `CREATE INDEX idx_workflows_owner ON workflows(owner_id) WHERE is_active = true;`

> `output_schema` 는 별도 컬럼이 아니라 `graph` JSONB 내부의 노드 속성으로
> 저장된다 (ADR-007 Decision 1: "워크플로우가 JSON 으로 직렬화되는 전체
> 수명주기에서 스키마가 데이터와 함께 이동"). Repository 계약은 이 불변식을
> 문서로 명시한다 — DDL 수준 강제는 없음.

### 3.3 `nodes` — 런타임 노드 카탈로그

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `type` | `text` | 예: `'http.request'`, `'llm'`, `'approval'` |
| `version` | `text` | semver |
| `schema` | `jsonb NOT NULL` | 파라미터 스키마 (Frontend 폼 렌더링용) |
| `registered_at` | `timestamptz NOT NULL DEFAULT now()` | |

복합 PK `(type, version)`.

> 사용자의 그래프에 박힌 노드 인스턴스는 `workflows.graph` 에 저장되고, 이
> 테이블은 "엔진이 알고 있는 노드 타입 목록" 의 카탈로그 역할. Frontend 가
> 사용 가능한 노드 팔레트를 조회하는 경로.

### 3.4 `executions` — ADR-007 관측/승인 확장

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `id` | `uuid PK` | |
| `workflow_id` | `uuid REFERENCES workflows(id) ON DELETE CASCADE` | |
| `status` | `text NOT NULL` | CHECK `IN ('queued','running','paused','resumed','success','failed','rejected','cancelled')` |
| `execution_mode` | `text NOT NULL` | 실행 시점에 고정 (워크플로우 설정 변경 후에도 이력 보존) |
| `started_at` | `timestamptz NULL` | |
| `finished_at` | `timestamptz NULL` | |
| `node_results` | `jsonb NOT NULL DEFAULT '{}'::jsonb` | 노드별 결과 요약 (상세 로그는 PLAN_03) |
| `error` | `jsonb NULL` | `{"node_id":..., "message":...}` |
| `token_usage` | `jsonb NOT NULL DEFAULT '{}'::jsonb` | `{"prompt": N, "completion": M}` 또는 모델별 상세 — ADR-007 |
| `cost_usd` | `numeric(10,6) NOT NULL DEFAULT 0` | 누적 집계. 로컬 vLLM 경로는 0 또는 amortized — ADR-007 |
| `duration_ms` | `integer NULL` | 종료 시 계산 — ADR-007 |
| `paused_at_node` | `text NULL` | ApprovalNode 대기 중인 노드 id — ADR-007 상태머신 |

인덱스:
- `CREATE INDEX idx_executions_workflow_id ON executions(workflow_id, started_at DESC);`
- `CREATE INDEX idx_executions_paused ON executions(paused_at_node) WHERE status = 'paused';` — Approval Inbox 조회 경로

> **상태 전이 (ADR-007 ApprovalNode)**:
> `queued → running → (paused ↔ resumed) → success | failed | rejected | cancelled`
>
> `resumed` 는 일시 상태. Repository 재개 호출은 멱등이어야 한다.
> 승인 대기 수명주기는 ADR-005 의 30초 하드 타임아웃과 **독립**.

## 4. Repository ABC

`Database/src/repositories/base.py` (신규):

```python
from abc import ABC, abstractmethod
from uuid import UUID

class WorkflowRepository(ABC):
    @abstractmethod
    async def get(self, workflow_id: UUID) -> Workflow | None: ...
    @abstractmethod
    async def save(self, workflow: Workflow) -> None: ...
    @abstractmethod
    async def list_by_owner(self, owner_id: UUID, *, active_only: bool = True) -> list[Workflow]: ...
    @abstractmethod
    async def delete(self, workflow_id: UUID) -> None: ...

class ExecutionRepository(ABC):
    @abstractmethod
    async def create(self, execution: Execution) -> None: ...
    @abstractmethod
    async def update_status(
        self,
        execution_id: UUID,
        status: ExecutionStatus,
        *,
        error: dict | None = None,
        paused_at_node: str | None = None,
    ) -> None: ...
    @abstractmethod
    async def append_node_result(
        self,
        execution_id: UUID,
        node_id: str,
        result: dict,
        *,
        token_usage: dict | None = None,
        cost_usd: float | None = None,
    ) -> None: ...
    @abstractmethod
    async def finalize(
        self,
        execution_id: UUID,
        *,
        duration_ms: int,
    ) -> None: ...
    @abstractmethod
    async def get(self, execution_id: UUID) -> Execution | None: ...
    @abstractmethod
    async def list_pending_approvals(self, owner_id: UUID) -> list[Execution]: ...

class CredentialStore(ABC):
    # 시그니처만. 실 구현은 PLAN_02 (ADR-004 Fernet).
    @abstractmethod
    async def store(self, owner_id: UUID, name: str, plaintext: dict) -> UUID: ...
    @abstractmethod
    async def retrieve(self, credential_id: UUID) -> dict: ...
```

테스트 더블 `InMemoryWorkflowRepository`, `InMemoryExecutionRepository` 는
`Database/tests/fakes.py` 로 제공. `API_Server` 단위 테스트가 이 더블만으로
플랜 라우팅(`users.plan_tier` → vLLM / 외부 API) 과 Approval 재개 흐름을
검증할 수 있어야 한다.

## 5. 산출물 / 파일

| 경로 | 내용 |
|------|------|
| `Database/schemas/001_core.sql` | 위 4개 테이블 DDL + 인덱스 + CHECK 제약 |
| `Database/migrations/20260414_initial_schema.sql` | `001_core.sql` 을 포함한 초기 마이그레이션 |
| `Database/src/models/core.py` | SQLAlchemy ORM (users/workflows/nodes/executions) |
| `Database/src/repositories/base.py` | 위 ABC |
| `Database/tests/fakes.py` | `InMemoryWorkflowRepository`, `InMemoryExecutionRepository` |
| `Database/tests/test_schema_loads.py` | 마이그레이션이 빈 DB 에 적용되는지 smoke 테스트 |
| `Database/tests/test_status_transitions.py` | Approval 상태머신 경로 테스트 (In-Memory) |

## 6. 수용 기준

- [ ] `psql -f schemas/001_core.sql` 이 빈 DB 에 에러 없이 적용
- [ ] 모든 CHECK 제약(plan_tier, execution_mode, status) 이 잘못된 값을 거부
- [ ] `InMemoryExecutionRepository` 로 다음 시나리오 단위 테스트가 통과:
  - queued → running → paused(paused_at_node set) → resumed → success
  - running → failed(error set)
  - paused → rejected
- [ ] `API_Server` 가 `users.plan_tier` + `users.external_api_policy` 만으로
      ADR-008 플랜 라우팅 의사결정을 내릴 수 있음 (조회 경로 존재)
- [ ] 모든 Repository 메서드가 async (ADR-002 FastAPI async 정합)

## 7. 리스크 & 오픈 이슈

1. **`external_api_policy` 키 네이밍 미확정**
   현재 합의된 키는 `allow_outbound: boolean` 하나. `API_Server` 가 다른 키를
   박기 전에 이 PLAN 머지 전 합의 필요. 그렇지 않으면 마이그레이션 없이
   계약이 깨진다.

2. **`nodes` 카탈로그 vs `NodeRegistry` 싱크**
   `Execution_Engine` 런타임의 `NodeRegistry` 와 DB `nodes` 가 어긋나면
   Frontend 가 존재하지 않는 노드 타입을 렌더링할 수 있음. 기동 시
   `NodeRegistry → upsert nodes` 경로는 PLAN_02 범위에서 정의.

3. **`executions.node_results` row 비대화**
   대용량 결과를 JSONB 로 축적하면 row 가 커진다. 현재는 "요약만" 규칙을
   문서로 유지하고, 상세 로그 분리 저장은 PLAN_03.

4. **`cost_usd` 집계 단위**
   로컬 vLLM (ADR-008) 경로는 호출당 명시 단가가 없음. Phase 1 에서는 0 으로
   기록하고, Phase 2 에서 amortized 단가 계산 합의 후 백필 여부 결정.

5. **`resumed` 상태의 수명**
   엄밀히는 과도 상태. 구현 편의상 유지하되, Repository `update_status(resumed)`
   이후 즉시 `running` 으로 재전환되도록 계약에 명시.

## 8. 후속 PLAN 예고

- **PLAN_02** — `credentials` (ADR-004 Fernet) + `agents` (+ `gpu_info`, ADR-009 대비) + `webhook_registry` + Postgres 구현체
- **PLAN_03** — 실행 관측 상세: 노드별 로그 분리 저장, retry 이력, Approval 알림 발송 이력
