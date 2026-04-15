# PLAN_03 — 노드별 실행 로그 분리 저장 (파티션 적용)

> **브랜치**: `Database` · **작성일**: 2026-04-15 · **완료일**: 2026-04-15 · **상태**: Done
>
> PLAN_01 §7 리스크 #3 에서 미뤘던 "상세 실행 로그" 를 `executions.node_results`
> JSONB 에서 분리해 전용 테이블로 이동한다. Retry 이력을 attempt 단위 N행으로
> 기록하고, 월별 RANGE 파티셔닝을 지금부터 적용해 장래 파티션 도입 기술부채를
> 만들지 않는다. stdout/stderr 원문은 DB 에 담지 않고 GCS URI 만 보관한다.

## 1. 목표

1. `execution_node_logs` 파티션 테이블 신규 — 월별 RANGE(`started_at`)
2. LLM 관측 핵심 4필드 (`model`, `tokens_prompt`, `tokens_completion`, `cost_usd`)
   를 JSONB 가 아닌 컬럼으로 선승격 — 집계 쿼리 성능
3. stdout/stderr 는 GCS URI 참조만 (`stdout_uri`, `stderr_uri text NULL`)
4. `ExecutionNodeLogRepository` ABC + Postgres/InMemory 구현
5. 월 12개 파티션 초기 생성 + `scripts/roll_partitions.py` 월 1회 실행으로
   다음 N 개월 보장 (cron 은 배포 측 책임)

## 2. 범위

**In**
- DDL: `execution_node_logs` 파티션 부모 + 12 개 월 파티션 (현재 월 + 향후 11)
- 인덱스: `(execution_id, node_id, attempt DESC)`, 부분 인덱스 `(model) WHERE model IS NOT NULL`
- `ExecutionNodeLogRepository` ABC + DTO + ORM
- `PostgresExecutionNodeLogRepository`, `InMemoryExecutionNodeLogRepository`
- `scripts/roll_partitions.py` — 월별 파티션 create-if-missing
- 통합 테스트: append/list/summarize + 파티션 라우팅 smoke

**Out (후속)**
- GCS 업로더 구현 — `Execution_Engine` 브랜치 책임
- 파티션 보존 삭제 정책 (90일/1년/무제한) → 별도 운영 PLAN
- 전역 로그 검색(grep 류) → 관측 스택(Loki/ELK) 도입 시점에 재검토
- LLM 사용량 대시보드 쿼리 — 집계 패턴 확정 후 뷰/머터뷰 검토

## 3. 테이블 설계

### 3.1 `execution_node_logs` — 파티션 부모

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `id` | `uuid NOT NULL DEFAULT gen_random_uuid()` | |
| `execution_id` | `uuid NOT NULL REFERENCES executions(id) ON DELETE CASCADE` | |
| `node_id` | `text NOT NULL` | `workflows.graph` 의 노드 인스턴스 id |
| `attempt` | `int NOT NULL DEFAULT 1` | 1-based. Retry 마다 증가 |
| `status` | `text NOT NULL` | CHECK `('running','success','failed','skipped')` |
| `started_at` | `timestamptz NOT NULL` | **파티션 키** |
| `finished_at` | `timestamptz NULL` | 종료 시 기록 |
| `duration_ms` | `integer NULL` | |
| `input` | `jsonb NULL` | 노드 입력 스냅샷 (요약 권장) |
| `output` | `jsonb NULL` | 노드 출력 요약 |
| `error` | `jsonb NULL` | `{"type":..., "message":..., "traceback":...}` |
| `stdout_uri` | `text NULL` | `gs://bucket/executions/{exec}/{node}/{attempt}/stdout.log` |
| `stderr_uri` | `text NULL` | 동일 패턴 |
| `model` | `text NULL` | LLM 노드 한정 |
| `tokens_prompt` | `integer NULL` | LLM 노드 한정 |
| `tokens_completion` | `integer NULL` | LLM 노드 한정 |
| `cost_usd` | `numeric(10,6) NULL` | LLM 노드 한정 |

**PK**: `(id, started_at)` — Postgres 네이티브 파티셔닝은 UNIQUE 제약이 파티션
키를 포함할 것을 요구. `id` 단독으로는 UNIQUE 못 잡음.

**파티셔닝**: `PARTITION BY RANGE (started_at)`. 월별 파티션명
`execution_node_logs_YYYY_MM`.

**FK 제약**: 파티션 부모 테이블에서 `executions(id) ON DELETE CASCADE`.
Postgres 12+ 에서 파티션 테이블이 참조 측(FK 보유) 이 되는 것은 지원됨.

### 3.2 인덱스

```sql
CREATE INDEX idx_enl_execution
    ON execution_node_logs (execution_id, node_id, attempt DESC);

CREATE INDEX idx_enl_model
    ON execution_node_logs (model)
    WHERE model IS NOT NULL;
```

Postgres 11+ 는 파티션 부모의 인덱스가 모든 자식 파티션에 자동 전파된다.

### 3.3 초기 파티션

마이그레이션 시점에 12 개 월 파티션 생성 (현재 월 포함). 구체 월은
`roll_partitions.py` 의 create-if-missing 로직과 동일.

## 4. Repository

### 4.1 ABC + DTO

```python
@dataclass
class ExecutionNodeLog:
    id: UUID
    execution_id: UUID
    node_id: str
    attempt: int
    status: Literal["running","success","failed","skipped"]
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    input: dict | None = None
    output: dict | None = None
    error: dict | None = None
    stdout_uri: str | None = None
    stderr_uri: str | None = None
    model: str | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    cost_usd: float | None = None

class ExecutionNodeLogRepository(ABC):
    @abstractmethod
    async def record(self, log: ExecutionNodeLog) -> None: ...
    @abstractmethod
    async def list_for_execution(
        self, execution_id: UUID
    ) -> list[ExecutionNodeLog]: ...
    @abstractmethod
    async def summarize_llm_usage(
        self, execution_id: UUID
    ) -> dict[str, dict]: ...
```

`summarize_llm_usage` 반환 형태:
`{"gpt-4o": {"tokens_prompt": N, "tokens_completion": M, "cost_usd": X, "calls": K}, ...}`

### 4.2 `ExecutionRepository` 와의 관계

PLAN_01 §7 리스크 #3 결정 (옵션 A 유지):
`executions.node_results` 는 **최신 attempt 요약 전용** 으로 계속 유지.
상세 로그는 `execution_node_logs` 가 단독 소스. 호출자(`Execution_Engine`)는
두 Repository 에 모두 기록 — 이 PLAN 은 기록 경로 자체는 강제하지 않고
Repository 시그니처만 제공.

## 5. 산출물

| 경로 | 내용 |
|------|------|
| `schemas/003_node_logs_partitioned.sql` | 파티션 부모 + 12 파티션 + 인덱스 |
| `migrations/20260501_node_logs_partitioned.sql` | 003 포함 마이그레이션 |
| `src/models/logs.py` | SQLAlchemy ORM (부모 테이블만 매핑; 파티션은 DB 측 라우팅) |
| `src/repositories/base.py` | `ExecutionNodeLogRepository` ABC + DTO 추가 |
| `src/repositories/execution_node_log_repository.py` | Postgres 구현 |
| `tests/fakes.py` | `InMemoryExecutionNodeLogRepository` 추가 |
| `scripts/roll_partitions.py` | 다음 N 개월 파티션 create-if-missing |
| `tests/test_execution_node_logs.py` | 통합 테스트 (append/list/summarize + 파티션 라우팅 smoke) |

## 6. 수용 기준

- [x] 003 마이그레이션이 깨끗이 적용되고 12 개 월 파티션이 생성됨 *(pg_inherits count=12)*
- [x] `pg_inherits` 조회로 부모-자식 관계 확인 가능
- [x] `record_start`/`record_finish` 2-phase 흐름 + 3개 attempt 를 삽입하고
      `list_for_execution()` 이 `(node_id, attempt DESC)` 순으로 반환 *(test_two_phase_write_and_retry_ordering)*
- [x] `summarize_llm_usage()` 가 model 별 토큰/비용/호출수 집계 정확 *(test_llm_usage_summarization)*
- [x] 서로 다른 월의 `started_at` 을 가진 두 로그가 각각 다른 파티션에 착지
      (`tableoid::regclass` 로 검증) *(test_rows_land_in_expected_month_partitions)*
- [x] `roll_partitions.py --months 6` 이 create-if-missing 멱등
- [x] `test_schema_loads` 가 003 을 포함한 전체 스키마 복원 후 `execution_node_logs` 포함 확인

## 구현 노트 (2026-04-15)

- **`timestamptz` 매핑**: `Mapped[datetime]` 만으로는 SQLAlchemy 가
  `TIMESTAMP WITHOUT TIME ZONE` 으로 보낸다. tz-aware 파이썬 datetime 을
  삽입하려면 `DateTime(timezone=True)` 를 명시. PLAN_03 구현 중 한 번 겪음.
- **DDL 스크립트 다중 문장 적용**: `schemas/003` 의 `DO $$ ... $$` 블록 때문에
  단순 `;` split 가 깨진다. `test_schema_loads` 는 이제 raw asyncpg 커넥션의
  `.execute()` 를 사용 (simple query protocol) — SQLAlchemy 래퍼는 prepared
  statement 경로라 multi-statement 를 허용하지 않음.
- **파티션 UPDATE 경로**: `record_finish` 의 UPDATE WHERE 절은 반드시
  `(id, started_at)` 을 둘 다 지정해야 한다. `id` 만으로는 Postgres 가 모든
  파티션을 스캔한다 (파티션 프루닝 실패).

## 7. 리스크 & 오픈 이슈

1. **FK 무결성 비용** — 파티션 테이블에서 `executions(id)` FK 는 INSERT 마다
   부모 테이블 lookup. 대량 삽입 시 병목 가능. 관측 후 필요 시 FK 를
   "트리거 기반 검증" 으로 바꿀지 재검토. MVP 는 FK 유지.

2. **파티션 보존 / 삭제 정책 미정** — 무제한 누적. 한 번의 운영 결정이
   필요하지만 PLAN_03 스코프 밖. `roll_partitions.py` 에 삭제 옵션은 넣지
   않음 — 데이터 삭제는 명시적 운영 이벤트.

3. **`input`/`output` JSONB 크기** — 큰 페이로드가 파티션 부피를 키운다.
   규칙은 "요약만 JSONB, 원문은 GCS URI". 애플리케이션 계층 책임이며 DB 는
   CHECK 제약 없음.

4. **`roll_partitions.py` 자동화** — 이 PLAN 은 스크립트만 제공. 크론/
   스케줄러(예: K8s CronJob, GCP Cloud Scheduler) 는 배포 책임.

5. **GCS URI 검증** — `stdout_uri` / `stderr_uri` 는 자유 텍스트. URI 형식
   검증(예: `gs://` 프리픽스)은 애플리케이션 계층.

## 8. 후속 PLAN 영향

- **PLAN_04 (알림 이력)** — 이 테이블을 읽어 "최근 실패 10건" 같은 알림을
  구성할 수 있음. 직접 결합은 없음.
- **PLAN_05 (Agent 재암호화)** — 무관.
- **PLAN_06 (RAG)** — 과거 워크플로우 성공 로그의 output 을 임베딩 소스로
  쓸 수 있음. 이 PLAN 의 스키마가 그 사용 사례를 차단하지는 않음.
