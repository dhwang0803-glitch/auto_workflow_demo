# Test Writer Agent Instructions

## Role

Writes failing tests before implementation (TDD Red step).
After implementation, runs the tests and collects results (verification step).

---

## Test-writing principles

1. Write the test before any implementation exists
2. Each test verifies exactly one requirement
3. State expected values clearly
4. On failure, include a message that lets you identify the cause
5. For tests that depend on external APIs / network, separate real calls from mock mode

---

## Per-branch test locations

| Branch | Test directory | Style |
|--------|--------------|------|
| `API_Server` | `API_Server/tests/` | pytest + httpx TestClient |
| `Database` | `Database/tests/` | pytest + real DB connection (test DB) |
| `Execution_Engine` | `Execution_Engine/tests/` | pytest + Celery eager mode |
| `Frontend` | `Frontend/tests/` | Jest + Playwright |

---

## Test examples (pytest)

### API_Server router test

```python
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_create_workflow_rejects_cycle():
    """A workflow with a cycle returns 400"""
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
    assert "cycle" in r.json()["detail"]
```

### Execution_Engine node test

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

### Database Repository test

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

## Required test categories

### API_Server
- Workflow CRUD (create / read / activate / delete)
- DAG scheduler cycle detection
- Webhook trigger received → execution queued
- Agent JWT register / authenticate
- WebSocket connection established + heartbeat handling

### Database
- Save/retrieve/list round-trip for each Repository
- CredentialStore encryption/decryption symmetry
- Migration up/down verification

### Execution_Engine
- `execute()` behavior of each `BaseNode` implementation
- `NodeRegistry.register()` → `get_node()` round trip
- Serverless / Agent dispatch branching
- CodeExecutionNode sandbox-escape attempts are rejected
- Idempotency when the same `execution_id` is executed twice

### Frontend
- WorkflowCanvas node add/delete/connect
- Workflow JSON serialization round-trip
- API client error response handling

---

## Result-collection format

```
Total tests: X
PASS: X
FAIL: X
SKIP: X

FAIL list:
- [test ID]: [failure message]
```
