# PLAN_03 — 수동 실행 트리거 + Execution 이력 조회 (API_Server)

> **브랜치**: `API_Server` · **작성일**: 2026-04-16 · **상태**: Draft
>
> PLAN_02 Workflow CRUD 위에 실행 트리거와 이력 조회를 얹는다.
> 실행은 비동기(202 Accepted) — `executions` row 를 `queued` 상태로
> 생성하고 `execution_id` 를 즉시 반환한다. 실제 디스패치(Celery/Agent)
> 는 Execution_Engine 브랜치 소관이므로 여기선 스텁. Scheduler 기반
> 자동 실행(activate/deactivate)은 PLAN_04 로 분리됨.

## 1. 목표

1. `POST /workflows/{id}/execute` — 수동 실행 트리거 (202 + execution_id)
2. `GET /executions/{id}` — 실행 단건 조회
3. `GET /workflows/{id}/executions` — 워크플로우별 실행 목록 (keyset 페이지네이션)
4. `main.py` lifespan 에 `PostgresExecutionRepository` 주입
5. `execution_mode` 디스패치는 **스텁** — row 생성만, 실제 큐잉/푸시 없음

## 2. 범위

**In**
- Pydantic: `ExecutionResponse`, `ExecutionListResponse` (items/next_cursor/has_more)
- `app/services/workflow_service.py` 확장 — `execute_workflow`, `get_execution`, `list_executions`
- `app/routers/executions.py` 신규 — 이력 조회 라우터
- `app/routers/workflows.py` 확장 — `POST /{id}/execute` 엔드포인트 추가
- `app/dependencies.py` 확장 — `get_execution_repo`
- `app/main.py` 확장 — `PostgresExecutionRepository` lifespan 주입 + 라우터 등록
- `app/errors.py` 확장 — `WorkflowNotActiveError` (비활성 workflow 실행 시도 거부)
- `tests/test_executions.py` 신규 — E2E 테스트

**Out (후속 PLAN)**
- Scheduler 워커 + activate/deactivate — **PLAN_04**
- 실제 Celery 큐잉 / Agent WebSocket push — **Execution_Engine 브랜치**
- Execution 취소 (`POST /executions/{id}/cancel`) — Phase 2
- Execution 상세 노드 로그 (`GET /executions/{id}/logs`) — Phase 2

## 3. 선결 결정 (확정)

| 결정 | 확정 내용 | 근거 |
|---|---|---|
| 실행 응답 | 비동기 202 + `execution_id` | 장시간 워크플로우 대응, Execution_Engine 분리 자연 |
| 페이지네이션 | keyset (`created_at DESC, id DESC`) | append-only 시계열, PLAN_06 에서 DB 지원 완료 |
| Scheduler 분리 | PLAN_04 로 분리 | 별도 배포 단위 (프로세스), 볼륨 과대 |
| execution_mode | 스텁 (`# TODO(Execution_Engine)`) | Celery/Agent 미구현 |

## 4. 엔드포인트

| 메서드 | 경로 | 설명 | 응답 |
|--------|------|------|------|
| `POST` | `/api/v1/workflows/{id}/execute` | 수동 실행 트리거 | 202 `ExecutionResponse` |
| `GET` | `/api/v1/executions/{id}` | 실행 단건 조회 | 200 `ExecutionResponse` / 404 |
| `GET` | `/api/v1/workflows/{id}/executions` | 워크플로우별 실행 목록 | 200 `ExecutionListResponse` |

**에러 코드**:

| 상황 | HTTP |
|------|------|
| 워크플로우 미존재 / 소유권 없음 | 404 |
| 비활성 워크플로우 실행 시도 | 409 Conflict |
| 실행 미존재 | 404 |
| 인증 실패 | 401 |

## 5. Pydantic 스키마

```python
class ExecutionResponse(BaseModel):
    id: UUID
    workflow_id: UUID
    status: str
    execution_mode: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime | None
    error: dict | None

class ExecutionListResponse(BaseModel):
    items: list[ExecutionResponse]
    next_cursor: str | None
    has_more: bool
```

- `next_cursor` 는 `"{created_at_iso}_{id}"` 문자열로 인코딩. 라우터에서
  파싱 시 `split("_", 1)` 로 인라인 해제 — `_parse_cursor` 헬퍼 금지.
- `node_results`, `token_usage`, `cost_usd`, `duration_ms` 는 목록 응답에서
  제외 (단건 조회에만 포함). 이를 위해 `ExecutionDetailResponse` 를 따로
  두지 않고, 목록용 `ExecutionResponse` 에서 필드를 Optional 로 두고
  `model_config = ConfigDict(from_attributes=True)` 사용.

## 6. 서비스 로직 (`WorkflowService` 확장)

### execute_workflow(workflow_id, user)
한 함수 안에서 순차 진행:
1. `workflow_repo.get(workflow_id)` → 없거나 `owner_id != user.id` → `WorkflowNotFoundError` (404)
2. `workflow.is_active == False` → `WorkflowNotActiveError` (409)
3. `Execution(id=uuid4(), workflow_id=..., status="queued", execution_mode=workflow.settings.get("execution_mode", user.default_execution_mode))` DTO 생성
4. `execution_repo.create(execution)`
5. `# TODO(Execution_Engine): dispatch based on execution_mode`
6. return execution

**함수 증식 방지**: `_validate_ownership`, `_check_active`, `_create_execution` 같은
1회용 private 메서드 금지. 위 5단계는 본문에서 직선적으로 처리.

### get_execution(execution_id, user)
1. `execution_repo.get(execution_id)` → 없으면 404
2. `workflow_repo.get(execution.workflow_id)` → 소유권 확인 → 불일치 시 404
3. return execution

### list_executions(workflow_id, user, limit, cursor)
1. 소유권 확인 (workflow_repo.get → owner_id 비교)
2. `execution_repo.list_by_workflow(workflow_id, limit=limit, cursor=cursor)`
3. return executions

## 7. main.py 변경

```python
from auto_workflow_database.repositories.execution_repository import (
    PostgresExecutionRepository,
)

# lifespan 안에서:
execution_repo = PostgresExecutionRepository(sessionmaker)
app.state.execution_repo = execution_repo
app.state.workflow_service = WorkflowService(
    repo=workflow_repo, execution_repo=execution_repo, settings=s
)
```

`WorkflowService` 생성자에 `execution_repo` 추가. 기존 테스트의
`WorkflowService` 호출도 이에 맞춰 fixture 수정.

## 8. 함수 증식 방지 가드레일

- `WorkflowService` 에 메서드 3개 추가 (`execute_workflow`, `get_execution`,
  `list_executions`). 별도 `ExecutionService` 클래스 신설 금지 — 아직 메서드
  3개로 독립 클래스를 정당화할 수 없음.
- 라우터에서 cursor 파싱은 2줄 인라인. `_parse_cursor` / `_encode_cursor` 헬퍼 금지.
- 에러는 `DomainError` 하위 클래스 `raise`. `_raise_*` wrapper 금지 (PR #21 원칙).
- Pydantic 스키마는 `app/models/` 에 기존 파일 확장 또는 최소 1개 신규 파일.
  `schemas.py` / `request_models.py` / `response_models.py` 분리 금지.

## 9. 테스트

1. `test_execute_workflow_creates_queued_execution` — 202 + status=queued
2. `test_execute_workflow_not_owned_returns_404`
3. `test_execute_inactive_workflow_returns_409`
4. `test_get_execution_happy`
5. `test_get_execution_not_owned_returns_404`
6. `test_list_executions_returns_keyset_response`
7. `test_list_executions_cursor_pagination`
8. `test_list_executions_empty`

## 10. 수용 기준

- [ ] 신규 8 테스트 통과
- [ ] 기존 API_Server 34 테스트 회귀 없음 (총 42+)
- [ ] `POST /execute` 가 202 반환하고 DB 에 `queued` row 생성 확인
- [ ] `GET /executions/{id}` 가 소유권 검증 후 반환
- [ ] `GET /workflows/{id}/executions` 가 `{items, next_cursor, has_more}` 래퍼로 반환
- [ ] cursor 를 이어받아 2번째 페이지 조회 시 중복/누락 없음
- [ ] `WorkflowService` 에 1회용 private 헬퍼 0개
- [ ] 라우터에 `try/except` 0개 (DomainError 전역 핸들러로 위임)
- [ ] `# TODO(Execution_Engine): dispatch based on execution_mode` 주석 존재

## 11. 후속 영향

- **PLAN_04 (API_Server)** — `POST /workflows/{id}/activate` 와 `/deactivate`.
  본 PLAN 의 `WorkflowService` + `execution_repo` 주입 패턴을 재사용.
  APScheduler 워커가 본 PLAN 의 `execute_workflow` 를 내부 호출.
- **Execution_Engine 브랜치** — `# TODO` 주석 위치에 Celery task.delay() 또는
  AgentManager.dispatch() 를 연결하는 것이 첫 작업.
- **Frontend** — 대시보드에서 "Run" 버튼 → `POST /execute` → 202 → 폴링
  `GET /executions/{id}` 패턴. 목록은 무한 스크롤 + keyset cursor.

## 12. 선행 작업 (완료)

- [x] Database PLAN_06 (PR #25) — `created_at` 컬럼, keyset 인덱스, `list_by_workflow` 메서드
- [x] Database PLAN_07 (PR #22) — engine resilience + query logging
- [x] API_Server DBAPIError → 503 핸들러 (PR #24)

## 13. 작업 순서

1. PLAN_03 문서 (본 문서) ✓
2. main 브랜치 변경사항을 API_Server 에 반영 (PLAN_06/07 등)
3. Pydantic 스키마 추가
4. `app/errors.py` 에 `WorkflowNotActiveError` 추가
5. `WorkflowService` 메서드 3개 추가 + 생성자 `execution_repo` 파라미터
6. `app/routers/workflows.py` 에 `POST /{id}/execute` 추가
7. `app/routers/executions.py` 신규 — 단건 + 목록
8. `app/dependencies.py` + `app/main.py` 확장
9. `tests/test_executions.py` 작성
10. 로컬 테스트 통과 확인
11. PR 생성 → 리뷰 → 머지
