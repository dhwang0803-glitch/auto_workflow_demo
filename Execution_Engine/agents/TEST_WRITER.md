# Test Writer Agent Instructions — Execution_Engine

## Role
Writes failing tests before implementation (TDD Red step).

---

## Test-writing principles

1. Write the test before any implementation exists
2. Each test verifies exactly one requirement
3. Use InMemory fakes — no real DB needed

---

## Test file location

```
Execution_Engine/tests/test_{feature}.py
```

| File | Subject |
|------|----------|
| `test_http_request_node.py` | HttpRequestNode + registry |
| `test_condition_node.py` | ConditionNode per-operator branching |
| `test_code_node.py` | CodeNode + RestrictedPython sandbox |
| `test_executor.py` | DAG executor (single/chain/diamond/failure/empty) |
| `test_dispatcher.py` | Celery dispatcher _execute() |
| `test_agent.py` | WebSocketExecutionRepository + command handler |

---

## Test examples

### Node unit test

```python
async def test_condition_eq_true():
    node = ConditionNode()
    result = await node.execute(
        {"status": 200},
        {"left_field": "status", "operator": "eq", "right_value": 200},
    )
    assert result["result"] is True
```

### DAG executor test (InMemory fakes)

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

### Sandbox security test

```python
def test_import_blocked():
    with pytest.raises(ImportError):
        run_restricted("import os", {})
```

---

## Required test categories

- `execute()` behavior of each BaseNode implementation
- NodeRegistry register/get round-trip
- DAG executor: single node, chain, diamond parallel, failure, empty graph
- Celery dispatcher: normal / missing execution / missing workflow / node failure
- Agent: WS repo message send, execute command success/failure
- Sandbox: import block, open block, timeout

---

## Result-collection format

```
Total: X, PASS: X, FAIL: X
FAIL: [test ID]: [message]
```
