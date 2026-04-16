# Test Writer Agent 지시사항 — Execution_Engine

## 역할
구현 전에 실패하는 테스트를 먼저 작성한다 (TDD Red 단계).

---

## 테스트 작성 원칙

1. 구현 코드가 없어도 테스트를 먼저 작성한다
2. 각 테스트는 하나의 요구사항만 검증한다
3. InMemory fakes 사용 — 실 DB 불필요

---

## 테스트 파일 위치

```
Execution_Engine/tests/test_{기능명}.py
```

| 파일 | 검증 대상 |
|------|----------|
| `test_http_request_node.py` | HttpRequestNode + registry |
| `test_condition_node.py` | ConditionNode 연산자별 분기 |
| `test_code_node.py` | CodeNode + RestrictedPython sandbox |
| `test_executor.py` | DAG executor (single/chain/diamond/failure/empty) |
| `test_dispatcher.py` | Celery dispatcher _execute() |
| `test_agent.py` | WebSocketExecutionRepository + command handler |

---

## 테스트 작성 예시

### 노드 단위 테스트

```python
async def test_condition_eq_true():
    node = ConditionNode()
    result = await node.execute(
        {"status": 200},
        {"left_field": "status", "operator": "eq", "right_value": 200},
    )
    assert result["result"] is True
```

### DAG executor 테스트 (InMemory fakes)

```python
async def test_diamond_parallel(reg, repo):
    graph = {
        "nodes": [
            {"id": "a", "type": "add", "config": {"amount": 1}},
            {"id": "b", "type": "add", "config": {"amount": 10}},
            {"id": "c", "type": "add", "config": {"amount": 100}},
            {"id": "d", "type": "add", "config": {"amount": 0}},
        ],
        "edges": [
            {"source": "a", "target": "b"}, {"source": "a", "target": "c"},
            {"source": "b", "target": "d"}, {"source": "c", "target": "d"},
        ],
    }
    await run_workflow(graph, ex, repo, reg)
    result = await repo.get(ex.id)
    assert result.status == "success"
```

### Sandbox 보안 테스트

```python
def test_import_blocked():
    with pytest.raises(ImportError):
        run_restricted("import os", {})
```

---

## 필수 테스트 카테고리

- 각 BaseNode 구현체의 execute() 동작
- NodeRegistry register/get 라운드트립
- DAG executor: single node, chain, diamond parallel, failure, empty graph
- Celery dispatcher: 정상/missing execution/missing workflow/node failure
- Agent: WS repo 메시지 전송, execute 커맨드 성공/실패
- Sandbox: import 차단, open 차단, 타임아웃

---

## 결과 수집 형식

```
전체: X건, PASS: X건, FAIL: X건
FAIL: [테스트 ID]: [메시지]
```
