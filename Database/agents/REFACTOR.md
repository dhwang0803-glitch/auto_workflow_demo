# Refactor Agent Instructions — Database

## Role
Runs only after every test PASSes. Improves code quality while keeping all tests green.

---

## Core principles

1. **Keep tests green**: after refactoring, re-run the full suite and confirm PASS
2. **No behavior change**: results must not change
3. **Scope restriction**: modify only `auto_workflow_database/` code
4. **Small steps**: improve one thing at a time, verify tests, then move on

---

## Items to consider

### Code quality
- [ ] One-shot helpers → inline them
- [ ] Unify duplicate DTO conversion logic
- [ ] Verify `flag_modified()` is not missing on JSONB mutations
- [ ] `DateTime(timezone=True)` unified

### Performance
- [ ] N+1 query pattern → batched fetch
- [ ] Check for missing indexes
- [ ] Pool config appropriate (`_session.py`)

### Consistency
- [ ] Repository ABC and implementation signatures match
- [ ] InMemory fakes behave the same as the real implementation

---

## Scope excluded

- `tests/`, `plans/`, `schemas/001_core.sql`, `.env`

---

## After refactoring

1. Re-run the full test suite after `taskkill`
2. Confirm PASS/FAIL counts match
3. Hand changes to the Reporter Agent
