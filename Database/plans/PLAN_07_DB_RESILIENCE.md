# PLAN_07 — DB resilience & observability (Database)

> **Branch**: `Database` · **Drafted**: 2026-04-16 · **Status**: Draft
>
> Today `_session.py` has no defensive or observability settings beyond
> `pool_pre_ping=True`. A slow query that holds onto a connection saturates
> the default `pool_size=5`, FastAPI workers block for the
> `pool_timeout=30s` window, and the bottleneck explodes into contention.
> The Repository layer also has zero logging, so failing / slow queries are
> impossible to identify in production. This PLAN solves that **cross-cutting
> defense layer** in a single spot at the engine layer. No Repository code
> is touched.

## 1. Goals

1. Lock in explicit DB connection-pool / timeout defaults, overridable via env vars
2. Apply a server-side PostgreSQL `statement_timeout` cutoff (prevent zombie queries)
3. Log slow / failing queries from a **single SQLAlchemy event-listener point**
4. Map `OperationalError` / `DBAPIError` to 503 in the API_Server top-level handler
5. **Do not touch a single line of Repository files** — this is the foundation of the function-sprawl-prevention principle

## 2. Scope

**In**
- Extend `auto_workflow_database/repositories/_session.py` (up to ~40 lines)
- One module logger in `auto_workflow_database/__init__.py` or `_session.py`
- `tests/test_session_resilience.py` (new) — 3 cases
- API_Server-branch changes (only in the acceptance criteria, separate PR after this one merges):
  - One FastAPI exception handler dedicated to `OperationalError` / `DBAPIError` → 503

**Out**
- Modifying Repository implementations — forbidden
- Retry / circuit-breaker logic — separate PLAN if needed
- Monitoring / metrics collection (Prometheus exporter, etc.) — Phase 2
- Query-plan analysis / `EXPLAIN` integration — separate PLAN
- New migration files — this PLAN does not change the schema

## 3. Engine config changes (`_session.py`)

```python
DEFAULT_POOL_SIZE = 10
DEFAULT_MAX_OVERFLOW = 10
DEFAULT_POOL_TIMEOUT_S = 30      # keep default, env-overridable
DEFAULT_POOL_RECYCLE_S = 1800    # 30 min
DEFAULT_STATEMENT_TIMEOUT_MS = 5000  # keep default, env-overridable


def build_engine(dsn: str | None = None) -> AsyncEngine:
    dsn = dsn or os.environ["DATABASE_URL"]
    engine = create_async_engine(
        dsn,
        future=True,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", DEFAULT_POOL_SIZE)),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", DEFAULT_MAX_OVERFLOW)),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT_S", DEFAULT_POOL_TIMEOUT_S)),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE_S", DEFAULT_POOL_RECYCLE_S)),
        connect_args={
            "server_settings": {
                "statement_timeout": os.getenv(
                    "DB_STATEMENT_TIMEOUT_MS", str(DEFAULT_STATEMENT_TIMEOUT_MS)
                ),
            }
        },
    )
    _install_query_logging(engine)
    return engine
```

**Default policy**: keep SQLAlchemy's default (`pool_timeout=30s`) and the
proposed default (`statement_timeout=5000ms`) **as-is**. Real tuning happens
after the DB-host spec is finalized, via env vars (see §9 "Operations tuning notes").

**Environment variables**:

| Variable | Default | Purpose |
|---|---|---|
| `DB_POOL_SIZE` | 10 | Baseline pool size |
| `DB_MAX_OVERFLOW` | 10 | Overflow allowance |
| `DB_POOL_TIMEOUT_S` | 30 | Wait cap on pool exhaustion |
| `DB_POOL_RECYCLE_S` | 1800 | Connection-recycle interval |
| `DB_STATEMENT_TIMEOUT_MS` | 5000 | Postgres server-side cutoff |

## 4. Logging event listeners (inside `_session.py`)

**Module logger**: `logger = logging.getLogger("auto_workflow_database")`

`_install_query_logging(engine)` is called inside `build_engine` and
registers SQLAlchemy's sync-engine event APIs on `engine.sync_engine`.

```python
SLOW_QUERY_MS = int(os.getenv("DB_SLOW_QUERY_MS", "1000"))


def _install_query_logging(engine: AsyncEngine) -> None:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):
        context._query_start = time.monotonic()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, params, context, executemany):
        elapsed_ms = int((time.monotonic() - context._query_start) * 1000)
        if elapsed_ms >= SLOW_QUERY_MS:
            logger.warning(
                "slow query %dms: %s", elapsed_ms, statement[:200]
            )

    @event.listens_for(engine.sync_engine, "handle_error")
    def _on_error(ctx):
        stmt = (ctx.statement or "")[:200]
        logger.error(
            "db error %s on: %s",
            type(ctx.original_exception).__name__,
            stmt,
            exc_info=ctx.original_exception,
        )
```

**Important**: all three listener callbacks are defined inline inside
`_install_query_logging`. No separate files / classes / EngineEventHandler-style
abstractions. No decorators injected into the Repository layer.

## 5. API_Server top-level mapping

**After this PLAN merges**, as a **separate PR** (on the API_Server branch):

```python
# API_Server/app/main.py or error_handlers.py
from sqlalchemy.exc import DBAPIError, OperationalError

@app.exception_handler(DBAPIError)
async def _db_error_handler(request, exc: DBAPIError):
    # Database layer already logged the details — keep this minimal.
    api_logger.error("db error reached router: %s", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"error": "database_unavailable"},
    )
```

- `OperationalError` is a `DBAPIError` subclass — one handler covers both
- No try/except in routers — preserve the "global handler only" pattern that
  matches DomainError (PR #21 principle)
- Add exactly one handler function — no helper split

## 6. Function-sprawl-prevention guardrails

This PLAN must become the **showcase example of the function-sprawl-prevention principle**.

- Repository files (`workflow_repository.py`, `execution_repository.py`,
  `user_repository.py`, `credential_store.py`, etc.) are **off-limits**.
  `repositories/*.py` other than `_session.py` must not appear in `git diff`.
- Total `_session.py` line count: stay **≤ 70 lines** (currently 25 → expect ~65).
  The three inline event-listener definitions are this PLAN's core; the
  initial 40-line estimate isn't realistic. The essence is "no Repository
  edits + no helper / module splits", with line count as the proxy.
- No `EngineConfig` / `ResilienceSettings` dataclass to bundle settings.
  `build_engine` arguments + env vars are enough.
- Do not split the event-listener callbacks into a `query_logging.py` module.
- No `_slow_query_logger` / `_error_logger` thin wrappers — the listener
  callbacks are already small enough.
- API_Server-side exception handler: exactly one function. No
  `_format_db_error` / `_log_and_return_503` helpers.

**Reject in review on violation** — if this principle breaks, the PLAN
loses its reason to exist.

## 7. Tests (`tests/test_session_resilience.py`)

Postgres testcontainer-based. 3 cases are enough.

1. **`test_slow_query_logs_warning`**
   - Execute `SELECT pg_sleep(1.5)`
   - Use `caplog` to confirm a `"slow query"` warning was emitted
   - With the `DB_SLOW_QUERY_MS=1000` default, 1500 ms should trigger it

2. **`test_statement_timeout_aborts_long_query`**
   - Override `DB_STATEMENT_TIMEOUT_MS=500` and recreate the engine
   - `SELECT pg_sleep(2)` → confirm `asyncpg.QueryCanceledError`
   - Confirm the listener also wrote an error log

3. **`test_pool_timeout_raises_and_releases`**
   - Override `DB_POOL_SIZE=2`, `DB_POOL_TIMEOUT_S=1`
   - Hold 2 connections deliberately and confirm the 3rd request fails
     quickly with `TimeoutError`
   - After releasing the held connections, confirm the pool recovers normally

**API_Server integration tests are added in §5's PR** — out of scope here.

## 8. Acceptance criteria

- [ ] All 28 existing Database tests pass after changing `_session.py` (no regression)
- [ ] The 3 new tests pass
- [ ] Repository files have 0 lines in git diff (= nothing modified beyond `_session.py`, tests, and this PLAN doc)
- [ ] `_session.py` ≤ 70 lines
- [ ] Slow / error logs raised by `logging.getLogger("auto_workflow_database")` are caught by `caplog`
- [ ] Includes a case that verifies `statement_timeout` is actually applied on the Postgres session via `SHOW statement_timeout`
- [ ] (Separate PR) `DBAPIError` → 503 handler added on API_Server + integration test

## 9. Follow-up impact / operations-tuning notes

**This PLAN's defaults are a "safe starting point"** — re-tune after the
operational DB is finalized.

### 9.1 What `pool_size` / `max_overflow` mean — they're per-process

**Common misconception**: "`pool_size=10` means we can only handle 10 users"
→ **wrong**. `pool_size` is the **maximum number of DB connections a single
Python process may concurrently hold**, with no direct relationship to user
or request counts.

A single user's `GET /workflows` request borrows one connection for the
few-ms-to-tens-of-ms that the DB query actually runs and returns it to the
pool immediately afterward. So with `pool_size=10, max_overflow=10`
(= 20 connections per process) and an average 10 ms query, **a single
process can handle ~2000 requests/second**.

### 9.2 The real risk is "process multiplication"

What actually deserves attention is **total connection count exceeding
Postgres's `max_connections` limit**. Totals scale multiplicatively:

```
Total connection cap
  = (instance count)
    × (processes/workers per instance)
    × (pool_size + max_overflow)
  + (Scheduler worker processes)
  + (Execution_Engine worker processes)
```

Example — API_Server 1 instance, gunicorn `-w 4`, defaults (10/10):

| Component | Connections |
|---|---|
| API_Server 4 workers × 20 | 80 |
| Scheduler 1 worker × 20 | 20 |
| Execution_Engine N workers × 20 | N × 20 |
| **Total** | **100 + N × 20** |

With Postgres's default `max_connections=100`, **API_Server + Scheduler
alone already sit at the limit**. The moment Execution_Engine runs, it
overflows. The fix here is not "grow the pool" but **shrink the pool**.

### 9.3 Tuning formula

```
pool_size ≈ (Postgres max_connections × 0.5)
            / (instance count × worker count)
            − max_overflow
```

Reference points for the rest:

- `DB_STATEMENT_TIMEOUT_MS`: p99 query time × 3
- `DB_POOL_TIMEOUT_S`: API_Server request timeout − 1 second
- `DB_SLOW_QUERY_MS`: p95 query time + margin

**Rule of thumb**: when worker count grows, `pool_size` should actually
**drop**. The intuition "traffic is up so let's grow the pool" is usually
wrong; in most cases Postgres rejects first. If queries are slow and
exhaust the pool, before growing the pool look at `statement_timeout` and
slow-query logs and remove the root cause.

When tuning, you can adjust **via env vars only with no code changes** —
that's this PLAN's design intent. Configuration change → restart, no
re-deploy needed.

**Follow-up PLAN candidates** (after this PLAN, if needed):
- DB metrics export (Prometheus)
- Read-replica routing
- Retry decorator (for transient errors, idempotent queries only)

These are kicked off on top of this PLAN's logging foundation only after
deciding "is it really needed?".

## 10. Work order

1. Write the PLAN_07 document (this document)
2. Modify `_session.py` + add event listeners
3. Write `tests/test_session_resilience.py`
4. Verify the 3 cases pass on a local Postgres testcontainer
5. Verify no regression in the existing 28 tests
6. Open PR → review → merge
7. → start PLAN_06 (Execution list support)
8. → return to the API_Server branch + open the `DBAPIError` 503 handler PR
9. → draft API_Server PLAN_03 document
