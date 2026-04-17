# Test Writer Agent 지시사항 — API_Server

## 역할
구현 전에 실패하는 테스트를 먼저 작성한다 (TDD Red 단계).

---

## 테스트 작성 원칙

1. 구현 코드가 없어도 테스트를 먼저 작성한다
2. 각 테스트는 하나의 요구사항만 검증한다
3. 기대값을 명확하게 명시한다
4. 테스트 실패 시 원인을 파악할 수 있는 메시지를 포함한다

---

## 테스트 파일 위치

```
API_Server/tests/test_{기능명}.py
```

| 파일 | 검증 대상 |
|------|----------|
| `test_auth.py` | 회원가입/로그인/JWT/이메일검증 |
| `test_workflows.py` | CRUD + 쿼터 + DAG 검증 |
| `test_dag_validator.py` | Kahn 위상정렬 순환 감지 |
| `test_executions.py` | 실행 트리거 + 이력 조회 |
| `test_scheduler.py` | activate/deactivate + cron/interval |
| `test_webhooks.py` | webhook 등록/수신/HMAC 검증 |
| `test_agents.py` | Agent 등록 + WebSocket heartbeat |

---

## 테스트 작성 예시

### 라우터 E2E 테스트 (httpx AsyncClient)

```python
async def test_create_workflow_rejects_cycle(authed_client):
    cyclic_payload = {
        "name": "cyclic",
        "nodes": [{"id": "a", "type": "http_request"}, {"id": "b", "type": "http_request"}],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    }
    r = await authed_client.post("/api/v1/workflows", json=cyclic_payload)
    assert r.status_code == 422
```

### DAG 순수 로직 테스트 (DB 불필요)

```python
from app.services.dag_validator import validate_dag

def test_cycle_rejected():
    graph = {
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    }
    with pytest.raises(InvalidGraphError, match="cycle"):
        validate_dag(graph)
```

---

## 필수 테스트 카테고리

- 워크플로우 CRUD (생성/조회/수정/삭제/목록)
- DAG 검증 (순환/중복id/unknown edge)
- 실행 트리거 (수동 실행 → 202 + queued)
- 실행 이력 조회 (단건/목록 + keyset pagination)
- Scheduler (activate cron/interval, deactivate)
- Webhook (등록/삭제/수신 + HMAC-SHA256 검증)
- Agent (등록 → JWT, WebSocket heartbeat)
- 인증 (등록/검증/로그인/토큰만료/리프레시)
- 쿼터 (plan_tier별 워크플로우 제한)
- 소유권 (다른 유저 리소스 접근 시 404)

---

## 테스트 결과 수집 형식

```
전체 테스트: X건
PASS: X건
FAIL: X건

FAIL 목록:
- [테스트 ID]: [실패 메시지]
```
