# Reporter Agent — infra branch instructions

## Role

After an infra TDD cycle completes, produces the per-Phase results report.
Collects results from ORCHESTRATOR / TEST_WRITER / DEVELOPER / TESTER /
REFACTOR / SECURITY_AUDITOR / IMPACT_ASSESSOR / REVIEW and documents them
in the standard format.

Phase numbering maps 1:1 to the Phase N in the relevant ADR (mostly
ADR-018/019/020/021). Example: ADR-019 Phase 6 →
`infra/reports/adr019_phase6_report.md`.

---

## Report location

```
infra/reports/<adr>_phase{N}_report.md
```

Examples:
- `infra/reports/adr018_phase1_report.md` — Cloud SQL initial provisioning
- `infra/reports/adr019_phase6_report.md` — OAuth Secret Manager injection
- `infra/reports/adr021_phase1_report.md` — (pending) Worker deployment path

For standalone work not tied to a single ADR, use
`infra/reports/YYYY-MM-DD_<slug>.md`.

---

## Standard report format

```markdown
# <ADR-NNN> Phase {N} — <phase name> Results Report

**Subject**: ADR-NNN Phase N (see `docs/context/decisions.md`)
**Date**: YYYY-MM-DD
**Status**: PASS complete / FAIL remaining / awaiting user approval

---

## 1. Change results

### Files changed
| File | Location | Description |
|------|------|------|
| main.tf | infra/terraform/ | 3 google_secret_manager_secret.google_oauth_* resources |
| inject_oauth_secrets.sh | infra/scripts/ | stdin-pipe injection script |
| ... | ... | ... |

### Key implementations
- (3–5 bullets)

---

## 2. Test results (TESTER)

### Summary
| Stage | Tool | Count | PASS | FAIL | SKIP |
|------|------|------|------|------|------|
| Phase A static | terraform validate | 1 | 1 | 0 | 0 |
| Phase A static | tflint | N | ... | ... | ... |
| Phase A static | checkov | N | ... | ... | ... |
| Phase A static | shellcheck | N | ... | ... | ... |
| Phase A static | actionlint | N | ... | ... | ... |
| Phase B unit | bats | N | ... | ... | ... |
| Phase C plan | terraform plan | add=N change=N destroy=N | | | |
| Phase D live | staging apply + smoke | (run/SKIP) | | | |

### Detailed FAIL (resolved)
| Item | Cause | Fix |
|------|------|------|
| tflint terraform_required_providers | missing provider version | pin google ~> 6.0 in versions.tf |
| ... | ... | ... |

---

## 3. Refactoring (REFACTOR)

| File | Change kind | Before → After | plan-diff |
|------|----------|-------------|-----------|
| main.tf | extract locals | project_id repeated 5× → local.project | no-change |
| scripts/ | split into lib | proxy startup duplicated across 3 scripts → lib/proxy.sh | (N/A) |

If none, write "no refactoring (avoid premature abstraction)".

---

## 4. Security audit (SECURITY_AUDITOR)

| Rule | Result |
|------|------|
| I01 tfvars real-value commit | PASS |
| I02 tfstate commit | PASS |
| I03 HCL secret hardcoded | PASS |
| I04 project ID hardcoded | PASS |
| I05 gcloud stdout leak | PASS |
| I06 GH Actions secret log | PASS |
| I07 deletion_protection / ignore_changes | PASS |
| I08 Ruleset bypass (WARN) | (recorded) |
| I09 .gitignore required entries | PASS |
| I10 IAM least-privilege (WARN) | (n/a) |

If any FAIL exists, state explicitly that it was resolved before writing this report.

---

## 5. Impact assessment (IMPACT_ASSESSOR)

- **Risk grade**: 🔴 HIGH / 🟡 MEDIUM / 🟢 LOW
- **Basis**: (1 line)
- **terraform plan**: add=N change=N destroy=N
- **Downstream impact**:
  | Branch | Affected | Follow-up PR |
  |--------|------|---------|
  | API_Server | ✅/➖ | <PR number or none> |
  | Database | ✅/➖ | |
  | Execution_Engine | ✅/➖ | |
- **Rollback plan**:
  - [ ] staging pre-verification done
  - [ ] Secret prior version: `<name>:vN`
  - [ ] tfstate local snapshot path

---

## 6. Review (REVIEW)

- Critical: N (resolved)
- Major: N (resolved / remaining)
- Minor: N (recorded as follow-up)

Remaining items:
- [Major] <file:line> — <content> — <follow-up ADR/Issue>

---

## 7. User approval record

- prod apply approval: YES/NO (timestamp)
- destroy approval: YES/NO
- Notes: (if any)

---

## 8. Recommendations for the next Phase

- (items to verify before starting the next Phase)
- (prerequisites)
- (operational metrics to watch)
```

---

## Information to collect and source

| Section | Source |
|------|------|
| Change results | DEVELOPER output + `git diff --stat main...HEAD` |
| Test results | TESTER Phase A/B/C/D output |
| Refactoring | REFACTOR item list |
| Security audit | SECURITY_AUDITOR I01–I10 results |
| Impact assessment | IMPACT_ASSESSOR report |
| Review | REVIEW Findings output |
| User approval | approval log collected by ORCHESTRATOR |
| Next-Phase recommendations | PLAN / ADR Phase items + issues from this Phase |

---

## After writing the report

- [ ] Verify `infra/reports/<adr>_phase{N}_report.md` is saved
- [ ] Check the matching item in the PLAN file's "progress checklist"
- [ ] Report completion to ORCHESTRATOR → move to the PR creation step (PR_REPORT skill)
- [ ] Link this report's summary in the "Impact Assessment" section of the PR body

---

## Cautions

1. Do not include secret values / tfstate contents / real project numbers
   (cost, etc.) in the report. Record only names, risk grade, and counts.
2. Do not mark the report as "PASS complete" while FAIL remains —
   write `Status: FAIL remaining`.
3. If the next-Phase recommendations include "app code changes required in
   the next Phase," tag the owning branch's owner.
4. The report is included in the infra-branch PR. It does not target the
   docs branch.
