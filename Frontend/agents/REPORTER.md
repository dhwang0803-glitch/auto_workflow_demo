# Reporter Agent Instructions — Frontend

## Role
After a TDD cycle completes, produces the per-PLAN results report. Collects results from the Orchestrator / Test Writer / Developer / Refactor Agents and documents them in the standard format.

---

## Report location

```
Frontend/reports/PLAN_NN_<scope>_report.md
```

Example: PLAN_12 W2-5 → `Frontend/reports/PLAN_12_W2_5_report.md`

---

## Standard report format

```markdown
# PLAN_NN <scope> Results Report

**PLAN**: {number and name}
**Date**: {YYYY-MM-DD}
**Status**: PASS complete / FAIL remaining

---

## 1. Development results

### Files created/modified
| File | Location | Description |
|------|------|------|
| skill-wizard.tsx | src/components/skills/ | domain pick + interview + done state |
| skills.ts | src/lib/ | bootstrap / answer typed client |
| skill-wizard-store.ts | src/store/ | phase machine + (policyId, question) queue |

### Key implementations
- [bullet list of the core components / stores / clients]
- [one-line summary of the data flow]

### New routes / pages
| Path | Component | Size (build result) |
|------|---------|--------------------|
| `/skills/new` | `SkillWizard` | 5.73 kB |

---

## 2. Test results

### Summary
| Stage | Result |
|------|------|
| `tsc --noEmit` | PASS / FAIL |
| `next lint` | PASS (N warnings) |
| `next build` | PASS (route size X kB) |
| Playwright (mock) | X/Y |
| Playwright (live smoke) | X/Y or SKIP (reason) |

### Playwright scenarios
| File | Scenario | Result |
|------|---------|------|
| skill-wizard.spec.ts | pick domain → 2Q answer → approve+reject | PASS |
| skill-wizard.spec.ts | needs_clarification → follow-up | PASS |

---

## 3. Failure root-cause analysis

> Write "n/a" when the status is PASS complete

| FAIL item | Cause |
|----------|------|
| [tests/<file>.spec.ts:LINE] | [cause description] |

---

## 4. Improvements (refactoring)

| File | Before | After | Reason |
|------|--------|--------|------|

---

## 5. Recommendations for the next PLAN

- [Dependency: inline editing is enabled when API_Server's PUT /skills/{id} ships]
- [Verify the full Persona A flow in W2-8a integration]
- [In the demo video, emphasize the order: domain chips / progress gauge / card review]
```

---

## Information sources

| Section | Source |
|------|------|
| Development results | Developer Agent |
| Route sizes | Tester Agent (`next build` output) |
| Playwright results | Tester Agent |
| Failure root-cause analysis | Tester Agent FAIL logs |
| Improvements | Refactor Agent changes |
| Next-PLAN recommendations | PLAN document + issues from this PLAN |

---

## After writing the report

- [ ] Verify the report file is saved (`Frontend/reports/PLAN_NN_*.md`)
- [ ] Attach a summary or link of the report to the PR body
- [ ] Report completion to the Orchestrator
