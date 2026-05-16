# PLAN_09 — DBQueryNode (Postgres via asyncpg)

> Predecessor: PLAN_08 (credential resolution) — the `postgres_dsn`
> credential_type was reserved for this node in blueprint §1.2.

## Purpose

Run a SQL query against the customer's Postgres DB from a workflow.
Parameter binding (`$1, $2`) is the first-line defense against SQL
injection. We assume the credential_ref injected the plaintext DSN into
config (handled by the PLAN_08 Worker).

## Scope

- **DB supported**: Postgres only (asyncpg). MySQL/SQLite become separate node types later.
- **Allowed SQL**: any statement (SELECT/INSERT/UPDATE/DELETE/DDL) — BYO model, the customer credential = customer responsibility.
- **Parameter binding enforced**: asyncpg only supports `$N` placeholders → string interpolation isn't possible by construction.
- **Target segment**: **Middle** (users who registered an internet-reachable managed Postgres such as Supabase / Neon / RDS public endpoint). Heavy (VPC-internal DB) waits for the Agent credential follow-up.

## File changes

### New
| File | Role |
|------|------|
| `src/nodes/db_query.py` | DBQueryNode — asyncpg.connect + fetch/execute |
| `tests/test_db_query_node.py` | AsyncMock-based unit tests |

### Modified
| File | Change |
|------|--------|
| `pyproject.toml` | Add `asyncpg>=0.29` as a direct dependency (currently transitive via auto-workflow-database) |

## Implementation details

### DBQueryNode (`src/nodes/db_query.py`)

```python
class DBQueryNode(BaseNode):
    node_type = "db_query"

    async def execute(self, input_data, config):
        url = config["connection_url"]
        query = config["query"]
        params = config.get("parameters", [])
        timeout = config.get("timeout_seconds", 30)

        conn = await asyncio.wait_for(
            asyncpg.connect(dsn=url, timeout=timeout),
            timeout=timeout,
        )
        try:
            # fetch() returns list[Record] for any statement that produces rows
            # (SELECT, INSERT/UPDATE/DELETE ... RETURNING). Otherwise execute().
            stripped = query.lstrip().lower()
            returns_rows = stripped.startswith(("select", "with")) or "returning" in stripped
            if returns_rows:
                rows = await asyncio.wait_for(
                    conn.fetch(query, *params), timeout=timeout
                )
                return {
                    "rows": [dict(r) for r in rows],
                    "row_count": len(rows),
                }
            else:
                status = await asyncio.wait_for(
                    conn.execute(query, *params), timeout=timeout
                )
                # status is like "UPDATE 3" / "DELETE 5" — parse last token.
                affected = int(status.rsplit(" ", 1)[-1]) if status else 0
                return {"rows": [], "row_count": affected}
        finally:
            await conn.close()
```

**Design choices:**
- **Connection-per-call**: open and close per node invocation. No pooling — node instances are stateless (per registry description); sharing a pool would make them stateful. Suitable for low-traffic workflows.
- **`returns_rows` heuristic**: SELECT / WITH / RETURNING → `fetch()`, else → `execute()`. `fetch` does work on DDL, but lacks an affected count — branching keeps the API clear.
- **dict conversion**: asyncpg.Record is not JSON-serializable → convert with `dict(r)`. The executor stores it as JSONB via `append_node_result`.
- **Two timeouts**: one at connect, one at query. `asyncio.wait_for` is the outer guard.

## Security invariants

- DSN is injected via credential_ref → never put a plaintext DSN directly in the graph JSON (reaffirms blueprint §1.6 invariant 2)
- Don't interpolate parameters into the query string — use `$1 $2` only (asyncpg enforces it)
- Error messages bubble up from asyncpg as-is. asyncpg doesn't include the DSN in errors, but `SyntaxError: syntax error at or near "FOO"` can expose a query fragment. **Sanitizing error messages is a separate policy at the executor layer** — within this node we propagate as-is.

## Test strategy (AsyncMock-based, no DB)

Monkeypatch `asyncpg.connect` with an AsyncMock → verify `conn.fetch` /
`conn.execute` call args and return values.

### test_db_query_node.py (5 tests)
1. `test_select_returns_rows` — `SELECT` → fetch called, rows dict-converted, row_count correct
2. `test_insert_returns_affected_count` — `INSERT` → execute called, status `"INSERT 0 3"` → row_count=3
3. `test_parameters_passed_through` — `SELECT ... WHERE id = $1` + params=[42] → verify conn.fetch(query, 42)
4. `test_returning_clause_uses_fetch` — `INSERT ... RETURNING id` → goes through fetch
5. `test_connection_always_closed` — conn.close is called even if the query fails (finally)

## Dependency addition

```toml
dependencies = [
    "httpx>=0.27",
    "celery[redis]>=5.3",
    "websockets>=12.0",
    "RestrictedPython>=7.0",
    "aiosmtplib>=3.0",
    "asyncpg>=0.29",
    "auto-workflow-database",
]
```

## Checklist

- [ ] `src/nodes/db_query.py` — DBQueryNode + registry registration
- [ ] `pyproject.toml` — declare asyncpg explicitly
- [ ] 5 tests pass, overall 49→54
- [ ] Commit → push → PR

## Out of scope

- MySQL / SQLite as separate nodes — follow-up
- Heavy users (Agent mode) — after the Agent credential follow-up
- Connection pooling — currently per-call. If a high-query-frequency workflow becomes a problem, follow up (WorkerContainer holds the pool)
- Query-result row cap — omitted in MVP, protected by the customer timeout
- Sanitizing query fragments in error messages — executor-layer policy
