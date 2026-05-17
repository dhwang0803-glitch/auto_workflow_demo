# Developer Agent Instructions — Database

## Role
Implements the minimum code that passes the tests written by the Test Writer Agent (TDD Green step).
Avoids over-design and adds no unnecessary features.

---

## Implementation principles

1. **Passing tests first**: implement only what is needed to pass the currently failing tests
2. **Minimum implementation**: write the simplest code that passes the tests
3. **Honor CLAUDE.md**: do not stray from the file-location rules in `Database/CLAUDE.md`
4. **No function sprawl**: do not create one-shot helpers or thin wrappers

---

## File locations

| File kind | Location | Import path |
|-----------|------|------------|
| DDL (CREATE TABLE/INDEX) | `schemas/` | — |
| Schema change (ALTER TABLE) | `migrations/YYYYMMDD_*.sql` | — |
| Repository implementation | `auto_workflow_database/repositories/` | `auto_workflow_database.repositories.X` |
| ORM model | `auto_workflow_database/models/` | `auto_workflow_database.models.X` |
| Crypto helper | `auto_workflow_database/crypto/` | `auto_workflow_database.crypto.X` |
| Migration script | `scripts/` | (run directly) |
| pytest | `tests/` | — |

**Do not create `.py` files directly at the `Database/` root.**

---

## Repository pattern

Keep the ABC interfaces (`base.py`) separate from the Postgres implementations. Tests use `InMemory*Repository` fakes.

---

## DB access rules

1. **Async only**: `create_async_engine` + `asyncpg`
2. **No N+1**: never run a DB query inside a loop
3. **Pool config**: centralized in `_session.py`'s `build_engine()`
4. **JSONB mutation**: `flag_modified()` is mandatory

---

## datetime unification

- ORM: `DateTime(timezone=True)` mandatory
- Python: use `datetime.now(timezone.utc)`
- Do not modify `schemas/001_core.sql` — migration 1 references it with `\i`
- Column additions/changes go through `migrations/` files only

---

## Post-implementation self-check

- [ ] No hardcoded DB URL or password
- [ ] No N+1 queries
- [ ] DateTime(timezone=True) unified
- [ ] New Repository added as an ABC + InMemory fake set
- [ ] Schema changes go through `migrations/` files only
