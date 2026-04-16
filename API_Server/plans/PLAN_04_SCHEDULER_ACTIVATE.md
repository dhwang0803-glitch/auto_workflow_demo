# PLAN_04 — Scheduler 워커 + Activate/Deactivate (API_Server)

> **브랜치**: `API_Server` · **작성일**: 2026-04-16 · **상태**: Draft
>
> PLAN_03 의 수동 실행 위에 **스케줄 기반 자동 실행**을 얹는다.
> APScheduler 를 별도 프로세스로 분리하여 멀티 워커 환경에서도
> 중복 실행 없이 안전하게 동작한다. API 프로세스는 job 등록/해제만
> 담당하고, 실제 발사는 Scheduler 워커가 수행한다.

## 1. 목표

1. `POST /workflows/{id}/activate` — cron/interval 트리거 등록
2. `POST /workflows/{id}/deactivate` — 트리거 해제
3. `GET /workflows/{id}` 응답에 `trigger_status` 필드 추가 (active/inactive)
4. 별도 Scheduler 워커 프로세스 (`python -m app.scheduler`)
5. `SQLAlchemyJobStore` 로 job 을 DB 에 영속 — 재시작 시 자동 복원

## 2. 범위

**In**
- `app/scheduler.py` 신규 — 독립 엔트리포인트, `AsyncIOScheduler` + `SQLAlchemyJobStore`
- `app/services/workflow_service.py` 확장 — `activate_workflow`, `deactivate_workflow`
- `app/routers/workflows.py` 확장 — activate/deactivate 엔드포인트
- `app/models/workflow.py` 확장 — `ActivateRequest` (trigger_type, cron/interval 설정)
- `app/config.py` 확장 — `scheduler_jobstore_url` (기본값: `database_url` 에서 async 제거)
- `migrations/` — `apscheduler_jobs` 테이블은 APScheduler 가 자동 생성 (`create_all`)
- `tests/test_scheduler.py` 신규

**Out**
- Webhook 트리거 — PLAN_05
- Agent WebSocket — PLAN_06
- 실제 Celery/Agent 디스패치 — Execution_Engine
- Job 실행 이력 대시보드 — Phase 2
- 동시 activate 방지 (distributed lock) — Phase 2 (현재는 DB unique constraint 로 충분)

## 3. 아키텍처

```
┌──────────────┐     add_job / remove_job      ┌─────────────────────┐
│  API_Server  │ ──────────────────────────────▶│  apscheduler_jobs   │
│  (FastAPI)   │     (SQLAlchemyJobStore)       │  (PostgreSQL 테이블) │
└──────────────┘                                └──────────┬──────────┘
                                                           │ poll
                                                ┌──────────▼──────────┐
                                                │  Scheduler Worker   │
                                                │  (python -m         │
                                                │   app.scheduler)    │
                                                └──────────┬──────────┘
                                                           │ 직접 호출
                                                ┌──────────▼──────────┐
                                                │ WorkflowService     │
                                                │ .execute_workflow() │
                                                └─────────────────────┘
```

- API 와 Scheduler 는 **같은 DB** 를 공유하지만 **다른 프로세스**
- Scheduler 워커가 `execute_workflow` 를 직접 호출 (HTTP self-request 아님)
- 멀티 API 워커가 `add_job` 을 해도 Scheduler 워커는 1대라서 중복 발사 없음

## 4. 엔드포인트

| 메서드 | 경로 | 설명 | 응답 |
|--------|------|------|------|
| `POST` | `/api/v1/workflows/{id}/activate` | 트리거 등록 | 200 `WorkflowResponse` |
| `POST` | `/api/v1/workflows/{id}/deactivate` | 트리거 해제 | 200 `WorkflowResponse` |

### ActivateRequest body

```python
class ActivateRequest(BaseModel):
    trigger_type: Literal["cron", "interval"]
    cron: str | None = None          # "0 9 * * MON-FRI"
    interval_seconds: int | None = None  # 300
```

**에러 코드**:

| 상황 | HTTP |
|------|------|
| 워크플로우 미존재 / 소유권 없음 | 404 |
| 비활성 워크플로우 | 409 |
| 이미 활성화된 상태에서 재활성화 | 200 (멱등, job 교체) |
| trigger_type=cron 인데 cron 필드 없음 | 422 |
| 유효하지 않은 cron 표현식 | 422 |

## 5. 서비스 로직

### activate_workflow(user, workflow_id, trigger)
1. 소유권 + is_active 확인 (기존 패턴)
2. `workflow.settings["trigger"] = trigger.model_dump()` 저장
3. APScheduler jobstore 에 `add_job` (job_id = `str(workflow_id)`, replace_existing=True)
   - trigger_type=cron → `CronTrigger.from_crontab(trigger.cron)`
   - trigger_type=interval → `IntervalTrigger(seconds=trigger.interval_seconds)`
   - func = `_execute_scheduled` (workflow_id, owner_id 인자 바인딩)
4. return workflow

### deactivate_workflow(user, workflow_id)
1. 소유권 + is_active 확인
2. jobstore 에서 `remove_job(str(workflow_id))` — 없으면 무시 (멱등)
3. `workflow.settings.pop("trigger", None)` 저장
4. return workflow

### _execute_scheduled(workflow_id, owner_id) — Scheduler 워커에서 호출
1. `user = await user_repo.get(owner_id)`
2. `await workflow_service.execute_workflow(user, workflow_id)`
3. 실패 시 로깅만 (job 자체를 제거하지 않음 — 다음 스케줄에서 재시도)

## 6. Scheduler 워커 (`app/scheduler.py`)

```python
"""Scheduler worker — run as: python -m app.scheduler"""
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

async def main():
    engine = build_engine()
    sm = build_sessionmaker(engine)
    # 서비스 레이어 조립 (API lifespan 과 동일 패턴)
    user_repo = PostgresUserRepository(sm)
    workflow_repo = PostgresWorkflowRepository(sm)
    execution_repo = PostgresExecutionRepository(sm)
    svc = WorkflowService(repo=workflow_repo, execution_repo=execution_repo, settings=Settings())

    jobstore = SQLAlchemyJobStore(url=Settings().scheduler_jobstore_url)
    scheduler = AsyncIOScheduler(jobstores={"default": jobstore})
    scheduler.start()

    # 무한 대기 — Ctrl+C 로 종료
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
```

**함수 증식 방지**: `main()` 한 함수에서 조립 + 시작 + 대기 + 종료.
`_setup_repos`, `_configure_scheduler` 같은 헬퍼 금지.

## 7. API 쪽 JobStore 접근

API 프로세스에서 `add_job` / `remove_job` 을 하려면 **Scheduler 인스턴스
없이 JobStore 에 직접 접근**해야 합니다. 두 가지 방법:

**방법 1**: API lifespan 에서도 `AsyncIOScheduler` 를 생성하되 `start()` 안 함
- `scheduler.add_job(...)` → jobstore 에 INSERT
- Scheduler 워커가 poll 해서 발사
- 장점: APScheduler API 그대로 사용
- 단점: API 프로세스마다 Scheduler 인스턴스 생성 (but start 안 하므로 가벼움)

**방법 2**: `SQLAlchemyJobStore` 에 직접 `add_job`
- APScheduler 내부 API 의존, 버전 변경 시 깨질 수 있음

→ **방법 1 채택**. `app.state.scheduler` 에 미시작 Scheduler 저장,
서비스가 이를 통해 job CRUD.

## 8. 함수 증식 방지 가드레일

- `app/scheduler.py` 는 **단일 파일, `main()` 한 함수** — 별도 모듈/클래스 금지
- `WorkflowService` 에 메서드 2개 추가 (`activate_workflow`, `deactivate_workflow`)
- `SchedulerManager` / `TriggerService` 같은 새 클래스 금지
- cron 파싱은 `CronTrigger.from_crontab()` 한 줄 — 래퍼 금지
- 에러는 기존 `DomainError` 하위 클래스 재사용 (`NotFoundError`, `WorkflowNotActiveError`, `InvalidGraphError` 로 cron 검증 실패 처리)

## 9. 테스트

1. `test_activate_cron_happy` — activate 후 settings 에 trigger 저장 확인
2. `test_activate_interval_happy`
3. `test_activate_not_owned_404`
4. `test_activate_inactive_409`
5. `test_activate_invalid_cron_422`
6. `test_deactivate_happy` — trigger 제거 확인
7. `test_deactivate_already_inactive_is_idempotent`
8. `test_activate_replaces_existing_trigger` — 재활성화 시 job 교체

**Scheduler 워커 통합 테스트는 Phase 2** — 현재는 API 측 job 등록/해제 +
settings 반영만 검증. 워커가 실제로 `execute_workflow` 를 발사하는 E2E 는
Execution_Engine 연동 후 구성.

## 10. 수용 기준

- [ ] 신규 8 테스트 통과
- [ ] 기존 42 테스트 회귀 없음 (총 50+)
- [ ] `POST /activate` 가 jobstore 에 row 생성 확인
- [ ] `POST /deactivate` 가 jobstore 에서 row 제거 확인
- [ ] `python -m app.scheduler` 로 워커 기동 가능 (수동 확인)
- [ ] 워커 기동 후 cron job 이 실제로 발사되어 execution row 생성 (수동 확인)
- [ ] `WorkflowService` 에 1회용 private 헬퍼 0개
- [ ] `app/scheduler.py` 단일 파일, 50줄 이내

## 11. 후속 영향

- **PLAN_05 (Webhook)** — Webhook 트리거도 activate 패턴 재사용 가능
  (`trigger_type: "webhook"` 추가 시 activate 가 WebhookRegistry 에 등록)
- **Execution_Engine** — Scheduler 워커의 `execute_workflow` 호출이
  실제 Celery task.delay() 로 연결되는 시점에 E2E 완성
- **Frontend** — 워크플로우 설정 패널에 "Schedule" 탭 추가,
  cron/interval 입력 → `POST /activate` 호출

## 12. 의존성

- `apscheduler>=3.10` — pyproject.toml 에 추가 필요
- `SQLAlchemyJobStore` 는 동기 SQLAlchemy 엔진 사용 (`database_url` 에서 `+asyncpg` 제거)
- `apscheduler_jobs` 테이블은 APScheduler 가 자동 생성 (DDL 불필요)

## 13. 작업 순서

1. PLAN_04 문서 (본 문서) ✓
2. `pyproject.toml` 에 `apscheduler` 의존성 추가
3. `app/config.py` 에 `scheduler_jobstore_url` 추가
4. `app/models/workflow.py` 에 `ActivateRequest` 추가
5. `app/services/workflow_service.py` 에 scheduler 주입 + activate/deactivate
6. `app/routers/workflows.py` 에 엔드포인트 추가
7. `app/main.py` 에 미시작 Scheduler 인스턴스 lifespan 주입
8. `app/scheduler.py` 신규 — 독립 워커 엔트리포인트
9. `tests/test_scheduler.py` 작성
10. 로컬 테스트 통과 확인
11. 워커 수동 기동 + cron 발사 수동 확인
12. PR 생성 → 리뷰 → 머지
