# Orchestrator Agent Instructions — Execution_Engine

## Role
Manages the entire TDD cycle for each PLAN.

---

## Execution order

```
1. Security Auditor → 2. Read PLAN → 3. Decompose
4. Test Writer → 5. Developer → 6. Tester
7. On FAIL, re-invoke Developer (up to 3 times)
8. Reporter → 9. Security Auditor (pre-commit) → 10. Create PR
```

---

## PLAN file location

`Execution_Engine/plans/PLAN_NN_*.md`

| PLAN | Scope | Status |
|------|--------|------|
| PLAN_01 | BaseNode + NodeRegistry + HttpRequestNode | Done (PR #31) |
| PLAN_02 | DAG executor (Kahn level-sort + gather) | Done (PR #32) |
| PLAN_03 | Celery dispatcher (serverless mode) | Done (PR #33) |
| PLAN_04 | Agent daemon (WebSocket client + WS repo) | Done (PR #34) |
| PLAN_05 | ConditionNode + CodeNode + RestrictedPython | Done (PR #35) |

---

## Branch-boundary rules

- On the Execution_Engine branch, modify only the `Execution_Engine/` directory
- If prerequisite Database work is needed, check out that branch first

---

## Test-execution rules (MANDATORY)

- Keep exactly one test process at a time — `taskkill //F //IM python.exe` before re-running
- No background execution
- No infinite-loop tests (`while True: pass`, etc.)

---

## Completion criteria

- [ ] Security Audit PASS
- [ ] Test + implementation complete
- [ ] All tests PASS
- [ ] PR created
