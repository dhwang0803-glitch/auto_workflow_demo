# Tester Agent Instructions

## Role

After Developer Agent writes the implementation files, actually runs the tests and collects results.
All Execution_Engine tests are based on pytest + pytest-asyncio.

---

## Runtime environment

- Python 3.11+ (anaconda3)
- Windows 11 (PowerShell or Git Bash)
- `pip install -e .` completed
- Docker Postgres container (port 5435) — required for integration tests

---

## Process management rules (MANDATORY)

1. **Run exactly one test process at a time** — always kill the previous process before starting a new run
   ```bash
   taskkill //F //IM python.exe 2>/dev/null
   python -m pytest tests/ -v
   ```
2. **In the fail → fix → re-run cycle, not killing the previous process accumulates zombies that hog CPU/memory and slow later tests**
3. No background execution — read results immediately in the foreground
4. Replace infinite-loop tests (sandbox timeout, etc.) with bounded loops (`range(10**8)`) — Python threads cannot be killed, so design them to exit naturally

---

## Test execution order

### Unit tests (no DB required)
```bash
python -m pytest tests/ -v
```

### Integration tests (Docker Postgres required)
```bash
# 1. Verify Docker Postgres is running
docker compose up -d

# 2. Run migrations (mandatory after DB re-init)
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5435/auto_workflow"
python scripts/migrate.py

# 3. Run integration tests
python -m pytest tests/ -v -m integration
```

---

## Result parsing and reporting

```
[Tester run results]
- Run environment: Python {version}, Docker Postgres {running/not running}
- Files run: [list]
- Total tests: X
- PASS: X
- FAIL: X
- Duration: X s

FAIL items:
- [test ID] [error message summary]

Next action:
- 0 FAIL → proceed to commit
- FAIL exists → analyze cause → fix code → kill previous process → re-run
```

---

## Cautions

1. Do not expose `.env` connection info in logs or output
2. Do not use `while True: pass` in sandbox tests — threads cannot be killed
3. On `UndefinedColumn` error after DB re-init, re-run the migration
4. Always kill the previous python process before re-running tests
