# Database Deploy — GCP Cloud SQL

> Implements ADR-018. Provisions a Cloud SQL for PostgreSQL 16
> instance and three Secret Manager entries via Terraform.

## Prerequisites (one-time)

1. **Create the GCP project** — separate projects for staging and prod
   are recommended.
   ```bash
   gcloud projects create auto-workflow-staging-xxx --name="Auto Workflow Staging"
   gcloud config set project auto-workflow-staging-xxx
   ```
2. **Link the billing account** — Cloud SQL is not in the free tier.
   Console, or:
   ```bash
   gcloud billing projects link auto-workflow-staging-xxx --billing-account=YOUR-BILLING-ID
   ```
3. **Install the tools**
   - `terraform` >= 1.6
   - `gcloud` CLI — run `gcloud auth application-default login`
4. **Find your public IP** (for dev access):
   <https://whatismyipaddress.com/>

## Create the instance

```bash
cd infra/terraform

# 1. Author tfvars (git-ignored)
cp environments/staging.tfvars.example environments/staging.tfvars
# Edit environments/staging.tfvars:
#   - set project_id to your real GCP project ID
#   - add your IP /32 to authorized_networks

terraform init
terraform plan  -var-file=environments/staging.tfvars
terraform apply -var-file=environments/staging.tfvars
```

The first apply takes 5–8 min (Cloud SQL instance boot + API
enablement).

## Secret R/W patterns

**One-line rule**: never let secret values reach **visible stdout**.
Write via pipe, read by capturing into a shell variable.

### Write — inject real secret values

Terraform creates the credential master key and JWT secret as
**valid-but-placeholder** values (it used to use the plaintext
`REPLACE_ME_*` strings, but the container's Fernet init failed, so
we switched to base64-valid dummies). Real values must be set:

```bash
# Fernet master key (ADR-004) — never print/echo the generated value, pipe straight in
python -c "from cryptography.fernet import Fernet; import sys; sys.stdout.write(Fernet.generate_key().decode())" | \
  gcloud secrets versions add credential-master-key-staging --data-file=-

# JWT signing key (ADR-015)
python -c "import secrets, sys; sys.stdout.write(secrets.token_urlsafe(48))" | \
  gcloud secrets versions add jwt-secret-staging --data-file=-
```

**Caveat**: rotating either of these later breaks decryption of all
stored credentials and invalidates all existing JWTs. On prod, plan
deliberate downtime when rotating.

### Read — reuse a secret value

`gcloud secrets versions access latest --secret=<id>` printed to the
terminal leaves the cleartext in scrollback, shell history, and
agent conversation logs (JSONL). Always `$(...)`-capture into a
shell variable and feed it directly into the next command's env.

```bash
# ❌ Bad — the password ends up in scrollback
gcloud secrets versions access latest --secret=db-password-prod
# → gsAW6wOy4dgugAKCrhqunqT27tIMENu8   ← persists forever

# ✅ Good — capture into a variable, consume immediately
PW="$(gcloud secrets versions access latest --secret=db-password-prod --project=$P)"
export DATABASE_URL_SYNC="postgresql://auto_workflow:${PW}@127.0.0.1:15432/auto_workflow"
python Database/scripts/migrate.py
unset PW
```

For migrations, the wrapper script already implements this pattern:
```bash
infra/scripts/migrate_via_proxy.sh prod --status
infra/scripts/migrate_via_proxy.sh prod          # apply pending
```
The wrapper handles proxy startup → secret-variable capture →
migration → proxy cleanup, and the DB password never appears in
stdout, argv, or logs.

### Developer workstation hygiene

If a secret value reached the terminal even once, consider:
- **PowerShell**: `Clear-History` +
  `Remove-Item (Get-PSReadlineOption).HistorySavePath`
- **bash / zsh**: `history -c && history -w` +
  `shred -u ~/.bash_history ~/.zsh_history`
- **Terminal scrollback**: close & restart the terminal
- **Agent logs**: Claude Code / Copilot etc. retain full transcripts
  as JSONL — delete that session file (and the sync folder if it's
  synced)
- **If it was a prod secret**: rotate immediately. Cloud Run picks
  up new versions of `value_source.secret_key_ref` with
  `version = "latest"` on cold start, so add a new version, then
  force a revision redeploy.

## App connection setup

### Path A — Cloud SQL Auth Proxy (recommended)

```bash
# Download cloud-sql-proxy once
# https://cloud.google.com/sql/docs/postgres/sql-proxy#install

INSTANCE_CONN=$(cd infra/terraform && terraform output -raw instance_connection_name)
cloud-sql-proxy --port=5433 "$INSTANCE_CONN" &
```

Then
`DATABASE_URL="postgresql+asyncpg://<user>:<pw>@localhost:5433/auto_workflow"`.

Fetch the password:
```bash
gcloud secrets versions access latest --secret=db-password-staging
```

### Path B — Direct public-IP access (dev only)

You must add your IP to `authorized_networks` in
`environments/staging.tfvars` to connect.

```bash
IP=$(cd infra/terraform && terraform output -raw instance_public_ip)
PW=$(gcloud secrets versions access latest --secret=db-password-staging)
export DATABASE_URL="postgresql+asyncpg://auto_workflow:${PW}@${IP}:5432/auto_workflow"
export DATABASE_URL_SYNC="postgresql://auto_workflow:${PW}@${IP}:5432/auto_workflow"
```

## Apply schema + migrations

Reuses the existing `migrate.py`. Works identically against the
Cloud SQL instance.

```bash
DATABASE_URL_SYNC="postgresql://auto_workflow:<pw>@<host>:5432/auto_workflow" \
  python Database/scripts/migrate.py
```

`schemas/001_core.sql` runs `CREATE EXTENSION IF NOT EXISTS vector`
— Cloud SQL Postgres 16 supports pgvector natively, no extra
enablement step.

## Wiring API_Server / Execution_Engine

Just replace `DATABASE_URL` with the Cloud SQL DSN — everything is
already env-based, no code changes needed.

For Cloud Run deployment (later ADR), inject via
`--set-secrets=DATABASE_URL=...,CREDENTIAL_MASTER_KEY=credential-master-key-staging:latest,JWT_SECRET=jwt-secret-staging:latest`.

## Cost management

- **Tear down staging after the demo**:
  ```bash
  # First set deletion_protection = false in staging.tfvars, then:
  terraform destroy -var-file=environments/staging.tfvars
  ```
- **Prod defaults to deletion_protection = true** — `terraform
  destroy` is refused. Flip the variable only when you intentionally
  want to tear it down.
- Stop the instance when idle:
  ```bash
  gcloud sql instances patch auto-workflow-staging --activation-policy=NEVER
  ```

### Destroy time budget

`terraform destroy` finishes the billable resources (Cloud SQL,
Cloud Run, AR, secrets) in 2–5 min, but **the
`serverless-ipv4-*` address reservations left behind by Cloud Run
Direct VPC Egress make VPC / subnet / service-networking removal
take 10–30 min** (GCP's internal reconciler, no CLI to force it).
Don't slot a destroy mid-demo — schedule it just before or just
after a live demo with a **45-minute** budget.

Symptoms: `subnetwork ... is already being used by
.../addresses/serverless-ipv4-*` or
`Service Networking Connection: Producer services are still using
this connection`.

Workaround: a polling retry script.
```bash
# Retry until the address is released (up to ~40 min)
for i in $(seq 1 40); do
  gcloud compute addresses delete "serverless-ipv4-*" \
    --region="$REGION" --project="$PROJECT" --quiet 2>/dev/null && break
  sleep 60
done
terraform destroy -var-file=environments/prod.tfvars  # finalize the remaining VPC / peering
```

## Troubleshooting

- **First `terraform apply` errors that APIs aren't enabled**: run
  `terraform apply` again. API enablement is async, so the first
  plan may race.
- **`ERROR: permission denied for schema public`**: the
  `auto_workflow` user didn't exist yet at DB-creation time.
  Terraform enforces the order (instance → db → user), but running
  migrate.py too early can trip this. Wait until
  `google_sql_user.app` is Ready.
- **pgvector not found**: `CREATE EXTENSION vector` needs
  superuser. `cloudsqlsuperuser` has it, but `auto_workflow` doesn't.
  Run `migrate.py` once with the `postgres` user DSN to install the
  extension first.

## Cloud Run deployment (ADR-020)

ADR-020 deploys API_Server on Cloud Run. Terraform provisions the
full infra (VPC + Cloud Run service + AR + SA + IAM + Auth Proxy
sidecar); only image updates flow through CI or a manual `gcloud`
push.

### Prerequisite — Workload Identity Federation (WIF, one-time)

GH Actions → GCP auth runs **without service-account JSON keys**,
via WIF OIDC. Eliminates key leak / rotation issues.

```bash
PROJECT_ID=auto-workflow-prod-REPLACE
POOL=github-pool
PROVIDER=github-actions
REPO=dhwang0803-glitch/teamlift   # owner/name

# 1. Workload Identity Pool + OIDC provider
gcloud iam workload-identity-pools create "$POOL" \
  --project="$PROJECT_ID" --location=global

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --project="$PROJECT_ID" --location=global \
  --workload-identity-pool="$POOL" \
  --display-name="GitHub Actions" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '${REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# 2. CI service account (Cloud Run admin + AR writer + impersonate Cloud Run runtime SA)
SA_CI=auto-workflow-ci@${PROJECT_ID}.iam.gserviceaccount.com
gcloud iam service-accounts create auto-workflow-ci --project="$PROJECT_ID"

for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_CI}" --role="$role"
done

# 3. Allow only the release branch to impersonate this SA
POOL_ID=$(gcloud iam workload-identity-pools describe "$POOL" \
  --project="$PROJECT_ID" --location=global --format='value(name)')
gcloud iam service-accounts add-iam-policy-binding "$SA_CI" \
  --project="$PROJECT_ID" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${REPO}"
```

Register the two resulting values on the GitHub repo:

- **Settings → Secrets → Actions**:
  - `GCP_WIF_PROVIDER` =
    `${POOL_ID}/providers/${PROVIDER}` (full resource path)
  - `GCP_WIF_SERVICE_ACCOUNT` =
    `auto-workflow-ci@<project>.iam.gserviceaccount.com`
- **Settings → Variables → Actions**:
  - `GCP_PROJECT_ID_PROD` = `auto-workflow-prod-…`
  - `GCP_REGION` = `asia-northeast3`

### Deploy branches + protection rules (one-time)

```bash
# Create two branches from main
git checkout main && git pull
git push origin main:development
git push origin main:release
```

GitHub → Settings → Branches → Add rule:

| Branch | Rules |
|--------|-------|
| `release` | Require a pull request before merging · **Require linear history** · Require status checks to pass · Do not allow force pushes · Do not allow deletions |
| `development` | Require a pull request before merging · Do not allow force pushes |

For `release`, allowing only **Rebase and merge** or **Squash and
merge** (with `Allow merge commits` OFF) enforces linear history.

### Bootstrap — first `terraform apply` (Phase 4 baseline)

`api_image_uri` is a required variable (ADR-020 §6-a). AR must
exist before any image push, and the image must exist before the
`/health` probe can pass — so do this in two stages:

```bash
cd infra/terraform

# 1) Apply API enablement + Artifact Registry only
terraform apply -var-file=environments/staging.tfvars \
  -target=google_project_service.runtime_apis \
  -target=google_artifact_registry_repository.images

# 2) Build + push the image (locally)
AR="asia-northeast3-docker.pkg.dev/${PROJECT_ID}/auto-workflow/api"
TAG=bootstrap-$(date +%Y%m%d)
gcloud auth configure-docker asia-northeast3-docker.pkg.dev --quiet
docker build -f API_Server/Dockerfile -t "${AR}:${TAG}" .
docker push "${AR}:${TAG}"

# 3) Set api_image_uri = "${AR}:${TAG}" in staging.tfvars and full apply
terraform apply -var-file=environments/staging.tfvars
```

Subsequent applies are single-step. Thanks to
`lifecycle.ignore_changes`, image updates pushed by CI or a manual
`gcloud run deploy` are not reverted on the next apply.

### Manual deploy to dev (`development` branch)

A human deploys to the Cloud Run service
(`auto-workflow-api-staging`) in the staging GCP project.

```bash
git checkout development && git pull
git merge --ff-only main        # bring in only what was promoted from main

SHA=$(git rev-parse HEAD)
PROJECT=auto-workflow-staging-REPLACE
REGION=asia-northeast3
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/auto-workflow/api:${SHA}"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker build -f API_Server/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"

gcloud run deploy auto-workflow-api-staging \
  --image="$IMAGE" \
  --region="$REGION" \
  --project="$PROJECT" \
  --quiet

git push origin development      # advance the branch pointer
```

Inspect logs / errors / responses here. If it passes, promote to
`release`.

### Auto-deploy to prod (`release` branch + GH Actions)

Promote a validated commit from `development` to `release`, ff-only.

```bash
git checkout release && git pull
git merge --ff-only development
git push origin release          # protection refuses a non-ff push
```

A successful push triggers `.github/workflows/deploy-prod.yml`:
1. linearity guard (fails on a merge commit)
2. WIF auth to GCP
3. AR login → `docker build / push` (tag = `${{ github.sha }}`)
4. `gcloud run deploy auto-workflow-api-prod --image=<tag>`
5. Cloud Run rolls a new revision, runs `/health`, and swaps
   traffic. On probe failure, `gcloud` returns non-zero → the
   workflow fails → the previous revision stays live.

### Rollback

When a regression surfaces in prod:

```bash
git checkout release
git revert <bad-sha>             # creates a revert commit
git push origin release          # the same workflow re-deploys the prior state
```

Or, immediately swing traffic with the Cloud Run Console / `gcloud
run services update-traffic auto-workflow-api-prod
--to-revisions=<prev>=100`, then file the `git revert` separately.

### Emergency hotfix

When you need to shortcut the `main` review / merge cycle, ff-only
merge a `hotfix/*` branch into `release`, then sync backwards into
`main` and `development`. `release` still stays linear.

## Worker Pools deploy runbook (ADR-021)

The path that runs Execution_Engine as Cloud Run Worker Pools with
Memorystore as the broker. API_Server wakes the pool on `execute` by
calling the Cloud Run Admin API `services.patch`.

### First apply (staging)

```bash
# 1) Build + push the EE image to AR
AR="asia-northeast3-docker.pkg.dev/${PROJECT_ID}/auto-workflow/worker"
TAG=bootstrap-$(date +%Y%m%d)
docker build -f Execution_Engine/Dockerfile -t "${AR}:${TAG}" .
docker push "${AR}:${TAG}"

# 2) Set ee_image_uri = "${AR}:${TAG}" in staging.tfvars and apply
cd infra/terraform
terraform apply -var-file=environments/staging.tfvars
#   → Memorystore 1GB BASIC provisions in ~5 min; Worker Pool creation ~1 min
```

On the first apply, `google_redis_instance.broker.host` is assigned a
private IP, and `WORKER_POOL_NAME` + `CELERY_BROKER_URL` env vars
land on the API revision. API Cloud Run flips traffic automatically
once the new revision passes `/health`.

### Wake-up check

```bash
# Call /execute once, then check the wake log in API logs
gcloud logging read \
  'resource.type="cloud_run_revision" AND jsonPayload.message~"worker pool .* woken"' \
  --limit=5 --freshness=5m

# Worker Pool instance count (jumps to 1 right after patch)
gcloud run worker-pools describe auto-workflow-ee-staging \
  --region=asia-northeast3 --format='value(scaling.minInstanceCount)'
```

GCP returns the pool to 0 automatically after 15 min of no tasks. We
have no sleep code on our side.

### Image update (steady state)

```bash
gcloud run worker-pools update auto-workflow-ee-staging \
  --image="${AR}:${NEW_TAG}" --region=asia-northeast3
```

`lifecycle.ignore_changes = [template[0].containers[0].image]`
prevents Terraform from reverting to the old image.

### Destroy

```bash
# The broker is guarded by prevent_destroy. Lift it momentarily when intentionally tearing down:
#   set lifecycle.prevent_destroy = false in memorystore.tf temporarily
terraform destroy -var-file=environments/staging.tfvars \
  -target=google_cloud_run_v2_worker_pool.ee \
  -target=google_redis_instance.broker
# Restore prevent_destroy afterwards and commit.
```

Recreating Memorystore with the same name has no API constraint
(unlike Cloud SQL, the name is reusable).

## Related docs

- `docs/context/decisions.md` ADR-018 — Cloud SQL + Secret Manager
- `docs/context/decisions.md` ADR-019 — Google OAuth2
  credential_type. The three secrets' injection procedure is in
  [`README_oauth.md`](README_oauth.md)
- `docs/context/decisions.md` ADR-020 — Cloud Run deployment
  (§1–10) + §6-a `api_image_uri` policy + §7 branch strategy
- `docs/context/decisions.md` ADR-021 — Cloud Run Worker Pools +
  Memorystore Redis
- `infra/plans/PLAN_21_worker_pools.md` — implementation breakdown
- `Database/scripts/migrate.py` — migration runner
- `Database/schemas/` — schema SQL sources
- `Database/migrations/` — incremental change history
- `.github/workflows/deploy-prod.yml` — release auto-deploy
  pipeline
