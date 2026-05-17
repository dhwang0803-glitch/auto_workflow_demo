# Refactor Agent Instructions — Execution_Engine

## Role
Runs only after every test PASSes. Improves code quality while keeping all tests green.

---

## Core principles

1. **Keep tests green**: after refactoring, re-run the full suite and confirm PASS
2. **No behavior change**: results must not change
3. **Scope restriction**: modify only `src/` code
4. **Small steps**: improve one thing at a time, verify tests, then move on

---

## Items to consider

### Code quality
- [ ] One-shot helpers → inline them (avoid function sprawl)
- [ ] Keep the NodeRegistry intent comment (stores classes, not instances)
- [ ] Verify sandbox guard functions are present (`_getitem_`, `_write_`, `_inplacevar_`)

### Architecture
- [ ] Verify new repo/node is not instantiated outside WorkerContainer
- [ ] Interface consistency between executor and nodes

### Performance
- [ ] asyncio.gather parallel execution applied correctly
- [ ] Missing `to_thread` + `wait_for` timeout pattern

---

## Scope excluded

- `tests/`, `plans/`, `config/`, `scripts/`

---

## After refactoring

1. Re-run the full test suite after `taskkill`
2. Confirm PASS/FAIL counts match
3. Hand changes to the Reporter Agent
