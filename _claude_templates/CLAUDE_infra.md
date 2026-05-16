# infra — Claude Code branch guide

> Applied alongside the root `CLAUDE.md` security rules.

## Module role

**Infrastructure / Deployment / DevOps** — provisions GCP resources (Terraform),
deploy / migration bash scripts, and operational runbooks.

Sole owner of cross-module operations work:
- This branch provisions Cloud Run / Cloud SQL / Secret Manager / VPC shared across `API_Server` · `Database` · `Execution_Engine`
- `.github/workflows/**` (CI/CD) physically lives at the root but **infra owns it** (cannot move — GitHub-required path)
- Operational files that belong to a single module (e.g., `API_Server/Dockerfile`) stay in that module branch (memory exception rule)

---

## Related documents

| Document | Contents |
|----------|----------|
| `infra/docs/README.md` | main → development → release 3-stage deploy procedure + secret R/W pattern |
| `infra/docs/README_oauth.md` | Google OAuth client registration and secret-injection procedure |
| `docs/context/decisions.md` — ADR-018 | Adoption of Cloud SQL + Secret Manager |
| `docs/context/decisions.md` — ADR-019 | Google OAuth2 / Workspace node integration |
| `docs/context/decisions.md` — ADR-020 | Absence of tfstate remote backend, public-IP policy |
| `docs/context/decisions.md` — ADR-021 | (pending) Worker deployment path — Cloud Run Worker Pools vs Cloud Tasks vs GKE |

ADRs themselves are edited only on the `docs` branch. infra-branch PRs only link to them.

---

## File layout rules (MANDATORY)

```
infra/
├── terraform/                   ← *.tf, modules/, environments/ (Terraform conventions)
│   ├── main.tf
│   ├── cloud_run.tf
│   ├── network.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── versions.tf
│   └── environments/
│       ├── staging.tfvars.example
│       └── prod.tfvars.example
├── scripts/                     ← deploy / migration bash
│   ├── migrate_via_proxy.sh
│   ├── inject_oauth_secrets.sh
│   └── run_e2e_workspace_node.sh
├── docs/                        ← runbooks
│   ├── README.md                ← main → development → release 3-stage deploy
│   └── README_oauth.md          ← Google OAuth secret injection procedure
├── agents/                      ← instructions for the 9 infra-TDD-cycle agents
│                                  (ORCHESTRATOR / TEST_WRITER / DEVELOPER / TESTER /
│                                   REFACTOR / REVIEW / SECURITY_AUDITOR /
│                                   IMPACT_ASSESSOR / REPORTER)
├── plans/                       ← PLAN_NN_*.md (1:1 mapping to ADR Phases)
├── reports/                     ← TDD-cycle result reports (REPORTER output)
├── tests/                       ← bats + terraform validate/plan + tflint/checkov
└── config/                      ← (if needed) ops-level configuration
```

**Owned by infra but kept at the root path**:
- `.github/workflows/*.yml` — required by GitHub
- `.dockerignore` — docker build context root
- `_claude_templates/CLAUDE_infra.md` — the template hub itself

---

## Deploy flow (branch model)

```
main  ──(PR merge)──▶  main
                        │
                        ├─ ff-only push ▶  development  ──▶ staging Cloud Run (GH Actions staging-deploy)
                        │
                        └─ ff-only push ▶  release      ──▶ prod Cloud Run    (GH Actions release-deploy)
```

- `main` is the default for development. Feature PRs use `main` as base.
- `development` / `release` are **pointer branches for deployment triggers**. No development on them. Only fast-forward push allowed.
- Ruleset: `development` and `release` keep only `deletion` + `non_fast_forward` rules (pull_request rules removed — intentionally removed to allow ff-only push).
- staging apply is triggered by PR merge → development push; prod apply by release push.

---

## Environment-distinction rules (MANDATORY)

- Terraform commands **always specify `-var-file=environments/<env>.tfvars`**. Defaults are forbidden (prevents accidental prod application).
- `<env>` ∈ `{staging, prod}`. No other names allowed.
- All resource names include the `${var.environment}` suffix (`auto-workflow-staging`, `db-password-prod`, …). Terraform does not enforce this convention, so adhere manually when writing new resources.
- staging and prod share the **same GCP project** (`autoworkflowdemo`) (memory: `project_gcp_project_strategy`). Project-based IAM separation is unavailable — isolation by resource name.

---

## Tech stack

```hcl
# Terraform
terraform { required_version = ">= 1.6" }
provider "google" { ... }
resource "google_cloud_run_v2_service" "api" { ... }
resource "google_sql_database_instance" "pg" { ... }
```

```bash
# scripts — gcloud + terraform + cloud-sql-proxy
gcloud secrets versions access latest --secret=...
terraform apply -var-file=environments/staging.tfvars
cloud-sql-proxy <instance-connection-name>
```

---

## Run

```bash
# Terraform
cd infra/terraform && terraform init
terraform plan  -var-file=environments/staging.tfvars   # always plan first
terraform apply -var-file=environments/staging.tfvars

# Migrations (via Cloud SQL Auth Proxy)
bash infra/scripts/migrate_via_proxy.sh staging

# OAuth secret injection
bash infra/scripts/inject_oauth_secrets.sh staging /path/to/client_secret.json

# E2E node run
bash infra/scripts/run_e2e_workspace_node.sh staging <cred_id> gmail_send '{...}'
```

---

## Terraform-apply rules (MANDATORY)

1. **No apply without `terraform plan`**. Visually confirm the plan output before applying.
2. **staging first, then prod**. Never apply the same change to prod first.
3. **No `--auto-approve` on prod**. Always confirm via interactive prompt.
4. **If destroy targets exist, user approval is required** — resources that could be removed accidentally are highlighted in red in the plan output.
5. **`tfstate` lives only locally** (ADR-020). Backing up `infra/terraform/terraform.tfstate` before/after work is recommended. Update the ADR when adopting a remote backend.
6. **Do not commit `*.tfvars` real values**. Include them in `.gitignore`. Share structure only via `*.tfvars.example`.

---

## Destroy protocol

prod-resource destroy is forbidden in principle. Run only on staging.

- Resources with `var.deletion_protection = true` are refused destroy by Terraform → **correct state**. Leave it alone; keep prod resources.
- After staging destroy and recreation, the serverless-ipv4 VPC peering is released → reallocation can take **up to 45 min** (confirmed in a prior session). Make sure to leave enough slack for re-apply timing.
- Recreating a Cloud SQL instance with the same name after deletion requires **at least 1 week** of waiting (GCP constraint). Destroying without changing the name will block recreation.
- Secret Manager secrets soft-delete and retain for 30 days on destroy — prior values can be restored on recreate but it is not recommended.

---

## cloud-sql-proxy conventions

- Local Windows dev: `.tmp/cloud-sql-proxy.exe` (relative to repo root). Non-Windows: `.tmp/cloud-sql-proxy`.
- The `.tmp/` directory is `.gitignore`d. Do not commit binaries directly.
- Scripts probe `REPO_ROOT/.tmp/cloud-sql-proxy[.exe]` in order — see `infra/scripts/run_e2e_workspace_node.sh`.
- Default ports: local migration `5433`, E2E runner `15434` (5432 conflicts with Hyper-V; 5433 is migration-only — memory: `reference_gcp_terraform_gotchas`).

---

## Artifact Registry image-tag convention

```
asia-northeast3-docker.pkg.dev/autoworkflowdemo/auto-workflow/<service>:<tag>
```

- `<service>`: `api` (API_Server), `worker` (Execution_Engine), `agent` (future).
- `<tag>`: `<feature-slug>-<git-sha7>` (e.g., `logging-fix-632d8f8`) recommended. `latest` is forbidden (no rollback tracking).
- Image push permission is granted only to the GH Actions service account. Local docker push is allowed only as a debug-temporary tag (`debug-YYYYMMDD-HHMM`).
- Changes to `image = "...:tag"` in `cloud_run.tf` go through **infra PRs**. Do not change the image tag in app-code PRs.

---

## Observability / log-query patterns

```bash
# Cloud Run service logs (last 1h)
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="api-staging"' \
  --limit=50 --freshness=1h --format='value(timestamp,severity,textPayload)'

# Specific logger (e.g., api_server.email)
gcloud logging read \
  'resource.type="cloud_run_revision" AND jsonPayload.logger="api_server.email"' \
  --limit=20 --freshness=1h

# Cloud SQL query log (pgaudit)
gcloud logging read \
  'resource.type="cloudsql_database" AND resource.labels.database_id~"auto-workflow-staging"' \
  --limit=30 --freshness=30m
```

- When investigating prod issues, **always try to reproduce on staging first** with the same query.
- `uvicorn` only configures the `uvicorn.*` logger — if app loggers (`api_server.*`) are missing from Cloud Logging, `logging.basicConfig` is missing (post-mortem of PR #a633076).

---

## Security notes (MANDATORY)

1. **No secret R/W via stdout**:
   - Write: `echo -n "$value" | gcloud secrets versions add ... --data-file=-`
   - Read: capture into a shell variable with `val="$(gcloud secrets versions access ...)"`; do not expose via argv/logs
   - Details: `infra/docs/README.md` "Developer workstation hygiene" section
2. **Do not commit tfvars real values**: `*.tfvars` is in `.gitignore`; commit only `*.tfvars.example`
3. **Do not commit tfstate**: local only. No remote backend yet (ADR-020)
4. **Distinguish placeholders**: Fernet/JWT secrets are `REPLACE_ME_…` for local validation; real keys live in GH Secret + Secret Manager
5. **No prod destroy**: keep `deletion_protection = true`. Destroy only on staging.
6. **GH Actions secrets are injected via env only**; never `echo ${{ secrets.X }}` inside a `run:` step.

Mechanical checks live in `infra/agents/SECURITY_AUDITOR.md` (rules I01–I10) executor.

---

## Interfaces

- **Upstream**: Dockerfile / migration SQL from `API_Server` / `Database` / `Execution_Engine` branches (infra builds and deploys them)
- **Downstream**: GCP resources (Cloud Run, Cloud SQL, Secret Manager, Artifact Registry, VPC)

---

## PR scope rules

- Terraform changes: infra-branch standalone PR
- Dockerfile changes: in the **owning module branch** (API_Server / Execution_Engine). Not infra.
- `.github/workflows/**` changes: infra-branch PR (kept at root path)
- `docs/context/**` (ADR, MAP, architecture): **docs branch** PR. infra must not modify.
- Changes with cross-branch impact (e.g., Terraform schema change adding API_Server env): note it explicitly in the PR body's `Downstream impact assessment` section + split downstream-branch PRs.
