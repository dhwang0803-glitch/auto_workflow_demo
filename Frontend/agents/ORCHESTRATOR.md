# Orchestrator Agent Instructions — Frontend

## Role
Manages the entire TDD cycle for each Frontend PLAN. Reads the PLAN, breaks the work into pieces, invokes each agent in order, and judges the completion criteria.

---

## Execution order

```
1. Invoke Security Auditor Agent (pre-PLAN check)
   - FAIL → report to the user and stop
   - PASS → proceed
2. Read the PLAN file
3. Decompose the work list (testable units — usually route/component/store)
4. Invoke Test Writer Agent → confirm Playwright spec was authored
5. Invoke Developer Agent → confirm component/store/client implementation
6. Invoke Tester Agent → run tsc + lint + build + Playwright and collect results
7. Decide on the result
   - All steps PASS → invoke Refactor Agent
   - FAIL exists → re-invoke Developer Agent → re-run Tester Agent (up to 3 iterations)
8. Invoke Reporter Agent → generate the report
9. Invoke Security Auditor Agent (final pre-commit check)
10. git add / commit (mention PR-number dependencies in the message) / push → create the PR
```

---

## PLAN file location

```
Frontend/plans/PLAN_NN_*.md
AI_Agent/plans/PLAN_12_skill_bootstrap.md   # contains Frontend W2-5 / W2-6 / W3-1 items
```

| PLAN | Scope | Status |
|------|--------|------|
| PLAN_01 | Workflow Editor MVP (PR A/B/C) | Done |
| PLAN_02 | AI Composer (PR A/B/C/D) | Done |
| PLAN_12 W2-5 | Skill bootstrap interview wizard | Done (PR #137) |
| PLAN_12 W2-6 | Skill review cards + approve/reject | Done (PR #138) |
| PLAN_12 W3-1 | Document upload UI | Not started (after 05/05 outage) |

---

## Branch-boundary rules

- **On the Frontend branch, modify only the `Frontend/` directory** — monorepo subdirectory ≠ unit of work
- If an API_Server / AI_Agent contract change is required, check out that branch first and ship a separate PR
- Comply with `feedback_no_merge_commits_in_branch.md`: sync main with `git rebase origin/main` (NOT `git merge`). After a PR is merged, start re-work from `git reset --hard origin/main`

---

## Information to include when invoking an agent

- Current PLAN number + file path
- Target route / component / store list
- Result of the previous step (Playwright results, implementation results, route size)
- API contract dependencies (e.g., mirror of `API_Server/app/models/skills.py`)

---

## Failure handling

- After 3 Developer retries with FAIL → hand failure details to Reporter and ask for user review
- Playwright intermittent failure from a race condition → ensure `await route.fulfill` in the mock response + check for missing `await` on assertions
- Record details in the report's "Failure root-cause analysis"

---

## Completion criteria

- [ ] Security Audit PASS (before)
- [ ] Playwright spec authored
- [ ] Components / stores / clients implemented
- [ ] `tsc --noEmit` / `next lint` / `next build` all green
- [ ] Playwright (mock) 100% PASS
- [ ] Report generated (`Frontend/reports/PLAN_NN_*.md`)
- [ ] Security Audit PASS (before commit)
- [ ] PR body mentions dependent contract PR numbers (e.g., "Depends on PR #135 — `/api/v1/skills/*` endpoint")

---

## When an API_Server / AI_Agent contract change is included

To avoid the scenario where merging the Frontend PR alone breaks main (contract mismatch):

1. Merge the API_Server / AI_Agent contract PR first
2. Reset to main → start the Frontend branch fresh (`feedback_no_merge_commits_in_branch.md`)
3. Mention the dependent PR's merge SHA in the Frontend PR body → reviewers can trace it
