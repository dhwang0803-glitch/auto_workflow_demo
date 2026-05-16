# infra — Claude Code branch guide

> Applied alongside the security rules in the root `CLAUDE.md`.

## Module role

**Infrastructure / Deployment / DevOps** — provisions GCP resources
(Terraform), holds deploy / migration bash scripts, and the operational
runbooks.

Sole owner of cross-module ops work:
- `API_Server`, `Database`, `Execution_Engine` share Cloud Run / Cloud
  SQL / Secret Manager / VPC, all provisioned from this branch
- `.github/workflows/**` (CI/CD) lives at the repo root physically but is
  **owned by the infra branch** (the path is fixed by GitHub)
- Single-module operational files (e.g. `API_Server/Dockerfile`) stay in
  the module's own branch (memory exception rule)

---

## Related docs

| Doc | Contents |
|------|----------|
| `infra/docs/README.md` | 3-stage `main → development → release` deploy + secret R/W patterns |
| `infra/docs/README_oauth.md` | Google OAuth client registration & secret injection |
| `docs/context/decisions.md` — ADR-018 | Adoption of Cloud SQL + Secret Manager |
| `docs/context/decisions.md` — ADR-019 | Google OAuth2 / Workspace node integration |
| `docs/context/decisions.md` — ADR-020 | Absence of tfstate remote backend, public-IP policy |
| `docs/context/decisions.md` — ADR-021 | (pending) Worker deployment path — Cloud Run Worker Pools vs Cloud Tasks vs GKE |

ADRs are edited only on the `docs` branch. infra PRs only link to them.

---

## File-location rules (MANDATORY)

```
infra/
├── terraform/                   ← *.tf, modules/, environments/ (Terraform convention)
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
│   ├── README.md                ← 3-stage main → development → release deploy
│   └── README_oauth.md          ← Google OAuth secret injection
├── agents/                      ← infra TDD-cycle 9-agent instructions
│                                  (ORCHESTRATOR / TEST_WRITER / DEVELOPER / TESTER /
│                                   REFACTOR / REVIEW / SECURITY_AUDITOR /
│                                   IMPACT_ASSESSOR / REPORTER)
├── plans/                       ← PLAN_NN_*.md (1:1 with ADR Phases)
├── reports/                     ← TDD-cycle reports (REPORTER output)
├── tests/                       ← bats + terraform validate/plan + tflint/checkov
└── config/                      ← (if needed) ops-level config
```

**Owned by infra but kept at the repo root**:
- `.github/workflows/*.yml` — required by GitHub
- `.dockerignore` — docker build context root
- `_claude_templates/CLAUDE_infra.md` — the template hub itself

---

## Deployment flow (branch model)

```
main  ──(PR merge)──▶  main
                        │
                        ├─ ff-only push ▶  development  ──▶ staging Cloud Run (GH Actions staging-deploy)
                        │
                        └─ ff-only push ▶  release      ──▶ prod Cloud Run    (GH Actions release-deploy)
```

- `main` is the default development branch. Feature PRs target `main`.
- `development` / `release` are **pointer branches that trigger
  deployment**. No development happens on them; only fast-forward pushes
  are allowed.
- Ruleset: `development` and `release` keep only the `deletion` +
  `non_fast_forward` rules (the `pull_request` rule has been removed on
  purpose to allow ff-only pushes).
- staging applies on PR merge → `development` push; prod applies on
  `release` push.

---

## Environment-naming rules (MANDATORY)

- Every Terraform command **must include
  `-var-file=environments/<env>.tfvars`**. Never rely on defaults
  (prevents prod misapplication).
- `<env>` ∈ `{staging, prod}`. No other names allowed.
- Every resource name carries the `${var.environment}` suffix
  (`auto-workflow-staging`, `db-password-prod`, …). Terraform does not
  enforce this — keep it consistent manually when adding resources.
- staging and prod share **the same GCP project** (`autoworkflowdemo`)
  (memory: `project_gcp_project_strategy`). IAM isolation via project
  separation is therefore not possible — rely on resource-name
  isolation.

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

## Running

```bash
# Terraform
cd infra/terraform && terraform init
terraform plan  -var-file=environments/staging.tfvars   # always plan first
terraform apply -var-file=environments/staging.tfvars

# Database migration (via Cloud SQL Auth Proxy)
bash infra/scripts/migrate_via_proxy.sh staging

# Inject OAuth secret
bash infra/scripts/inject_oauth_secrets.sh staging /path/to/client_secret.json

# E2E node run
bash infra/scripts/run_e2e_workspace_node.sh staging <cred_id> gmail_send '{...}'
```

---

## Terraform-apply rules (MANDATORY)

1. **No apply without `terraform plan` first.** Eyeball the plan before
   applying.
2. **staging first, prod second.** Never apply the same change to prod
   ahead of staging.
3. **No `--auto-approve` on prod.** Always go through the interactive
   prompt.
4. **Get user approval when the plan includes a destroy.** Anything that
   could be deleted by accident is highlighted in red in the plan
   output.
5. **`tfstate` lives locally only** (ADR-020). Back up
   `infra/terraform/terraform.tfstate` before/after major changes. If
   we adopt a remote backend, update the ADR.
6. **Never commit real `*.tfvars`**. `.gitignore` covers them — share
   only the structure via `*.tfvars.example`.

---

## Destroy protocol

Destroying prod resources is forbidden as a rule; destroys happen only
on staging.

- Resources with `var.deletion_protection = true` refuse destroy from
  Terraform → **that is correct**; do not turn it off.
- After destroying staging and recreating, releasing the
  serverless-ipv4 VPC peering takes **up to 45 min** before
  re-allocation succeeds (verified in a prior session). Plan accordingly.
- After deleting a Cloud SQL instance, recreating with the same name
  requires **at least one week** (GCP constraint). Destroying without
  renaming blocks recreation.
- Secret Manager secrets enter a 30-day soft-delete state on destroy —
  the previous value can be recovered but doing so is not recommended.

---

## cloud-sql-proxy convention

- Windows local dev: `.tmp/cloud-sql-proxy.exe` (relative to repo root).
  Non-Windows: `.tmp/cloud-sql-proxy`.
- `.tmp/` is git-ignored. Never commit the binary.
- Scripts look up `REPO_ROOT/.tmp/cloud-sql-proxy[.exe]` in that order
  — see `infra/scripts/run_e2e_workspace_node.sh`.
- Default ports: local migration `5433`, E2E runner `15434`
  (5432 conflicts with Hyper-V; 5433 is reserved for migrations —
  memory: `reference_gcp_terraform_gotchas`).

---

## Artifact Registry image-tag convention

```
asia-northeast3-docker.pkg.dev/autoworkflowdemo/auto-workflow/<service>:<tag>
```

- `<service>`: `api` (API_Server), `worker` (Execution_Engine), `agent`
  (future).
- `<tag>`: prefer `<feature-slug>-<git-sha7>` (e.g.
  `logging-fix-632d8f8`). **No `latest`** (breaks rollback tracking).
- Only the GH Actions service account may push images. Local
  `docker push` is allowed only for debug with a temporary tag
  (`debug-YYYYMMDD-HHMM`).
- Updates to `image = "...:tag"` in `cloud_run.tf` go through an **infra
  PR**. App-code PRs do not change the image tag.

---

## Observability / log-query patterns

```bash
# Cloud Run service logs (last 1h)
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="api-staging"' \
  --limit=50 --freshness=1h --format='value(timestamp,severity,textPayload)'

# Specific logger (e.g. api_server.email)
gcloud logging read \
  'resource.type="cloud_run_revision" AND jsonPayload.logger="api_server.email"' \
  --limit=20 --freshness=1h

# Cloud SQL query log (pgaudit)
gcloud logging read \
  'resource.type="cloudsql_database" AND resource.labels.database_id~"auto-workflow-staging"' \
  --limit=30 --freshness=30m
```

- When investigating an outage, **reproduce on staging first** with the
  same query.
- `uvicorn` configures only the `uvicorn.*` loggers — if app loggers
  (`api_server.*`) do not appear in Cloud Logging, look for a missing
  `logging.basicConfig` (postmortem: PR #a633076).

---

## Security notes (MANDATORY)

1. **Never let secrets touch stdout**:
   - Write: `echo -n "$value" | gcloud secrets versions add ... --data-file=-`
   - Read: capture into a shell var, `val="$(gcloud secrets versions
     access ...)"`. Never leak via argv / logs.
   - Details: the "Developer workstation hygiene" section of
     `infra/docs/README.md`.
2. **Never commit real `*.tfvars`**: keep them in `.gitignore`; commit
   only `*.tfvars.example`.
3. **Never commit `tfstate`**: local only. No remote backend
   (ADR-020).
4. **Distinguish placeholders**: Fernet / JWT secrets are
   `REPLACE_ME_…` for local validation; real keys live in GH Secrets +
   Secret Manager.
5. **No prod destroy**: keep `deletion_protection = true`. Destroys
   only on staging.
6. **GH Actions secrets reach steps only as env vars** — never
   `echo ${{ secrets.X }}` inside a `run:` step.

Mechanical checks are run from `infra/agents/SECURITY_AUDITOR.md`
(rules I01–I10).

---

## Interfaces

- **Upstream**: Dockerfiles / migration SQL in the
  `API_Server` / `Database` / `Execution_Engine` branches (infra
  builds & deploys them)
- **Downstream**: GCP resources (Cloud Run, Cloud SQL, Secret Manager,
  Artifact Registry, VPC)

---

## PR scope rules

- Terraform changes: infra branch PR, standalone.
- Dockerfile changes: in the **owning module branch**
  (API_Server / Execution_Engine), not infra.
- `.github/workflows/**` changes: infra branch PR (kept at the repo
  root).
- `docs/context/**` (ADR, MAP, architecture): on the **docs branch**.
  Do not edit from infra.
- Cross-branch impact (e.g. a Terraform schema change that introduces
  a new env var for API_Server): call it out in the PR's "Impact
  Assessment" section and split the downstream branch into its own PR.
