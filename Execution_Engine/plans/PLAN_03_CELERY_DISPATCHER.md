# PLAN_03 — Celery Dispatcher (Serverless Mode)

> 상태: DRAFT  
> 브랜치: `Execution_Engine`  
> 선행: PLAN_01 (NodeRegistry), PLAN_02 (DAG executor)

## 목적

`execution_mode=serverless` 워크플로우를 Celery + Redis 큐를 통해 비동기 실행.
API_Server가 `queued` Execution을 생성한 뒤 Celery 태스크를 enqueue → 
Execution_Engine 워커가 `run_workflow()`를 호출.

## 아키텍처

```
API_Server                          Execution_Engine
───────────                         ─────────────────
workflow_service                    Celery Worker (scripts/worker.py)
  .execute_workflow()                   │
       │                                │
       ├─ create Execution(queued)      │
       │                                │
       └─ celery.send_task(             │
            "execute_workflow",     ──►  run_workflow_task(execution_id)
            args=[execution_id]          │
          )                              ├─ load workflow graph (DB)
                                         ├─ run_workflow(graph, execution, repo, registry)
                                         └─ (status → success/failed by executor)
```

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/dispatcher/__init__.py` | 빈 패키지 |
| `src/dispatcher/serverless.py` | Celery app + `run_workflow_task` 태스크 |
| `scripts/worker.py` | `celery -A` 워커 진입점 |
| `config/celery_config.py` | 브로커/백엔드 URL, 직렬화 설정 |
| `tests/test_dispatcher.py` | 단위 테스트 (Celery eager mode) |

### 수정
| 파일 | 변경 |
|------|------|
| `pyproject.toml` | `celery[redis]` 의존성 추가 |

## 구현 상세

### 1. config/celery_config.py
```python
import os

broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
task_serializer = "json"
accept_content = ["json"]
task_acks_late = True
worker_prefetch_multiplier = 1
```

### 2. src/dispatcher/serverless.py

Celery 앱 생성 + 태스크 정의. 태스크는 sync 함수 내부에서 `asyncio.run()`으로
비동기 executor를 실행.

```python
celery_app = Celery("execution_engine")
celery_app.config_from_object("config.celery_config")

@celery_app.task(name="execute_workflow", bind=True, max_retries=0)
def run_workflow_task(self, execution_id: str):
    asyncio.run(_run(execution_id))

async def _run(execution_id: str):
    # 1. DB 세션으로 execution + workflow 조회
    # 2. registry에 등록된 노드로 run_workflow() 호출
    # 3. executor가 status 업데이트 담당 (success/failed)
```

### 3. scripts/worker.py
```python
from src.dispatcher.serverless import celery_app
celery_app.worker_main(["worker", "--loglevel=info", "--concurrency=4"])
```

### 4. API_Server 연동 (API_Server 브랜치에서 처리)
`workflow_service.py`의 TODO 자리에:
```python
if execution.execution_mode == "serverless":
    from celery import Celery
    broker = Celery(broker=settings.celery_broker_url)
    broker.send_task("execute_workflow", args=[str(execution.id)])
```
→ `send_task`는 태스크 정의 없이 브로커에 메시지만 보냄.
이 변경은 **별도 PR** (API_Server 브랜치).

## 테스트 전략

Celery eager mode (`task_always_eager=True`)로 동기 실행:
1. `test_task_runs_workflow_to_success` — 정상 그래프 → status=success
2. `test_task_handles_missing_execution` — 없는 execution_id → 에러 로깅, 예외 안 전파
3. `test_task_handles_node_failure` — 실패 노드 → status=failed

DB 의존성: InMemoryRepository 사용 (실 DB 불필요).

## 의존성 추가

```toml
dependencies = [
    "httpx>=0.27",
    "celery[redis]>=5.3",
    "auto-workflow-database",
]
```

## 체크리스트

- [ ] `config/celery_config.py` 작성
- [ ] `src/dispatcher/serverless.py` — Celery app + task
- [ ] `scripts/worker.py` — 워커 진입점
- [ ] `pyproject.toml` — celery[redis] 추가
- [ ] 테스트 3개 작성 + pass
- [ ] 커밋 → push → PR

## 후속 작업

- API_Server 브랜치: `send_task` 연동 (TODO 제거)
- PLAN_04: Agent 데몬 (execution_mode=agent)
