# Orchestrator Agent Instructions — Database

## Role
Manages the entire TDD cycle for each PLAN. Reads the PLAN, breaks the work into pieces, and invokes each agent in order.

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

`Database/plans/PLAN_NN_*.md` — PLAN_01–08 Done.

---

## Branch-boundary rules

- On the Database branch, modify only the `Database/` directory
- Do not modify `schemas/001_core.sql`
- New Repositories are added as an ABC + implementation + InMemory fake set

---

## Completion criteria

- [ ] Security Audit PASS
- [ ] Test + implementation complete
- [ ] All tests PASS
- [ ] Migration SQL written (when the schema changes)
- [ ] PR created
