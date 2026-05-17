# Orchestrator Agent Instructions — API_Server

## Role
Manages the entire TDD cycle for each PLAN. Reads the PLAN, breaks the work into pieces, invokes each agent in order, and judges the completion criteria.

---

## Execution order

```
1. Invoke Security Auditor Agent (pre-PLAN check)
   - FAIL → report to the user and stop
   - PASS → proceed
2. Read the PLAN file
3. Decompose the work list (into testable units)
4. Invoke Test Writer Agent → confirm test files were created
5. Invoke Developer Agent → confirm implementation files were created
6. Invoke Tester Agent → actually run the tests and collect results
7. Decide on the result
   - All PASS → invoke Refactor Agent
   - FAIL exists → re-invoke Developer Agent → re-run Tester Agent (up to 3 iterations)
8. Invoke Reporter Agent → generate the report
9. Invoke Security Auditor Agent (final pre-commit check)
10. git add/commit/push → create the PR
```

---

## PLAN file location

```
API_Server/plans/PLAN_NN_*.md
```

| PLAN | Scope | Status |
|------|--------|------|
| PLAN_01 | Auth + User Management | Done (PR #18) |
| PLAN_02 | Workflow CRUD | Done (PR #20) |
| PLAN_03 | Manual execution trigger + history queries | Done (PR #26) |
| PLAN_04 | Scheduler worker + activate/deactivate | Done (PR #27) |
| PLAN_05 | Webhook + HMAC-SHA256 | Done (PR #28) |
| PLAN_06 | Agent WebSocket + registration | Done (PR #30) |

---

## Branch-boundary rules

- On the API_Server branch, modify only the `API_Server/` directory
- If prerequisite work in the Database branch is needed, check out that branch first
- Monorepo subdirectory ≠ unit of work. **Always work on the correct branch**

---

## Information to include when invoking an agent

- Current PLAN number and file path
- Target file list
- Result of the previous step (test results, implementation results)
- Dependency wiring lives only in `app/container.py`

---

## Failure handling

- If Developer Agent still has FAILs after 3 retries → hand the failure details to Reporter
- Record details in the report's "Failure root-cause analysis" section
- Recommend user review before starting the next PLAN

---

## Completion criteria

- [ ] Security Audit PASS (before)
- [ ] Test files created
- [ ] Implementation files created
- [ ] All tests PASS
- [ ] Report generated
- [ ] Security Audit PASS (before commit)
- [ ] PR created and review requested
