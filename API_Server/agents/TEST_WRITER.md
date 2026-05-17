# Test Writer Agent Instructions — API_Server

## Role
Writes failing tests before implementation (TDD Red step).

---

## Test-writing principles

1. Write the test before any implementation exists
2. Each test verifies exactly one requirement
3. State expected values clearly
4. On failure, include a message that lets you identify the cause

---

## Test file location

```
API_Server/tests/test_{feature}.py
```

| File | Subject |
|------|----------|
| `test_auth.py` | signup / login / JWT / email verification |
| `test_workflows.py` | CRUD + quota + DAG validation |
| `test_dag_validator.py` | Kahn topological sort cycle detection |
| `test_executions.py` | execution trigger + history queries |
| `test_scheduler.py` | activate/deactivate + cron/interval |
| `test_webhooks.py` | webhook register / receive / HMAC verify |
| `test_agents.py` | Agent register + WebSocket heartbeat |

---

## Test examples

### Router E2E test (httpx AsyncClient)

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

### DAG pure-logic test (no DB needed)

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

## Required test categories

- Workflow CRUD (create / read / update / delete / list)
- DAG validation (cycles / duplicate ids / unknown edge)
- Execution trigger (manual run → 202 + queued)
- Execution history queries (single / list + keyset pagination)
- Scheduler (activate cron/interval, deactivate)
- Webhook (register / delete / receive + HMAC-SHA256 verify)
- Agent (register → JWT, WebSocket heartbeat)
- Auth (register / verify / login / token expiry / refresh)
- Quota (per-plan_tier workflow limits)
- Ownership (accessing another user's resource → 404)

---

## Result-collection format

```
Total tests: X
PASS: X
FAIL: X

FAIL list:
- [test ID]: [failure message]
```
