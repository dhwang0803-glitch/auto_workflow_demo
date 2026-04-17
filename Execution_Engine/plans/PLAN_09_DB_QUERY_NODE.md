# PLAN_09 — DBQueryNode (Postgres via asyncpg)

> 선행: PLAN_08 (credential resolution) — `postgres_dsn` credential_type 은
> 청사진 §1.2 에서 본 노드용으로 예정됐음.

## 목적

워크플로우에서 고객의 Postgres DB 에 SQL 쿼리 실행. 파라미터 바인딩 (`$1, $2`)
기반 — SQL 인젝션 1차 방어. 자격증명은 credential_ref 를 통해 config 에 평문
DSN 이 주입되어 있다는 전제 (PLAN_08 Worker 가 처리).

## 스코프

- **지원 DB**: Postgres 만 (asyncpg). MySQL/SQLite 별도 노드 타입으로 후속.
- **허용 SQL**: 모든 문 (SELECT/INSERT/UPDATE/DELETE/DDL) — BYO 모델, 고객 credential = 고객 책임.
- **파라미터 바인딩 강제**: asyncpg 는 `$N` 플레이스홀더만 지원 → 자연히 string interpolation 불가.
- **타겟 세그먼트**: **Middle** (Supabase / Neon / RDS public endpoint 등 인터넷 reachable managed Postgres 를 등록한 사용자). Heavy (VPC 내부 DB) 는 Agent credential follow-up 이후.

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/nodes/db_query.py` | DBQueryNode — asyncpg.connect + fetch/execute |
| `tests/test_db_query_node.py` | AsyncMock 기반 단위 테스트 |

### 수정
| 파일 | 변경 |
|------|------|
| `pyproject.toml` | `asyncpg>=0.29` 직접 의존성 추가 (현재는 auto-workflow-database 경유 transitive) |

## 구현 상세

### DBQueryNode (`src/nodes/db_query.py`)

```python
class DBQueryNode(BaseNode):
    node_type = "db_query"

    async def execute(self, input_data, config):
        url = config["connection_url"]
        query = config["query"]
        params = config.get("parameters", [])
        timeout = config.get("timeout_seconds", 30)

        conn = await asyncio.wait_for(
            asyncpg.connect(dsn=url, timeout=timeout),
            timeout=timeout,
        )
        try:
            # fetch() returns list[Record] for any statement that produces rows
            # (SELECT, INSERT/UPDATE/DELETE ... RETURNING). Otherwise execute().
            stripped = query.lstrip().lower()
            returns_rows = stripped.startswith(("select", "with")) or "returning" in stripped
            if returns_rows:
                rows = await asyncio.wait_for(
                    conn.fetch(query, *params), timeout=timeout
                )
                return {
                    "rows": [dict(r) for r in rows],
                    "row_count": len(rows),
                }
            else:
                status = await asyncio.wait_for(
                    conn.execute(query, *params), timeout=timeout
                )
                # status is like "UPDATE 3" / "DELETE 5" — parse last token.
                affected = int(status.rsplit(" ", 1)[-1]) if status else 0
                return {"rows": [], "row_count": affected}
        finally:
            await conn.close()
```

**설계 선택:**
- **connection-per-call**: 노드 호출마다 연결 열고 닫음. 풀링 안 함 — 노드 인스턴스는 stateless
  (registry 설명), 풀을 share 하면 stateful 이 됨. 트래픽 적은 워크플로우 용도에 적합.
- **returns_rows 휴리스틱**: SELECT / WITH / RETURNING → `fetch()`, 그 외 → `execute()`.
  오류 시 fetch 도 DDL 에 작동은 하나 affected count 가 없어 불편. 분기로 API 명확하게.
- **dict 변환**: asyncpg.Record 는 JSON 직렬화 불가 → `dict(r)` 로 변환. executor 가
  `append_node_result` 에 저장할 때 JSONB 로 들어감.
- **타임아웃 두 번**: connect 시 한 번, query 시 한 번. asyncio.wait_for 가 외곽 경호.

## 보안 불변식

- DSN 은 credential_ref 경유 주입 전제 → 그래프 JSON 에 평문 DSN 직접 넣지 않음 (청사진 §1.6 불변식 2 재확인)
- 쿼리 문자열에 파라미터 interpolation 하지 않을 것 — `$1 $2` 만 사용 (asyncpg 강제)
- 에러 메시지는 asyncpg 의 exception 이 그대로 올라감. asyncpg 가 에러에 DSN 을 포함하진 않으나
  `SyntaxError: syntax error at or near "FOO"` 류는 쿼리 fragment 를 노출할 수 있음.
  **executor 단에서 에러 메시지 정제는 별도 정책 필요** — 현 노드 범위에서는 그대로 전파.

## 테스트 전략 (AsyncMock 기반, DB 불필요)

`asyncpg.connect` 를 monkeypatch 로 AsyncMock 교체 → conn.fetch / conn.execute 호출 인자 / 반환값 검증.

### test_db_query_node.py (5 tests)
1. `test_select_returns_rows` — `SELECT` → fetch 호출, rows dict 변환, row_count 정확
2. `test_insert_returns_affected_count` — `INSERT` → execute 호출, status `"INSERT 0 3"` → row_count=3
3. `test_parameters_passed_through` — `SELECT ... WHERE id = $1` + params=[42] → conn.fetch(query, 42) 확인
4. `test_returning_clause_uses_fetch` — `INSERT ... RETURNING id` → fetch 경로
5. `test_connection_always_closed` — 쿼리 실패해도 conn.close 호출됨 (finally)

## 의존성 추가

```toml
dependencies = [
    "httpx>=0.27",
    "celery[redis]>=5.3",
    "websockets>=12.0",
    "RestrictedPython>=7.0",
    "aiosmtplib>=3.0",
    "asyncpg>=0.29",
    "auto-workflow-database",
]
```

## 체크리스트

- [ ] `src/nodes/db_query.py` — DBQueryNode + registry 등록
- [ ] `pyproject.toml` — asyncpg 명시 추가
- [ ] 테스트 5 pass, 전체 49→54
- [ ] 커밋 → push → PR

## Out of scope

- MySQL / SQLite 별도 노드 — 후속
- Heavy 유저 (Agent 모드) — Agent credential follow-up 이후
- 연결 풀링 — 현재 per-call. 쿼리 빈도 높은 워크플로우가 문제되면 후속 (WorkerContainer 가 풀 보유)
- 쿼리 결과 row cap — MVP 에서 생략, 고객 timeout 으로 보호
- 에러 메시지에서 쿼리 fragment 정제 — executor 계층 정책
