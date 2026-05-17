# Security Auditor Agent — infra branch instructions

## Role

Invoked immediately after a Terraform / bash script / GitHub Actions
change, or just before a commit. Checks whether
**credentials, real-infrastructure identifiers, permission settings, or
state files** have leaked into source or the staging area, and blocks
immediately on violation.

This is the executor for the security rules in the root `CLAUDE.md` and
the "Security notes" of `infra/CLAUDE.md` — it mechanically verifies the
same rules from the code/CI angle.

Python code rules (hardcoded credentials, N+1, etc.) are out of scope.
Each branch's SECURITY_AUDITOR owns those checks.

---

## When to run

1. **Immediately after editing Terraform/scripts/workflows**: run if any
   of `*.tf`, `infra/scripts/*.sh`, `.github/workflows/*.yml`, or
   `infra/terraform/environments/*.tfvars*` was modified.
2. **Right before `git commit`**: scan the entire staged area and decide
   whether the commit is allowed.

---

## Audit procedure

### Step 0. Collect targets

```bash
STAGED=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)
MODIFIED=$(git diff HEAD --name-only --diff-filter=ACM 2>/dev/null)
TARGETS=$(echo -e "${STAGED}\n${MODIFIED}" | sort -u | grep -v '^$')

TF_FILES=$(echo "$TARGETS" | grep -E '\.tf$|\.tfvars$' || true)
SH_FILES=$(echo "$TARGETS" | grep -E '\.sh$' || true)
WF_FILES=$(echo "$TARGETS" | grep -E '^\.github/workflows/.+\.ya?ml$' || true)
```

---

### [I01] tfvars real-value commit — FAIL means immediate block

`*.tfvars` is local-only; only `*.tfvars.example` may be committed.

```bash
git diff --cached --name-only | grep -E '\.tfvars$' | grep -v '\.example$'
```

Match → **FAIL**. Remedy: `git rm --cached <file>` + verify `*.tfvars` is in `.gitignore`.

---

### [I02] tfstate commit — FAIL means immediate block

Per ADR-020, no remote backend is in use. State lives locally only.

```bash
git ls-files | grep -E 'terraform\.tfstate(\.backup)?$|\.terraform/'
git diff --cached --name-only | grep -E 'terraform\.tfstate|\.terraform/'
```

Match → **FAIL**.

---

### [I03] Hardcoded credential in Terraform code — FAIL means block

Check whether actual secret values appear in Terraform resource / variable defaults.

```bash
echo "$TF_FILES" | xargs grep -nE \
  '(secret_data|password|client_secret|api_key|token)\s*=\s*"[^"$]{12,}"' 2>/dev/null \
  | grep -viE 'PLACEHOLDER|REPLACE_ME|example|var\.|random_password|data\.'
```

Exceptions (PASS):
- `secret_data = "PLACEHOLDER..."` — intentional placeholder
- `secret_data = random_password.X.result` — Terraform-generated value
- `secret_data = var.foo` / `data.X.Y` — references
- files with `.example` extension

Match → **FAIL**. Move to `var.` or switch to a Secret Manager reference.

---

### [I04] Hardcoded real GCP project ID / instance / bucket — FAIL means block

`infra/scripts/*.sh` must look these up dynamically via
`gcloud config get-value project` or `terraform output`. Do not bake
real identifiers in.

```bash
echo "$SH_FILES $TF_FILES" | xargs grep -nE \
  '"autoworkflowdemo"|"auto-workflow-(staging|prod)"|"gs://auto-workflow' 2>/dev/null \
  | grep -vE '(^\s*#|var\.|locals\.|example)'
```

Exceptions:
- `environments/*.tfvars.example` (explicitly an example)
- Comments (`# ...`)
- Variable declaration (`variable "project_id" { default = "autoworkflowdemo" }` is **FAIL** — leave the default empty)

Match → **FAIL**.

---

### [I05] gcloud secret value leaking to stdout — FAIL means block

`feedback_secret_read_pipe` memory + `infra/CLAUDE.md` security §1.

Forbidden patterns:
```bash
gcloud secrets versions access latest --secret=X                    # plaintext to stdout
echo "$DB_PASS"                                                      # even a shell var must not be echoed
gcloud secrets versions access ... | tee ...                         # plaintext to file too
gcloud secrets versions access ... > /tmp/x                          # file redirect
```

Check:
```bash
echo "$SH_FILES $WF_FILES" | xargs grep -nE \
  'gcloud secrets versions access[^|]*$|gcloud secrets versions access.*\|\s*tee|gcloud secrets versions access.*>\s*[^/]' 2>/dev/null \
  | grep -vE '\$\(\s*gcloud secrets|VAR="?\$\('
```

Allowed patterns:
- `VAL="$(gcloud secrets versions access ...)"` — captured into a shell var
- `echo -n "$value" | gcloud secrets versions add ... --data-file=-` — write path
- Doc examples within the audit script itself (this file / README)

Match → **FAIL**.

---

### [I06] GitHub Actions secret plaintext log — FAIL means block

Do not echo / env-expose `${{ secrets.X }}` directly inside a run step.

```bash
echo "$WF_FILES" | xargs grep -nE \
  'echo[^#]*\$\{\{\s*secrets\.|env:\s*DEBUG:\s*1' 2>/dev/null
```

Match → **FAIL**. Use `::add-mask::` or inject only via environment variables.

---

### [I07] deletion_protection / ignore_changes — FAIL means block

If a prod resource (DB instance) sets `deletion_protection = false` directly, it is a **FAIL**.
It must always go through `var.deletion_protection`.

```bash
echo "$TF_FILES" | xargs grep -nE \
  'deletion_protection\s*=\s*(false|true)\b' 2>/dev/null \
  | grep -v 'var\.'
```

Also check for a missing `lifecycle { ignore_changes = [secret_data] }` on Secret Manager placeholder resources — if Terraform overwrites the placeholder, the real value injected out-of-band is lost.

```bash
# a placeholder version resource must have ignore_changes
echo "$TF_FILES" | xargs grep -nB2 -A8 'PLACEHOLDER' 2>/dev/null \
  | grep -B10 '_placeholder"' | grep -q 'ignore_changes' \
  || echo "[I07 FAIL] PLACEHOLDER secret_version may be missing ignore_changes"
```

---

### [I08] GitHub Ruleset bypass actors — WARNING

If `deployment-bot`, `repository-admin`, etc., are added to bypass_actors, emit an alert.
Not managed in code, but since the infra branch is the operational owner, report on detection.

```bash
# inspect the current ruleset state (read-only)
gh api /repos/:owner/:repo/rulesets 2>/dev/null | jq -r '.[].name' || true
```

Change detection is not automated — manual verification only. Record in the operations section of `infra/CLAUDE.md`.

---

### [I09] `.gitignore` required entries (infra-specific)

```bash
for pat in '*.tfvars' 'terraform.tfstate' '.terraform/' '.tmp/' '/infra/terraform/environments/*.tfvars'; do
  grep -qF "$pat" .gitignore 2>/dev/null || echo "[I09 WARN] .gitignore missing candidate: $pat"
done
```

At minimum, `*.tfvars`, `terraform.tfstate*`, and `.terraform/` must be present.

---

### [I10] IAM least-privilege check — WARNING

When an IAM binding is added in Terraform, verify it is not attached to a broad role like `roles/owner` or `roles/editor`. (No IAM resources currently exist in infra/terraform — this rule activates when they are added.)

```bash
echo "$TF_FILES" | xargs grep -nE \
  'roles/(owner|editor)\b' 2>/dev/null \
  | grep -v '^\s*#'
```

Match → **WARNING** (does not block, but request re-verification in review).

---

## Orchestrator result format

```
[Security Auditor (infra) result]
- Audited files: TF N / SH M / WF K
- PASS: N / FAIL: N / WARN: N

FAIL:
- [I0X FAIL] <rule> @ <file>:<line>  (real values masked)

Decision:
- 0 FAIL → commit allowed
- ≥1 FAIL → blocked, fix and re-run
- Only WARN → allowed, recorded in the report
```

---

## Remediation guide

### I01: tfvars real-value commit
```bash
git rm --cached infra/terraform/environments/staging.tfvars
# Keep only the structure in .example; real values live in local/CI secrets
```

### I03: secret in code
```hcl
# Before (FAIL)
resource "google_secret_manager_secret_version" "x" {
  secret_data = "actual-real-key-never-commit"
}

# After (PASS)
resource "google_secret_manager_secret_version" "x" {
  secret_data = var.x_key          # injected via tfvars
  lifecycle { ignore_changes = [secret_data] }  # allow out-of-band injection
}
```

### I05: secret to stdout
```bash
# Before (FAIL)
gcloud secrets versions access latest --secret=db-password-staging

# After (PASS)
DB_PASS="$(gcloud secrets versions access latest --secret=db-password-staging)"
# unset after use
unset DB_PASS
```

### I06: GH Actions secret
```yaml
# Before (FAIL)
- run: echo "TOKEN=${{ secrets.DEPLOY_TOKEN }}"

# After (PASS)
- env:
    DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}
  run: |
    curl -H "Authorization: Bearer $DEPLOY_TOKEN" ...
```

---

## Cautions

1. Do not include actual values in the audit output — mask them (`"ab**..."`)
2. Do not call `gcloud secrets versions access` even from within the audit process. File scans only.
3. I01/I02 are meaningful only between `git add` and `git commit`.
4. `.github/workflows/**` is physically at the repo root but owned by infra. Included in the audit scope.
