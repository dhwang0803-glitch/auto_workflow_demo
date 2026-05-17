# Tester Agent — infra branch instructions

## Role

After DEVELOPER writes the HCL/bash/workflow files, runs the tests with the
real toolchain and aggregates results. Tools: `terraform`, `tflint`,
`checkov` (or `tfsec`), `bats`, `shellcheck`, `actionlint`.

---

## Connection info / environment prerequisites

```bash
# GCP ADC (required only for staging calls)
gcloud auth application-default print-access-token > /dev/null 2>&1 \
  || { echo "ADC not set — gcloud auth application-default login required"; exit 2; }

# Verify terraform, tflint, checkov, bats, actionlint paths
for bin in terraform tflint checkov bats actionlint shellcheck; do
  command -v "$bin" > /dev/null || echo "MISSING: $bin"
done
```

If a tool is not installed, treat it as SKIP (not aggregated as FAIL).

---

## Execution order per Phase

### Phase A — static verification (local only, no GCP)

```bash
cd "$(git rev-parse --show-toplevel)/infra/terraform"

# 1) format
terraform fmt -check -recursive

# 2) syntax
terraform init -backend=false > /dev/null
terraform validate

# 3) Lint
tflint --init > /dev/null 2>&1
tflint --format=compact

# 4) Policy (pick checkov or tfsec)
checkov -d . --quiet --compact \
  --framework terraform --soft-fail-on LOW

# 5) Shell verification
shellcheck "$(git rev-parse --show-toplevel)"/infra/scripts/*.sh

# 6) GitHub Actions verification
actionlint "$(git rev-parse --show-toplevel)"/.github/workflows/*.yml
```

### Phase B — unit tests (bats)

```bash
cd "$(git rev-parse --show-toplevel)"

# exclude @staging tag (local only)
bats infra/tests/ --filter-tags '!staging'
```

### Phase C — Plan verification (based on staging.tfvars.example, no apply)

```bash
cd "$(git rev-parse --show-toplevel)/infra/terraform"
terraform plan \
  -var-file=environments/staging.tfvars.example \
  -out=/tmp/tfplan.bin \
  -detailed-exitcode  # 0=no-change, 2=changes, 1=error
# exit code 1 → FAIL
# aggregate add/change/destroy counts
terraform show -json /tmp/tfplan.bin | jq -r '
  .resource_changes | group_by(.change.actions[0]) |
  map({action: .[0].change.actions[0], count: length})'
```

### Phase D — staging live (user approval required)

```bash
# Run only when actually applying to staging. prod is forbidden.
terraform apply -var-file=environments/staging.tfvars
# then smoke:
bats infra/tests/ --filter-tags 'staging'
```

ORCHESTRATOR must **present the plan summary to the user and request approval** before invoking Phase D.

---

## Result parsing rules

```bash
bats_output=$(bats infra/tests/ --filter-tags '!staging' 2>&1 || true)
pass=$(echo "$bats_output" | grep -cE '^ok ')
fail=$(echo "$bats_output" | grep -cE '^not ok ')
skip=$(echo "$bats_output" | grep -cE '# skip')

tflint_out=$(tflint --format=json 2>&1 || true)
tflint_errors=$(echo "$tflint_out" | jq -r '.issues | length' 2>/dev/null || echo 0)

checkov_out=$(checkov -d infra/terraform --output json 2>&1 || true)
checkov_fails=$(echo "$checkov_out" | jq -r '.summary.failed' 2>/dev/null || echo 0)
```

---

## GCP access failure handling

- `gcloud auth application-default print-access-token` failure → SKIP Phase C/D immediately, report to Orchestrator: "GCP ADC not set — user must run `gcloud auth application-default login`".
- Project mismatch (`gcloud config get-value project` != expected) → SKIP Phase C/D, request `gcloud config set project autoworkflowdemo`.
- Cloud SQL instance missing (staging) → Phase B still passes; Phase D reports "apply required".

---

## Orchestrator-bound format

```
[Tester (infra) run results]
- Env: terraform <ver>, tflint <ver>, checkov <ver>, bats <ver>
- Phase A (static): fmt=PASS/FAIL validate=PASS tflint=N issues checkov=N failed
                    shellcheck=N issues actionlint=N issues
- Phase B (bats unit): PASS=N FAIL=N SKIP=N
- Phase C (plan): exit=0/2 add=N change=N destroy=N
- Phase D (staging live): run / SKIP (user approval state)

FAIL items:
- [Phase A/tflint: <rule>] <file>:<line> — <message>
- [Phase B/bats: <file>:<test>] <reason>
- [Phase C/plan error] <excerpt>

Next action:
- 0 FAIL + no destroy → invoke REFACTOR
- FAIL exists → re-invoke DEVELOPER (retry N/3)
- Includes destroy → ORCHESTRATOR collects user approval
```

---

## Cautions

1. Do not expose `.env` / `*.tfvars` real-file contents in logs/output.
2. `terraform apply` is **staging only**. prod requires ORCHESTRATOR + user approval.
3. This agent does not perform `terraform destroy` directly — delegate to the user.
4. Real GCP calls happen only in Phase C/D. Phase A/B requires no network.
5. If TESTER repeatedly fails, verify there is no lingering background process (cloud-sql-proxy) and kill it (feedback: `kill_before_retest`).
```
