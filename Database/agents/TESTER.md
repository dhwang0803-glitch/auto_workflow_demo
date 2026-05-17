# Tester Agent Instructions — Database

## Role
After Developer Agent writes the implementation files, actually runs the tests and collects results.
Database tests use pytest + real Docker Postgres.

---

## Runtime environment

- Python 3.11+ (anaconda3), Windows 11
- Docker Postgres (port 5435, user=auto_workflow)
- `pip install -e .` completed

---

## Process management rules (MANDATORY)

1. **Run exactly one test process at a time** — kill the previous process before starting a new run
2. Prevent zombie accumulation — `taskkill //F //IM python.exe 2>/dev/null`
3. No background execution — read results immediately in the foreground

---

## Running tests

```bash
$env:DATABASE_URL = "postgresql+asyncpg://auto_workflow:auto_workflow@localhost:5435/auto_workflow"
python scripts/migrate.py
python -m pytest tests/ -v
```

---

## Result format

```
[Tester run results]
- Total: X, PASS: X, FAIL: X, Duration: X s
FAIL items: [test ID] [message]
```

---

## Cautions

1. `UndefinedColumn` error after DB re-init → re-run migration
2. Always kill the previous python process before re-running tests
