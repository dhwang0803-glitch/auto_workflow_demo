# Test Writer Agent — infra branch instructions

## Role

Before an infra change (Terraform/bash/GitHub Actions), **writes the
failing tests first** (TDD Red). Does not handle Python code.

---

## Test-writing principles

1. Even without an implementation (.tf, .sh), express the **expected state** as tests first.
2. Each test verifies exactly one resource / rule.
3. The failure message must identify which resource/flag is missing.
4. Real GCP API calls happen only on staging. prod is read-only verification.
5. Tests depending on external state (presence of GCP resources) are split via `bats` tags (`@staging` / `@local`).

---

## Test file location

```
infra/tests/
├── terraform_plan.bats          ← static assertions based on terraform validate/plan
├── tflint_rules.bats            ← tflint configuration and rule checks
├── checkov_policies.bats        ← checkov / tfsec policy compliance
├── scripts_smoke.bats           ← bash script usage / arg validation
├── workflows_lint.bats          ← .github/workflows/*.yml syntax / actionlint
└── fixtures/                    ← mock tfvars, test JSON, etc.
```

Do not mix into the repo-root `tests/`. Always `infra/tests/`.

---

## Per-test-type patterns

### A. terraform plan assertion (bats + jq)

```bash
# infra/tests/terraform_plan.bats
setup() {
  cd "$(git rev-parse --show-toplevel)/infra/terraform"
  terraform init -backend=false > /dev/null
}

@test "cloud sql instance exists for staging" {
  run terraform plan -var-file=environments/staging.tfvars.example \
    -out=/tmp/tfplan.bin
  [ "$status" -eq 0 ]
  terraform show -json /tmp/tfplan.bin > /tmp/tfplan.json
  run jq -e '.planned_values.root_module.resources[] |
    select(.type=="google_sql_database_instance" and
           .values.name=="auto-workflow-staging")' /tmp/tfplan.json
  [ "$status" -eq 0 ]
}

@test "placeholder secrets have ignore_changes lifecycle" {
  run jq -e '.resource_changes[] |
    select(.address | contains("placeholder")) |
    select(.change.actions[0] == "create")' /tmp/tfplan.json
  # An existing placeholder must be a "no-op" on re-apply because of ignore_changes
  [ "$status" -eq 0 ]
}
```

### B. tflint / checkov / tfsec (policy)

```bash
@test "tflint passes with zero issues" {
  cd "$(git rev-parse --show-toplevel)/infra/terraform"
  tflint --init > /dev/null
  run tflint --format=compact
  [ "$status" -eq 0 ]
}

@test "checkov blocks public cloud sql ipv4" {
  run checkov -d "$(git rev-parse --show-toplevel)/infra/terraform" \
    --check CKV_GCP_11 --quiet --compact
  [ "$status" -eq 0 ]
}
```

### C. bash script (args / usage)

```bash
# infra/tests/scripts_smoke.bats
@test "run_e2e_workspace_node rejects missing args" {
  run bash "$(git rev-parse --show-toplevel)/infra/scripts/run_e2e_workspace_node.sh"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "inject_oauth_secrets uses stdin pipe (no echo of value)" {
  run grep -E 'gcloud secrets versions add .*--data-file=-' \
    "$(git rev-parse --show-toplevel)/infra/scripts/inject_oauth_secrets.sh"
  [ "$status" -eq 0 ]  # enforce stdin pipe (SECURITY_AUDITOR I05)
}
```

### D. GitHub Actions (actionlint)

```bash
@test "staging-deploy workflow passes actionlint" {
  run actionlint "$(git rev-parse --show-toplevel)/.github/workflows/staging-deploy.yml"
  [ "$status" -eq 0 ]
}
```

### E. staging live smoke (@staging tag, optional)

```bash
@test "staging cloud sql accepts proxy connection @staging" {
  # touches real staging GCP — filter out in CI via the @staging tag
  run bash "$(git rev-parse --show-toplevel)/infra/scripts/check_proxy_ready.sh" staging
  [ "$status" -eq 0 ]
}
```

---

## Required test categories (infra scope)

### Terraform consistency
- `terraform validate` integrity
- `terraform plan` add/change counts match expectations
- Resource-name suffix rule per `var.environment`
- Existence of `lifecycle.ignore_changes` on placeholder secret resources

### Policy (tflint / checkov / tfsec)
- No public IP exposure (prod)
- `deletion_protection` goes through a variable (no direct `false` in prod)
- No broad IAM roles (`roles/owner`, `roles/editor`)
- `authorized_networks` default is not `0.0.0.0/0`

### Scripts (bats / shellcheck)
- `set -euo pipefail` present
- `trap cleanup` cleans up background processes
- Secret stdin-pipe pattern (`--data-file=-`)
- Argument validation + usage output

### Workflows (actionlint)
- syntax / job deps / secret reference validity
- No `echo ${{ secrets.* }}` inside `run:` steps

---

## Result-collection format (hand to TESTER)

```
Total tests: X (bats: X, checkov: X, tflint: X, actionlint: X)
PASS: X
FAIL: X
SKIP: X (includes @staging tag)

FAIL list:
- [bats:<file>:<test-name>] <reason>
- [checkov:<CHECK_ID>] <resource address>
```

---

## Cautions

1. **Never expose real secret values on stdout** — even tests must use the `--data-file=-` pattern.
2. Do not read real `.env` / `*.tfvars` files in tests. Use `.example` or `fixtures/` only.
3. Tests must not create/delete GCP resources themselves (destroy requires user approval).
4. If bats / tflint / checkov / actionlint is not installed, ask TESTER to install the dependency → record as SKIP, not FAIL.
