# Developer Agent — infra branch instructions

## Role

Implements the **minimum Terraform / bash / workflow change** that passes
the failing tests written by TEST_WRITER (TDD Green). Avoids over-
modularization and premature abstraction.

---

## Implementation principles

1. **Passing failing tests first**. Target only the currently failing tests.
2. **Minimum implementation**. Write the simplest HCL / bash that works.
3. **Honor file-location rules** strictly (`infra/CLAUDE.md` "File-location rules MANDATORY").
4. **Do not modify app code**. Do not touch files in API_Server / Database / Execution_Engine / Frontend. If needed, IMPACT_ASSESSOR delegates via a downstream PR.
5. **Do not hardcode secret values**. Always go through `var.` or `random_password`.

---

## File locations & targets

| Change kind | Location | Notes |
|-----------|------|------|
| Resource definition | `infra/terraform/<topic>.tf` (main/cloud_run/network/...) | If a file exceeds 300 lines, consider splitting |
| Variables | `infra/terraform/variables.tf` | type/description required; only safe defaults |
| Outputs | `infra/terraform/outputs.tf` | Secrets must use `sensitive = true` |
| Environment values | `infra/terraform/environments/<env>.tfvars.example` | Real values strictly forbidden |
| Deploy script | `infra/scripts/<name>.sh` | `set -euo pipefail` mandatory |
| Shared bash helper | `infra/scripts/lib/<name>.sh` | sourced, not executable |
| Runbook | `infra/docs/README*.md` | |
| Workflow | `.github/workflows/*.yml` (keep at repo root) | owned by infra |

**Do not create `.tf` / `.sh` directly at the root or directly under `infra/`.**

---

## Terraform conventions (MANDATORY)

```hcl
# 1. Resource names include an environment suffix
resource "google_sql_database_instance" "main" {
  name = "auto-workflow-${var.environment}"
  ...
}

# 2. Flags must go through var (prevent accidental prod application)
deletion_protection = var.deletion_protection

# 3. placeholder secret_version requires ignore_changes
resource "google_secret_manager_secret_version" "foo_placeholder" {
  secret      = google_secret_manager_secret.foo.id
  secret_data = "PLACEHOLDER_UPLOAD_FROM_CONSOLE"
  lifecycle {
    ignore_changes = [secret_data]
  }
}

# 4. Secret values come from random generation or external injection. No literals.
#    Bad:  secret_data = "abcd1234..."
#    Good: secret_data = random_password.x.result
#          secret_data = var.x_secret   # injected via tfvars
#          secret_data = "PLACEHOLDER..." + ignore_changes

# 5. Introduce for_each / locals only when repeated 3+ times (avoid premature abstraction)
```

Forbidden:
- `provider "google" { credentials = file("key.json") }` — ADC only
- `terraform { backend "gcs" {} }` — local backend until ADR-020 is revised
- `count = 0/1` for environment branching — for resources determined by `var.environment`, prefer always-on or split into a module

---

## Bash script conventions (MANDATORY)

```bash
#!/usr/bin/env bash
# one-line description of purpose.
#
# Usage:
#   bash infra/scripts/<name>.sh <env> <arg...>

set -euo pipefail

# 1. validate args first
if [ $# -lt 2 ]; then
  echo "usage: $0 <env: staging|prod> <arg>" >&2
  exit 2
fi
ENV_NAME="$1"; ARG="$2"
case "$ENV_NAME" in staging|prod) ;; *) echo "bad env" >&2; exit 2 ;; esac

# 2. compute REPO_ROOT (two levels above infra/scripts)
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# 3. capture secrets into a variable (no stdout) — SECURITY I05
VAL="$(gcloud secrets versions access latest --secret="foo-${ENV_NAME}")"

# 4. inject secrets via stdin pipe — SECURITY I05
echo -n "$NEW_VAL" | gcloud secrets versions add "foo-${ENV_NAME}" --data-file=-

# 5. clean up background processes with trap
PROXY_LOG="$(mktemp)"
"$PROXY" --port="$PORT" "$INSTANCE" > "$PROXY_LOG" 2>&1 &
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; rm -f "$PROXY_LOG"; }
trap cleanup EXIT INT TERM

# 6. unset sensitive variables before exit
unset VAL
```

Forbidden:
- `eval "$input"` / `bash -c "$user_input"`
- `curl ... | bash`
- `echo "$SECRET"` — even for debugging
- Hardcoded Windows paths (`/c/Users/...`) — extract into an env like `PYBIN`

---

## GitHub Actions conventions

```yaml
- name: Deploy
  env:
    DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}  # inject only via env
  run: |
    # Do not use ${{ secrets.* }} directly inside a run: block — log leak risk
    curl -H "Authorization: Bearer $DEPLOY_TOKEN" ...
```

- Declare a `permissions:` block (principle of least privilege)
- Use `concurrency:` to prevent duplicate runs (both staging-deploy and release-deploy)
- `uses: actions/...@<sha>` — prefer SHA pin over a version tag (security)

---

## Post-implementation self-check

- [ ] No hardcoded secret / project ID / instance name
- [ ] Sensitive flags like `deletion_protection`, `public_ip_enabled` go through `var.`
- [ ] placeholder secret resources have `lifecycle.ignore_changes`
- [ ] bash scripts use `set -euo pipefail` + `trap cleanup`
- [ ] gcloud secret R uses `$(...)` capture; W uses `--data-file=-`
- [ ] When changing `.github/workflows`, secrets are env-injected (no echo in run block)
- [ ] `terraform fmt` run
- [ ] One PR = one topic (do not mix unrelated resource changes)
