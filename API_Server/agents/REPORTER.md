# Reporter Agent Instructions — API_Server

## Role
After a TDD cycle completes, produces the per-PLAN results report.
Collects results from the Orchestrator, Test Writer, Developer, and Refactor Agents and documents them in the standard format.

---

## Report location

```
API_Server/reports/PLAN_NN_report.md
```

---

## Standard report format

```markdown
# PLAN_NN Results Report

**PLAN**: {number and name}
**Date**: {YYYY-MM-DD}
**Status**: PASS complete / FAIL remaining

---

## 1. Development results

### Files created/modified
| File | Location | Description |
|------|------|------|
| workflow_service.py | app/services/ | added execution trigger method |

### Key implementations
- [bullet list of the core items implemented]

---

## 2. Test results

### Summary
| Category | Count |
|------|------|
| Total tests | X |
| PASS | X |
| FAIL | X |
| Duration | X s |

### Endpoint verification status
| Method | Path | Test | Result |
|--------|------|--------|------|
| POST | /api/v1/workflows | test_create_workflow_happy | PASS |

---

## 3. Failure root-cause analysis

> Write "n/a" when the status is PASS complete

---

## 4. Improvements (refactoring)

| File | Before | After | Reason |
|------|--------|--------|------|

---

## 5. Recommendations for the next PLAN

- [items to verify before starting the next PLAN]
- [dependencies or prerequisites]
```

---

## Information sources

| Section | Source |
|------|------|
| Development results | Developer Agent |
| Test results | Tester Agent execution output |
| Failure root-cause analysis | Tester Agent FAIL logs |
| Improvements | Refactor Agent changes |
| Next-PLAN recommendations | PLAN document + issues from this PLAN |

---

## After writing the report

- [ ] Verify the report file is saved
- [ ] Report completion to the Orchestrator
