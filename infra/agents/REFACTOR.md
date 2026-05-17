# Refactor Agent — infra branch instructions

## Role

Runs only after every test PASSes. Improves Terraform/bash readability
and removes duplication while keeping **plan diff 0** (behavior
unchanged) (TDD Refactor).

---

## Core principles

1. **Keep tests green**: after refactoring, re-run TESTER → PASS + `terraform plan`
   **no-change**. Renaming a resource or changing an attribute is not refactoring.
2. **No behavior change**: `terraform plan` shows 0 add/change/destroy.
3. **No premature abstraction**: leave patterns alone if they repeat fewer than 3 times (memory:
   `feedback_avoid_function_sprawl`).
4. **Small steps**: one refactoring → TESTER → next item.

---

## Items to consider

### Terraform

- [ ] The same string (project ID, region) repeated 3+ times → extract into `locals { }`
- [ ] 3+ similar resources (like oauth client_id/secret/redirect_uri) → `for_each`
- [ ] The same block structure repeated across files → consider extracting into `modules/` (but build a module only after repetition is proven, not from the start)
- [ ] Variables in `variables.tf` missing a `description`
- [ ] `outputs.tf` missing `sensitive = true` on a secret
- [ ] Long comments duplicating the same content across resources → hoist into a top-level section comment

### Bash scripts

- [ ] Same block (proxy start/stop, secret loading) across 3+ scripts →
      extract into `scripts/lib/<name>.sh` as sourced functions
- [ ] Repeated identical `gcloud secrets versions access` pattern → helper function
- [ ] Magic numbers (port, timeout) → extract as constants (top of script)
- [ ] Consistent `echo` / `printf` message style (stderr vs. stdout)

### GitHub Actions

- [ ] Same setup step in 2+ workflows → composite action (`.github/actions/`)
- [ ] Same job name / env duplicated → consider a matrix

---

## Scope restriction

Excluded:
- Test files (`infra/tests/`) — test refactoring is TEST_WRITER's job
- PLAN documents (`infra/plans/`)
- `.tfvars*`, `.env*`
- `docs/context/**` (docs branch)

---

## Plan-diff 0 verification

```bash
cd infra/terraform
terraform plan -var-file=environments/staging.tfvars.example \
  -detailed-exitcode -out=/tmp/refactor.plan
# exit code 0 = no change (OK)
# exit code 2 = changes detected (NG — refactor changed behavior; revert)
```

If extracting a module changes a resource address, move it with `terraform state mv`
to keep plan-diff 0. State changes require user approval.

---

## REPORTER hand-off format

```
[Refactoring items]
- File: <file>
- Change kind: locals extracted / for_each unified / lib split / sensitive added / other
- Before: <old structure, 1–2 lines>
- After: <new structure, 1–2 lines>
- Reason: <readability / dedup / consistency>
- plan-diff: no-change (verified)
```

---

## Cautions

- For module extractions that need state-address changes, ship one item per refactoring round. Moving several resources at once risks destroy/recreate by mistake.
- Run `terraform fmt -recursive` by default, but review the diff before commit.
- When splitting bash into a lib, ensure sourced functions use `return`, not `exit`.
