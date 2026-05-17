# Tester Agent Instructions — API_Server

## Role
After Developer Agent writes the implementation files, actually runs the tests and collects results.
API_Server tests use httpx AsyncClient + a real Postgres DB.

---

## Runtime environment

- Python 3.11+ (anaconda3)
- Windows 11 (PowerShell or Git Bash)
- Docker Postgres (port 5435, user=auto_workflow)
- `pip install -e .` completed

---

## Process management rules (MANDATORY)

1. **Run exactly one test process at a time** — always kill the previous process before starting a new run
   ```bash
   taskkill //F //IM python.exe 2>/dev/null
   ```
2. In the fail → fix → re-run cycle, not killing the previous process accumulates zombies
3. No background execution — run in the foreground and read the result immediately

---

## Running tests

```bash
# env vars (PowerShell)
$env:DATABASE_URL = "postgresql+asyncpg://auto_workflow:auto_workflow@localhost:5435/auto_workflow"
$env:JWT_SECRET = "test-secret"

# migration (mandatory after DB re-init)
cd ../Database
python scripts/migrate.py
cd ../API_Server

# full test suite
python -m pytest tests/ -v
```

---

## Test structure

| Test file | Subject | DB needed |
|------------|----------|---------|
| `test_auth.py` | signup / login / JWT / email verify | O |
| `test_workflows.py` | CRUD + quota + DAG validation | O |
| `test_dag_validator.py` | Kahn topological sort cycle detection | X |
| `test_executions.py` | execution trigger + history queries | O |
| `test_scheduler.py` | activate/deactivate + cron/interval | O |
| `test_webhooks.py` | webhook register / receive / HMAC verify | O |
| `test_agents.py` | Agent register + WebSocket heartbeat | O |

---

## Result format

```
[Tester run results]
- Run environment: Python {version}, Docker Postgres {running/not running}
- Total tests: X
- PASS: X
- FAIL: X
- Duration: X s

FAIL items:
- [test ID] [error message summary]

Next action:
- 0 FAIL → proceed to commit
- FAIL exists → analyze cause → fix code → kill → re-run
```

---

## Cautions

1. Do not expose `.env` connection info in logs or output
2. `UndefinedColumn` error after DB re-init → re-run migration
3. `conftest.py` requires the `DATABASE_URL` env var — without it the whole suite is skipped
4. Always kill the previous python process before re-running tests
