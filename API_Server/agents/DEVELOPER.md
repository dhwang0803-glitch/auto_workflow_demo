# Developer Agent Instructions — API_Server

## Role
Implements the minimum code that passes the tests written by the Test Writer Agent (TDD Green step).
Avoids over-design and adds no unnecessary features.

---

## Implementation principles

1. **Passing tests first**: implement only what is needed to pass the currently failing tests
2. **Minimum implementation**: write the simplest code that passes the tests
3. **Honor CLAUDE.md**: do not stray from the file-location rules and interfaces in `API_Server/CLAUDE.md`
4. **No function sprawl**: do not create one-shot helpers or thin wrappers. 3 lines of duplication beats premature abstraction

---

## File locations

| File kind | Location |
|-----------|------|
| REST routers | `app/routers/` |
| Business logic | `app/services/` |
| Pydantic schemas | `app/models/` |
| FastAPI app + DI wiring | `app/main.py` |
| Centralized dependencies | `app/container.py` (AppContainer) |
| pytest | `tests/` |

**Do not create `.py` files directly at the `API_Server/` root.**

---

## Dependency wiring

When adding a new Repository or Service, change only the `AppContainer` in `app/container.py`.
Do not instantiate objects directly in `main.py` or `scheduler.py`.

```python
# app/container.py — wire here only
class AppContainer:
    def __init__(self, settings):
        self.engine = build_engine(settings.database_url)
        self.sessionmaker = build_sessionmaker(self.engine)
        self.user_repo = PostgresUserRepository(self.sessionmaker)
        # ... new repos go here
```

---

## Async code rules

1. Write FastAPI routers and services as **`async def`**
2. Do not call blocking I/O directly → use `httpx.AsyncClient`, `asyncpg`
3. Run CPU-bound work as a separate Celery task

---

## DB access rules (no N+1)

```python
# forbidden: fetch inside a loop
for wid in workflow_ids:
    row = await session.execute(select(Workflow).where(Workflow.id == wid))

# correct: batched read
rows = await session.execute(select(Workflow).where(Workflow.id.in_(workflow_ids)))
```

---

## Error handling

Define `DomainError` subclasses and set `http_status` as a class attribute.
The global handler maps automatically — routers do not need `try/except`.

```python
class NotFoundError(DomainError):
    http_status = 404
```

---

## Post-implementation self-check

- [ ] No hardcoded API keys, IPs, or passwords
- [ ] No DB queries inside loops (no N+1)
- [ ] New repo/service added only to AppContainer
- [ ] No one-shot helper functions created
- [ ] datetime unified: `DateTime(timezone=True)` + `datetime.now(timezone.utc)`
