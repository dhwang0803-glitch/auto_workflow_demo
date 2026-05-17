# Orchestrator Agent — infra branch instructions

## Role

Coordinates the infra branch's work cycle. Drives Terraform / bash /
GitHub Actions changes through a TDD cycle (Red → Green → Refactor →
Review → Report), invoking the dedicated agent for each step and
integrating results.

---

## Execution flow

```
           ┌──────────────────────┐
           │ 0. Check PLAN         │  ← see plans/PLAN_NN_*.md or ADR Phase
           └──────────┬───────────┘
                      ▼
           ┌──────────────────────┐
     ┌─▶  │ 1. TEST_WRITER (Red)  │  write failing terraform validate/tflint/checkov/bats tests
     │     └──────────┬───────────┘
     │                ▼
     │     ┌──────────────────────┐
     │     │ 2. DEVELOPER (Green)  │  HCL/bash implementation — minimum to pass
     │     └──────────┬───────────┘
     │                ▼
     │     ┌──────────────────────┐
     │     │ 3. TESTER             │  actually run → PASS/FAIL aggregation
     │     └──────────┬───────────┘
     │           FAIL │ PASS
     └──── (retry N/3)▼
                      ▼
           ┌──────────────────────┐
           │ 4. REFACTOR          │  DRY (module/locals), shell lib
           └──────────┬───────────┘
                      ▼  (re-run TESTER — regression check)
           ┌──────────────────────┐
           │ 5. SECURITY_AUDITOR  │  I01–I10 mechanical checks
           └──────────┬───────────┘
                      ▼
           ┌──────────────────────┐
           │ 6. IMPACT_ASSESSOR   │  GCP resource impact + downstream delegation
           └──────────┬───────────┘
                      ▼
           ┌──────────────────────┐
           │ 7. REVIEW            │  7-axis defensive review
           └──────────┬───────────┘
                      ▼
           ┌──────────────────────┐
           │ 8. REPORTER          │  infra/reports/phase{N}_report.md
           └──────────────────────┘
```

---

## Invocation rules

1. **No skipping steps**. The only FAIL path is `TESTER → DEVELOPER` re-invocation.
2. **User-approval gates**:
   - Before prod apply: after SECURITY + IMPACT pass, ask the user to confirm the plan.
   - When a destroy is included: user confirmation is mandatory.
3. **Retry limit**: at most 3 DEVELOPER re-invocations. Beyond that, ask the user to revisit the PLAN.
4. **Do not modify app code**: do not touch files of other branches inside an infra PR.
   When a downstream change is required, delegate via IMPACT_ASSESSOR.

---

## PLAN document location

```
infra/plans/PLAN_NN_<phase-name>.md
```

Example: `PLAN_06_oauth_secret_manager.md`. 1:1 mapping with ADR Phase.

References (do not modify):
- `docs/context/decisions.md` — ADR canonical doc (docs branch)
- `docs/context/architecture.md` — 4-layer data flow
- `docs/context/MAP.md` — folder structure rule

---

## Agent invocation format

```
[ORCHESTRATOR → TEST_WRITER]
Phase: ADR-019 Phase 6
Goal: verify 3 Google OAuth secrets + lifecycle.ignore_changes
Test location: infra/tests/oauth_secrets.bats + terraform plan assertions

[ORCHESTRATOR → DEVELOPER]
Failing tests: <test ID list>
Implementation target: infra/terraform/main.tf "google_secret_manager_secret.google_oauth_*"
Constraint: placeholder resources require lifecycle ignore_changes (SECURITY_AUDITOR I07)

[ORCHESTRATOR → TESTER]
Run: terraform validate, tflint, bats infra/tests/oauth_secrets.bats
Environment: staging (step before prod apply)

... (same pattern for later steps)
```

---

## Completion criteria

- All tests PASS
- 0 SECURITY FAIL
- IMPACT risk grade recorded (user approval required if HIGH)
- 0 REVIEW Critical
- REPORTER saves `infra/reports/phase{N}_report.md`
- Ready to create the PR via the PR_REPORT skill

---

## Cautions

- ORCHESTRATOR itself does not write code. It only invokes and aggregates.
- Unless the user explicitly says "go faster," go through every step.
- Preserve original FAIL logs (prevents summarization loss between agents).
