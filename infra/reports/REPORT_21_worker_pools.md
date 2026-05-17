# ADR-021 Phase 6 — Worker Pools Live E2E Results Report

**Subject**: ADR-021 Phase 6 (see `docs/context/decisions.md`, PLAN_21 §6)
**Date**: YYYY-MM-DD  _(fill in after live E2E is run)_
**Status**: 🟡 Skeleton — live E2E not yet executed

> This report is filled in after actually running Steps 1–8 of
> `infra/docs/RUNBOOK_phase21_e2e.md`. It collects the observations from
> staging apply → `/execute` x3 → destroy in one place.

---

## 1. Change results

### Files changed
| File | Location | Description |
|------|------|------|
| worker.tf | infra/terraform/ | switched scaling_mode to MANUAL (consistent with the Python SDK) |
| variables.tf | infra/terraform/ | removed the unused `ee_worker_max_instances` |
| test_phase_21.bats | infra/tests/ | updated to the MANUAL + manual_instance_count contract |
| run_e2e_phase21.sh | infra/scripts/ | Phase 6.1 observability runner (steps 4-7) |
| RUNBOOK_phase21_e2e.md | infra/docs/ | Step 1–8 execution guide |

### Key implementations
- MANUAL scaling mode confirmed — `WorkerPoolScaling` in `google-cloud-run` 0.16.0 SDK exposes only `manual_instance_count`, so the AUTOMATIC path cannot be patched from Python
- Scale-down currently relies on `terraform destroy` — an automatic watchdog is a post-Phase-6 follow-up
- `run_e2e_phase21.sh` is read-only (curl + `gcloud logging read`); terraform apply/destroy is manual

---

## 2. Test results (TESTER)

### Summary
| Stage | Tool | Count | PASS | FAIL | SKIP |
|------|------|------|------|------|------|
| Phase A static | terraform validate | 1 | 1 | 0 | 0 |
| Phase A static | terraform fmt -check | 1 | 1 | 0 | 0 |
| Phase B unit | bats (test_phase_21.bats) | 12 | _TBD_ | _TBD_ | _TBD_ |
| Phase C plan | terraform plan (staging) | _TBD_ | | | |
| Phase D live | run_e2e_phase21.sh (staging) | 3 execs | _TBD_ | _TBD_ | _TBD_ |

### Live E2E measurements (RUNBOOK Step 4–7)
| Item | Value |
|------|----|
| Step 1 (image build + push) elapsed | _min_ |
| Step 2 (terraform apply) elapsed | _min_ |
| Step 3 (API redeploy) elapsed | _min_ |
| First `/execute` → `status=success` elapsed | _seconds_ (cold start included) |
| 2nd, 3rd `/execute` average elapsed | _seconds_ (warm pickup) |
| WakeWorker log `woken` occurrences | _n_ (target: 1) |
| Worker Cloud Logging instance-startup log | present / absent |

### Detailed FAILs (resolved)
_Record failures discovered during live E2E and how they were resolved. Verify whether the predictions in PLAN_21 §8 risk table actually materialized._

---

## 3. Refactoring (REFACTOR)

- No refactoring (avoid premature abstraction) — n/a
- Or record discovered duplications / improvements

---

## 4. Security audit (SECURITY_AUDITOR)

| Rule | Result |
|------|------|
| I01 tfvars real-value commit | PASS |
| I02 tfstate commit | PASS |
| I03 HCL secret hardcoded | PASS |
| I04 project ID hardcoded | PASS |
| I05 gcloud stdout leak | PASS (runner uses `--format='value(timestamp)'`, only timestamps) |
| I06 GH Actions secret log | N/A |
| I07 deletion_protection / ignore_changes | PASS |
| I08 Ruleset bypass (WARN) | _recorded_ |
| I09 .gitignore required entries | PASS |
| I10 IAM least-privilege (WARN) | roles/run.developer → follow-up: shrink to custom role `workerPool.updateOnly` (PLAN_21 §8) |

---

## 5. Impact assessment (IMPACT_ASSESSOR)

- **Risk grade**: 🟡 MEDIUM
- **Basis**: MANUAL mode scale-down is not automatic → leaving staging idle without implementation keeps one Worker Pool instance billing
- **terraform plan**: _add=N change=N destroy=N (fill in after Step 2)_
- **Downstream impact**:
  | Branch | Affected | Follow-up PR |
  |--------|------|---------|
  | API_Server | ✅ inline-branch removal pending (Phase 6.2) | _TBD_ |
  | Database | ➖ | none |
  | Execution_Engine | ➖ | none |
- **Rollback plan**:
  - [ ] staging pre-verification done
  - [ ] Memorystore / Worker Pool can be destroyed (path to clear prevent_destroy verified)
  - [ ] tfstate local snapshot path: `infra/terraform/terraform.tfstate.backup.<ts>`

---

## 6. Review (REVIEW)

- Critical: 0
- Major: 1 — MANUAL mode scale-down lacks automation (registered as a follow-up)
- Minor: _TBD_

Remaining items:
- [Major] `worker.tf` scaling — idle watchdog not implemented — track separately via Cloud Scheduler + Cloud Functions (post-Phase-6 ticket)

---

## 7. User approval record

- staging apply approval: _YES/NO (timestamp)_
- staging destroy approval: _YES/NO_
- Notes: MANUAL scale-down replaced by destroy is approved

---

## 8. Recommendations for the next Phase

1. **Phase 6.2** — remove the API_Server inline branch + `test_execute_inline.py`, add `.github/workflows/inline-guard.yml` (infra branch). Proceed only after live E2E succeeds.
2. **Scale-down watchdog** — Cloud Scheduler (5-min interval) → Cloud Functions → `workerPools.patch(manual_instance_count=0)` (only after the Celery queue is empty). Record in the ADR-021 Update section.
3. **Shrink IAM** — `roles/run.developer` → custom role `run.workerPools.update` with just one permission. Finalize the actual needed permission set from live logs.
4. **Update the ADR-021 Phase table** — separate PR on the docs branch to ✅ mark.
5. **Record measured cost** — add daily cost per category (Memorystore BASIC 1GB / Worker Pool 0.5 vCPU × 1h / API min=1, staging-session baseline) to `docs/context/decisions.md` ADR-021 Consequences.
