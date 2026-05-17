# Orchestrator Agent Instructions

## Role
Manages the entire TDD cycle for each Phase. Reads the PLAN, breaks the work into pieces, invokes each agent in order, and judges the completion criteria.

---

## Execution order

```
1. Invoke Security Auditor Agent (pre-Phase check)
   - FAIL → report to the user and stop
   - PASS → proceed
2. Read the Phase's PLAN file
3. Decompose the work list (into testable units)
4. Invoke Test Writer Agent → confirm test files were created
5. Invoke Developer Agent → confirm implementation files were created
6. Invoke Tester Agent → actually run the tests and collect results
7. Decide on the result
   - All PASS → invoke Refactor Agent
   - FAIL exists → re-invoke Developer Agent → re-run Tester Agent (up to 3 iterations)
8. Invoke Review Agent (defensive code review)
   - Receive results for the 7 axes (Correctness / Error handling / Test coverage / Performance / API design / Readability / Security delegation)
   - Critical → re-invoke Developer Agent → Tester → Refactor → re-run Review (up to 2 iterations)
   - Major → delegate to Developer or Refactor, then re-run Review
   - Only Minor → hand over to Reporter as is, proceed
   - Security delegation flag = yes → include that item in step 9's Security Auditor scope
9. Invoke Reporter Agent → confirm the report was generated (includes actual test results + Review Findings)
10. Invoke Security Auditor Agent (final pre-commit check)
    - FAIL → block commit, ask the user to act manually
    - PASS → proceed with git add/commit
11. Check completion criteria
```

---

## Per-Phase PLAN file location

| Phase | PLAN file |
|-------|----------|
| Phase 1 | `RAG/plans/PLAN_01_SETUP_PILOT.md` |
| Phase 2 | `RAG/plans/PLAN_02_HIGH_PRIORITY.md` |
| Phase 3 | `RAG/plans/PLAN_03_QUALITY_MONITORING.md` |
| Phase 4 | `RAG/plans/PLAN_04_MEDIUM_PRIORITY.md` |

---

## Decomposition principles

- Break the work into the smallest testable units
- Each unit must be independently verifiable
- Per-column dependencies: complete Phase 1 before Phase 2 (must verify pilot success criteria)
- Processing order inside Phase 2: director → cast_lead → rating → release_date

---

## Information to include when invoking an agent

Always include the following when invoking any agent:
- Current Phase number
- Target file paths
- Result of the previous step (test results when invoking Developer, implementation results when invoking Refactor, base/head ref + changed-file list when invoking Review)

---

## Failure handling rules

- If Developer Agent still has FAILs after 3 iterations → hand the failure details to Reporter and generate the report
- If Review Agent still has Critical after 2 iterations → hand Findings to Reporter, ask for user review, and pause the next step
- Record details in the "Failure root-cause analysis" and "Improvements" sections of the report
- Recommend team review before starting the next Phase

---

## Completion criteria (per Phase, common)

- [ ] Security Audit PASS (before Phase)
- [ ] Test files created
- [ ] Implementation files created
- [ ] All tests PASS or remaining FAIL reasons documented
- [ ] Review Agent run, 0 Critical (Major/Minor recorded in Findings)
- [ ] Report generated (`RAG/reports/phaseX_report.md`)
- [ ] Security Audit PASS (before commit)
