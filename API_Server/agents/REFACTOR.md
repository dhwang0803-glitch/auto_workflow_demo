# Refactor Agent Instructions — API_Server

## Role
Runs only after every test PASSes. Improves code quality while keeping all tests green (TDD Refactor step).

---

## Core principles

1. **Keep tests green**: after refactoring, always re-run the full test suite and confirm PASS
2. **No behavior change**: do not make changes that alter the runtime result
3. **Scope restriction**: modify only the `app/` files of the requested PLAN
4. **Small steps**: improve one thing at a time, verify tests, then move on

---

## Items to consider

### Code quality
- [ ] One-shot helper functions exist → inline them (avoid function sprawl)
- [ ] Duplicate logic → consider unifying if 3+ lines
- [ ] Hardcoded values → Settings or env vars
- [ ] Missing DomainError subclass → add

### Architecture
- [ ] Verify new repo/service is not instantiated outside AppContainer
- [ ] Business logic leaked into routers → move to a service
- [ ] `try/except` in a router → delegate to the DomainError global handler

### Performance
- [ ] N+1 query pattern → batched fetch
- [ ] Unnecessary DB lookups → cache or remove

### Consistency
- [ ] datetime unified to `DateTime(timezone=True)` + `datetime.now(timezone.utc)`
- [ ] Response schemas in consistent shape

---

## Scope excluded

- Test files (`tests/`)
- PLAN documents (`plans/`)
- Env configs (`.env`)
- `conftest.py` (test infrastructure)

---

## After refactoring

```
1. Re-run the full test suite (kill first)
2. Confirm the PASS/FAIL counts match the previous run
3. Write a changelog → hand to Reporter Agent
```

## Format to hand to the Reporter Agent

```
[Refactoring items]
- File: [name]
- Before: [old code/structure summary]
- After: [improved code/structure summary]
- Reason: [why the change]
```
