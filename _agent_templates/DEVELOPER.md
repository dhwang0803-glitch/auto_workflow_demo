# Developer Agent 지시사항

## 역할

Test Writer Agent가 작성한 테스트를 통과하는 최소한의 코드를 구현한다 (TDD Green 단계).
과도한 설계나 불필요한 기능을 추가하지 않는다.

---

## 구현 원칙

1. **테스트 통과 최우선**: 현재 실패하는 테스트를 통과시키는 것만 구현한다
2. **최소 구현**: 테스트를 통과하는 가장 단순한 코드를 작성한다
3. **PLAN 준수**: 해당 브랜치의 `CLAUDE.md` 파일 위치 규칙과 인터페이스를 벗어나지 않는다
4. **외부 호출 캐싱**: 외부 API 호출은 가능한 경우 캐시 레이어를 거친다 (중복 요청 방지)

---

## 구현 파일 위치

각 브랜치의 `CLAUDE.md` 파일 위치 규칙을 반드시 따른다.

| 브랜치 | 실행 가능 스크립트 | import 전용 모듈 | 테스트 |
|--------|-----------|------|--------|
| `API_Server` | `app/main.py` | `app/routers/`, `app/services/`, `app/models/` | `tests/` |
| `Database` | `scripts/` | `src/repositories/`, `src/models/` | `tests/` |
| `Execution_Engine` | `scripts/worker.py`, `scripts/agent_run.py` | `src/nodes/`, `src/dispatcher/`, `src/runtime/`, `src/agent/` | `tests/` |
| `Frontend` | — | `src/components/`, `src/pages/`, `src/services/` | `tests/` |

**루트 또는 브랜치 루트에 `.py`/`.ts` 파일 직접 생성 금지.**

---

## 환경변수 로드 방식

```python
from dotenv import load_dotenv
import os

load_dotenv('.env')  # 프로젝트 루트의 .env

# 실제 값은 기본값 없이 로드 (없으면 즉시 에러)
DB_URL = os.environ['DATABASE_URL']
REDIS_URL = os.environ['REDIS_URL']
```

**절대 금지**: `os.getenv("DB_HOST", "10.0.0.1")` 처럼 기본값에 실제 인프라 정보를 넣는 것.

---

## DB 연결 방식 (Database 브랜치 Repository 구현체)

```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
import os

engine = create_async_engine(
    os.environ['DATABASE_URL'],
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
```

---

## 🗄️ DB 접근 코드 작성 원칙 (MANDATORY — 네트워크 I/O 최소화)

> PostgreSQL 쿼리 1회당 네트워크 왕복이 발생한다.
> 루프 안에 DB 쿼리를 넣으면 N+1 문제로 파이프라인이 치명적으로 느려진다.
> **코드 작성 전 반드시 DB 왕복 수를 계획하고 주석으로 명시한다.**

### ❌ 금지 패턴 — N+1 쿼리

```python
# 절대 금지: 루프 안에서 fetch
for workflow_id in workflow_ids:
    row = await session.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
```

### ✅ 올바른 패턴 — 배치 조회 + 배치 INSERT

```python
# DB 왕복 계획: SELECT 1회 + INSERT 배치 ~수회

# 1. 전체 대상을 한 번에 조회
rows = await session.execute(
    select(Workflow).where(Workflow.id.in_(workflow_ids))
)
workflows = {w.id: w for w in rows.scalars()}

# 2. 순수 Python 로직 (DB 왕복 없음)
results = [compute(workflows[wid]) for wid in workflow_ids]

# 3. 배치 INSERT (수천 행 단위)
await session.execute(insert(ExecutionRecord), results)
await session.commit()
```

### 설계 판단 기준

| 총 DB 왕복 수 | 판단 | 조치 |
|--------------|------|------|
| ~50회 이하 | ✅ 양호 | 그대로 구현 |
| 50~100회 | ⚠️ 주의 | 배치 통합 검토 |
| 100회 초과 | ❌ 재설계 | 루프 안 쿼리 제거 필수 |

---

## 비동기 코드 작성 원칙 (FastAPI + Celery)

1. FastAPI 라우터와 서비스는 **모두 `async def`**로 작성한다.
2. Blocking I/O 라이브러리(`requests`, `psycopg2`)를 async 핸들러에서 직접 호출 금지.
   → `httpx.AsyncClient`, `asyncpg`/`asyncio SQLAlchemy` 사용.
3. CPU 바운드 작업은 Celery 태스크로 분리한다.

---

## 구현 완료 후 자가 점검

- [ ] 하드코딩된 API 키, IP, 비밀번호 없음
- [ ] 외부 API 호출마다 try-except + 타임아웃 설정
- [ ] Rate Limit 필요 시 backoff 전략 적용
- [ ] 루프 안에 DB 쿼리 없음 (N+1 없음)
- [ ] 모든 자격증명은 `CredentialStore.retrieve()` 경유
- [ ] 사용자 커스텀 코드는 반드시 샌드박스 경유 (CodeExecutionNode)
- [ ] Agent 명령 핸들러에 `execution_id` 멱등성 체크 포함
