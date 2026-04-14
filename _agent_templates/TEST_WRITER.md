# Test Writer Agent 지시사항

## 역할

구현 전에 실패하는 테스트를 먼저 작성한다 (TDD Red 단계).
구현 후에는 테스트를 실행하고 결과를 수집한다 (검증 단계).

---

## 테스트 작성 원칙

1. 구현 코드가 없어도 테스트를 먼저 작성한다
2. 각 테스트는 하나의 요구사항만 검증한다
3. 기대값을 명확하게 명시한다
4. 테스트 실패 시 원인을 파악할 수 있는 메시지를 포함한다
5. 외부 API/네트워크 의존 테스트는 실제 호출과 Mock 모드를 구분한다

---

## 브랜치별 테스트 파일 위치

| 브랜치 | 테스트 디렉토리 | 형식 |
|--------|--------------|------|
| `API_Server` | `API_Server/tests/` | pytest + httpx TestClient |
| `Database` | `Database/tests/` | pytest + 실제 DB 연결 (테스트 DB) |
| `Execution_Engine` | `Execution_Engine/tests/` | pytest + Celery eager mode |
| `Frontend` | `Frontend/tests/` | Jest + Playwright |

---

## 테스트 작성 예시 (pytest)

### API_Server 라우터 테스트

```python
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_create_workflow_rejects_cycle():
    """순환 참조가 있는 워크플로우는 400을 반환한다"""
    cyclic_payload = {
        "name": "cyclic",
        "nodes": [{"node_id": "a"}, {"node_id": "b"}],
        "connections": [
            {"source_node_id": "a", "target_node_id": "b"},
            {"source_node_id": "b", "target_node_id": "a"},
        ],
    }
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.post("/api/v1/workflows", json=cyclic_payload)
    assert r.status_code == 400
    assert "순환" in r.json()["detail"]
```

### Execution_Engine 노드 테스트

```python
import pytest
from src.nodes.condition import ConditionNode

@pytest.mark.asyncio
async def test_condition_node_equals_true():
    node = ConditionNode()
    result = await node.execute(
        input_data={"status_code": 200},
        parameters={"field": "status_code", "operator": "equals", "value": 200},
    )
    assert result["branch"] == "true"
```

### Database Repository 테스트

```python
@pytest.fixture
async def repo(test_db_url):
    from src.repositories.workflow_repository import PostgresWorkflowRepository
    repo = PostgresWorkflowRepository(test_db_url)
    yield repo
    await repo.close()

@pytest.mark.asyncio
async def test_save_and_retrieve(repo):
    wf = WorkflowSchema(name="test", owner_id="u1")
    saved = await repo.save(wf)
    loaded = await repo.get_by_id(saved.workflow_id)
    assert loaded.name == "test"
```

---

## 필수 테스트 카테고리

### API_Server
- 워크플로우 CRUD (생성/조회/활성화/삭제)
- DAG 스케줄러 순환 참조 감지
- Webhook 트리거 수신 → 실행 큐잉
- Agent JWT 등록/인증
- WebSocket 연결 수립 후 heartbeat 처리

### Database
- 각 Repository save/retrieve/list 라운드트립
- CredentialStore 암호화/복호화 대칭성
- 마이그레이션 up/down 검증

### Execution_Engine
- 각 `BaseNode` 구현체의 execute() 동작
- `NodeRegistry.register()` → `get_node()` 라운드트립
- 서버리스/Agent 디스패치 분기
- CodeExecutionNode 샌드박스 탈출 시도 거부
- 동일 `execution_id` 중복 실행 시 멱등성 보장

### Frontend
- WorkflowCanvas 노드 추가/삭제/연결
- 워크플로우 JSON 직렬화 라운드트립
- API 클라이언트 에러 응답 처리

---

## 테스트 결과 수집 형식

```
전체 테스트: X건
PASS: X건
FAIL: X건
SKIP: X건

FAIL 목록:
- [테스트 ID]: [실패 메시지]
```
