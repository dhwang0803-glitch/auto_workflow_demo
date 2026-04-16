# PLAN_07 — DB Resilience & Observability (Database)

> **브랜치**: `Database` · **작성일**: 2026-04-16 · **상태**: Draft
>
> Database 의 `_session.py` 가 현재 `pool_pre_ping=True` 외 방어/관측
> 설정이 전무하다. 느린 쿼리가 커넥션을 점유하면 기본 pool_size=5 가
> 막히고 `pool_timeout=30s` 동안 FastAPI 워커가 블록되어 병목 경합이
> 발생한다. 또한 Repository 계층에 로깅이 0 건이라 실패/지연 쿼리를
> 운영 시 식별할 수 없다. 본 PLAN 은 이 **횡단 방어막** 을 엔진 레이어
> 단 한 곳에서 해결한다. Repository 코드는 일절 건드리지 않는다.

## 1. 목표

1. DB 커넥션 풀/타임아웃 기본값을 명시적으로 박고, 환경변수로 override 가능
2. PostgreSQL `statement_timeout` 서버 사이드 컷오프 적용 (좀비 쿼리 방지)
3. 느린/실패 쿼리를 **SQLAlchemy 이벤트 리스너 단일 지점**에서 로깅
4. `OperationalError` / `DBAPIError` 를 API_Server 상위 핸들러에서 503 으로 매핑
5. **Repository 파일은 한 줄도 수정하지 않는다** — 함수 증식 방지의 근간

## 2. 범위

**In**
- `auto_workflow_database/repositories/_session.py` 확장 (~40 줄까지)
- `auto_workflow_database/__init__.py` 또는 `_session.py` 에 모듈 logger 1개
- `tests/test_session_resilience.py` (신규) — 3 케이스
- API_Server 브랜치 측 변경 (수용 기준에만 포함, 본 PLAN 머지 후 별도 PR):
  - `OperationalError` / `DBAPIError` 전용 FastAPI 예외 핸들러 1개 추가 → 503

**Out**
- Repository 구현체 수정 — 금지
- Retry / circuit breaker 로직 — 필요 시 별도 PLAN
- 모니터링/메트릭 수집 (Prometheus exporter 등) — Phase 2
- 쿼리 플랜 분석 / `EXPLAIN` 통합 — 별도 PLAN
- 마이그레이션 파일 추가 — 본 PLAN 은 스키마 변경 없음

## 3. 엔진 설정 변경 (`_session.py`)

```python
DEFAULT_POOL_SIZE = 10
DEFAULT_MAX_OVERFLOW = 10
DEFAULT_POOL_TIMEOUT_S = 30      # 기본값 유지, env 로 override
DEFAULT_POOL_RECYCLE_S = 1800    # 30분
DEFAULT_STATEMENT_TIMEOUT_MS = 5000  # 기본값 유지, env 로 override


def build_engine(dsn: str | None = None) -> AsyncEngine:
    dsn = dsn or os.environ["DATABASE_URL"]
    engine = create_async_engine(
        dsn,
        future=True,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", DEFAULT_POOL_SIZE)),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", DEFAULT_MAX_OVERFLOW)),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT_S", DEFAULT_POOL_TIMEOUT_S)),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE_S", DEFAULT_POOL_RECYCLE_S)),
        connect_args={
            "server_settings": {
                "statement_timeout": os.getenv(
                    "DB_STATEMENT_TIMEOUT_MS", str(DEFAULT_STATEMENT_TIMEOUT_MS)
                ),
            }
        },
    )
    _install_query_logging(engine)
    return engine
```

**기본값 방침**: SQLAlchemy 기본 (`pool_timeout=30s`) 과 제안 기본값
(`statement_timeout=5000ms`) 을 **그대로** 사용. 실제 튜닝은 DB 가 설치될
컴퓨터 스펙 확정 후 env 로 조정한다 (§9 운영 튜닝 노트 참조).

**환경변수**:

| 변수 | 기본값 | 용도 |
|---|---|---|
| `DB_POOL_SIZE` | 10 | 기본 풀 크기 |
| `DB_MAX_OVERFLOW` | 10 | 초과 허용 |
| `DB_POOL_TIMEOUT_S` | 30 | 풀 고갈 시 대기 상한 |
| `DB_POOL_RECYCLE_S` | 1800 | 커넥션 재생성 주기 |
| `DB_STATEMENT_TIMEOUT_MS` | 5000 | Postgres 서버 사이드 컷오프 |

## 4. 로깅 이벤트 리스너 (`_session.py` 내부)

**모듈 logger**: `logger = logging.getLogger("auto_workflow_database")`

`_install_query_logging(engine)` 는 `build_engine` 내부에서 호출되며,
SQLAlchemy 의 동기 엔진 이벤트 API 를 `engine.sync_engine` 에 등록한다.

```python
SLOW_QUERY_MS = int(os.getenv("DB_SLOW_QUERY_MS", "1000"))


def _install_query_logging(engine: AsyncEngine) -> None:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):
        context._query_start = time.monotonic()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, params, context, executemany):
        elapsed_ms = int((time.monotonic() - context._query_start) * 1000)
        if elapsed_ms >= SLOW_QUERY_MS:
            logger.warning(
                "slow query %dms: %s", elapsed_ms, statement[:200]
            )

    @event.listens_for(engine.sync_engine, "handle_error")
    def _on_error(ctx):
        stmt = (ctx.statement or "")[:200]
        logger.error(
            "db error %s on: %s",
            type(ctx.original_exception).__name__,
            stmt,
            exc_info=ctx.original_exception,
        )
```

**중요**: 세 리스너 콜백은 모두 `_install_query_logging` 안에 inline
정의. 별도 파일·클래스·EngineEventHandler 같은 추상화 금지. Repository
측에는 어떤 데코레이터도 주입하지 않는다.

## 5. API_Server 상위 매핑

**본 PLAN 머지 후 별도 PR** (API_Server 브랜치) 로:

```python
# API_Server/app/main.py 또는 error_handlers.py
from sqlalchemy.exc import DBAPIError, OperationalError

@app.exception_handler(DBAPIError)
async def _db_error_handler(request, exc: DBAPIError):
    # Database 레이어 로그에 이미 상세 기록됨. 여기선 최소한의 요약만.
    api_logger.error("db error reached router: %s", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"error": "database_unavailable"},
    )
```

- `OperationalError` 는 `DBAPIError` 의 서브클래스라 한 핸들러로 수용
- 라우터에 try/except 추가 금지 — DomainError 와 동일한 "전역 핸들러만"
  패턴 유지 (PR #21 원칙)
- 핸들러 함수 1개만 추가, 헬퍼 분리 금지

## 6. 함수 증식 방지 가드레일

이 PLAN 은 **함수 증식 금지 원칙의 모범 사례**가 되어야 한다.

- Repository 파일 (`workflow_repository.py`, `execution_repository.py`,
  `user_repository.py`, `credential_store.py`, 기타) 은 **수정 금지**.
  git diff 에 `repositories/*.py` 가 _session.py 외엔 등장하면 안 됨.
- `_session.py` 전체 줄 수는 **70 줄 이하** 유지 (현재 25 줄 → 약 65 줄 예상).
  이벤트 리스너 3개 inline 정의가 본 PLAN 의 핵심이라 초기 추정 40 줄로는
  불가. 본질은 "Repository 미수정 + 헬퍼/모듈 분리 금지" 이며 줄 수는 그 proxy.
- 설정값을 묶는 `EngineConfig` / `ResilienceSettings` dataclass 금지.
  `build_engine` 함수 인자 + env var 로 충분.
- 이벤트 리스너 콜백을 `query_logging.py` 같은 별도 모듈로 분리 금지.
- `_slow_query_logger`, `_error_logger` 같은 얇은 wrapper 금지 — 리스너
  콜백 자체가 이미 충분히 작다.
- API_Server 측 예외 핸들러도 함수 1개만. `_format_db_error`,
  `_log_and_return_503` 같은 헬퍼 금지.

**위반 시 리뷰에서 즉시 반려** — 이 원칙이 깨지면 본 PLAN 의 존재 이유가
사라진다.

## 7. 테스트 (`tests/test_session_resilience.py`)

Postgres testcontainer 기반. 3 케이스면 충분.

1. **`test_slow_query_logs_warning`**
   - `SELECT pg_sleep(1.5)` 실행
   - `caplog` 로 `"slow query"` 경고 포착 확인
   - `DB_SLOW_QUERY_MS=1000` 기본값에서 1500ms 초과하므로 기록돼야 함

2. **`test_statement_timeout_aborts_long_query`**
   - `DB_STATEMENT_TIMEOUT_MS=500` env 로 override 후 엔진 재생성
   - `SELECT pg_sleep(2)` → `asyncpg.QueryCanceledError` 발생 확인
   - 리스너가 error 로그도 남겼는지 확인

3. **`test_pool_timeout_raises_and_releases`**
   - `DB_POOL_SIZE=2`, `DB_POOL_TIMEOUT_S=1` override
   - 커넥션 2개를 의도적으로 잡아두고 3번째 요청이 `TimeoutError` 로
     빠르게 실패하는지 확인
   - 잡아둔 커넥션 release 후 풀이 정상 복구되는지 확인

**API_Server 통합 테스트는 §5 PR 에서 추가** — 본 PLAN 스코프 밖.

## 8. 수용 기준

- [ ] `_session.py` 변경 후 Database 기존 28 테스트 전부 통과 (회귀 없음)
- [ ] 신규 3 테스트 통과
- [ ] Repository 파일 git diff 0 줄 (= `_session.py` + 테스트 + 본 PLAN
      문서 외에는 수정 없음)
- [ ] `_session.py` ≤ 70 줄
- [ ] `logging.getLogger("auto_workflow_database")` 가 발생시키는
      slow/error 로그가 `caplog` 로 포착됨
- [ ] `statement_timeout` 이 Postgres 세션에 실제로 적용됨을 `SHOW
      statement_timeout` 로 검증하는 케이스 포함
- [ ] (별도 PR) API_Server 에 `DBAPIError` → 503 핸들러 추가 + 통합 테스트

## 9. 후속 영향 / 운영 튜닝 노트

**본 PLAN 의 기본값은 "안전한 출발점"** 이며 운영 DB 확정 후 재조정한다.

### 9.1 `pool_size` / `max_overflow` 의 의미 — 프로세스당이다

**흔한 오해**: "`pool_size=10` 은 유저 10명까지만 감당한다는 뜻이다"
→ **틀림**. `pool_size` 는 **한 Python 프로세스가 동시에 점유 가능한
DB 커넥션의 최대 개수** 이며, 유저 수·요청 수와 무관하다.

유저 1명의 `GET /workflows` 요청은 DB 쿼리가 실행되는 수 ms~수십 ms
동안만 커넥션 1개를 빌려쓰고 응답 직후 풀로 반납한다. 따라서
`pool_size=10, max_overflow=10` (= 프로세스당 20 커넥션) 이면 쿼리 평균
시간 10ms 기준 **프로세스당 초당 약 2000 요청** 까지 수용 가능하다.

### 9.2 진짜 위험은 "프로세스 곱셈"

실제로 주의해야 할 것은 **총 커넥션 수가 Postgres `max_connections`
한도를 초과하는 것**이다. 총 커넥션은 다음과 같이 곱셈으로 늘어난다:

```
총 커넥션 상한
  = (인스턴스 수)
    × (인스턴스당 프로세스/워커 수)
    × (pool_size + max_overflow)
  + (Scheduler 워커 프로세스)
  + (Execution_Engine 워커 프로세스)
```

예시 — API_Server 1 인스턴스, gunicorn `-w 4`, 기본값 (10/10):

| 구성 요소 | 커넥션 |
|---|---|
| API_Server 4 워커 × 20 | 80 |
| Scheduler 1 워커 × 20 | 20 |
| Execution_Engine N 워커 × 20 | N × 20 |
| **합계** | **100 + N × 20** |

Postgres 기본 `max_connections=100` 이면 **API_Server + Scheduler 만으로도
이미 한도 턱까지 차버림**. Execution_Engine 이 돌면 즉시 초과. 이 경우
해결책은 "풀을 늘린다" 가 아니라 **풀을 줄이는 것**이다.

### 9.3 튜닝 산정식

```
pool_size ≈ (Postgres max_connections × 0.5)
            / (인스턴스 수 × 워커 수)
            − max_overflow
```

나머지 값들의 기준점:

- `DB_STATEMENT_TIMEOUT_MS`: p99 쿼리 시간 × 3
- `DB_POOL_TIMEOUT_S`: API_Server 요청 타임아웃 − 1 초
- `DB_SLOW_QUERY_MS`: p95 쿼리 시간 + 여유

**경험칙**: 워커 수를 늘리면 `pool_size` 는 오히려 **낮춰야** 한다.
"트래픽이 느니까 풀을 키우자" 는 직관은 대개 틀리며, 대부분의 경우
Postgres 쪽이 먼저 거부한다. 쿼리가 느려서 풀이 마른다면 풀을 키우기
전에 `statement_timeout` 과 slow query 로그를 먼저 확인해 원인을
제거한다.

튜닝 시에는 **코드 변경 없이 env var 로만** 조정 가능하다는 것이 본
PLAN 의 설계 의도. 재배포 대신 설정 변경 → 재시작으로 족하다.

**후속 PLAN 후보** (본 PLAN 이후 필요 시):
- DB metrics export (Prometheus)
- Read replica 라우팅
- Retry 데코레이터 (transient error 대상, 단 idempotent 쿼리만)

이들은 본 PLAN 이 제공하는 로깅 기반 위에서 "진짜 필요한지" 판단 후 착수.

## 10. 작업 순서

1. PLAN_07 문서 작성 (본 문서)
2. `_session.py` 수정 + 이벤트 리스너 추가
3. `tests/test_session_resilience.py` 작성
4. 로컬 Postgres testcontainer 로 3 케이스 통과 확인
5. 기존 28 테스트 회귀 확인
6. PR 작성 → 리뷰 → 머지
7. → PLAN_06 (Execution 목록 지원) 착수
8. → API_Server 브랜치 복귀 + `DBAPIError` 503 핸들러 PR
9. → API_Server PLAN_03 문서 초안
