# Phase 6 Live E2E Runbook — ADR-021 Worker Pools

> Single-run guide covering the entire PLAN_21 §6.1. Only the first
> live E2E needs this order — afterwards, just re-run
> `run_e2e_phase21.sh`.

## Prerequisites

- Phase 3 / 4 / 5 / 5-b all merged into `main` (#85, #94, #95, this PR)
- `gcloud` auth + ADC done (`gcloud auth application-default login`)
- Docker daemon running + push permission on Artifact Registry
- staging `environments/staging.tfvars` filled in
- staging Cloud SQL instance already exists (ADR-018 apply complete)

## Step 1 — Build + push the Execution_Engine image

```bash
cd "$(git rev-parse --show-toplevel)"
REGION=asia-northeast3
PROJECT="$(gcloud config get-value project)"
TAG="phase21-$(git rev-parse --short HEAD)"
IMG="${REGION}-docker.pkg.dev/${PROJECT}/auto-workflow/worker:${TAG}"

docker build -t "$IMG" -f Execution_Engine/Dockerfile .
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker push "$IMG"
echo "$IMG"  # ← use as `ee_image_uri` in the next step
```

## Step 2 — Terraform apply (provision Memorystore + Worker Pool)

```bash
cd infra/terraform
# add ee_image_uri = "<IMG above>" to staging.tfvars
terraform plan  -var-file=environments/staging.tfvars
terraform apply -var-file=environments/staging.tfvars
```

The first apply takes **about 5–8 min** because Memorystore is
provisioning. `google_compute_global_address.private_services_range`
and the peering already exist from the ADR-018 apply, so the diff is
just Memorystore + Worker Pool + IAM.

`terraform output` items to capture:
- `ee_worker_pool_name` — feed into the API env `WORKER_POOL_NAME`
- `broker_host` — used to assemble the API env `CELERY_BROKER_URL`

## Step 3 — Redeploy API_Server (pick up the new env)

Inject `SERVERLESS_EXECUTION_MODE=celery`, `WORKER_POOL_NAME`,
`GCP_PROJECT_ID`, `GCP_REGION`, `CELERY_BROKER_URL` into the API
Cloud Run service. CI handles this by default; the manual redeploy is:

```bash
gcloud run services update "auto-workflow-api-${ENV}" \
  --region="$REGION" \
  --update-env-vars="SERVERLESS_EXECUTION_MODE=celery,\
WORKER_POOL_NAME=auto-workflow-ee-${ENV},\
GCP_PROJECT_ID=${PROJECT},\
GCP_REGION=${REGION},\
CELERY_BROKER_URL=redis://<broker_host>:6379/0"
```

> **Note**: get `broker_host` only via `terraform output -raw
> broker_host`. It would be safe to echo (Memorystore is RFC1918) but
> the habit of capturing first is worth keeping.

## Steps 4–7 — Observation (automated)

```bash
# Pre-create one workflow (condition→merge 2-node graph recommended —
# see the TWO_NODE_GRAPH fixture in tests/test_execute_inline.py from
# PR #95). Capture wf_id.
WF_ID=<created workflow id>
TOKEN=<access_token obtained by logging in>
API_BASE="$(gcloud run services describe auto-workflow-api-${ENV} \
             --region=$REGION --format='value(status.url)')"

bash infra/scripts/run_e2e_phase21.sh "$ENV" "$API_BASE" "$TOKEN" "$WF_ID"
```

Expected output:
```
[1/3] First /execute — expect wake-up log line and terminal status=success
  execution_id=...
  status=success
[2/3] Back-to-back executes within throttle window (30s default)
  execution_ids=... ...
  statuses=success success
[3/3] Verify throttle — expect exactly 1 'woken' log across all 3 executes
  woken_log_count=1 (within last 10m)

Phase 6.1 live observation passed: 3 executions, 1 wake(s), all success.
```

## Step 8 — Scale-down verification

The current implementation uses MANUAL scaling. AUTOMATIC's idle
timeout therefore does not engage, so verify scale-down with one of:

- **Simple**: `terraform destroy
  -target=google_cloud_run_v2_worker_pool.ee` — drop the pool itself.
  Confirms zero cost.
- **Keep the deployment**: call `gcloud run worker-pools update` with
  `--manual-instance-count=0` (Admin API patch), then verify
  `instance_count → 0` in the Cloud Console.

A post-Phase-6 follow-up could add a Cloud Scheduler + Cloud Functions
watchdog so the pool drops to 0 automatically on idle (PLAN_21 §6.3
risk table).

## Rollback / incident response

- No `woken` log → suspect missing WakeWorker IAM. Verify that the
  `api_wake_permission` IAM binding in `worker.tf` has been applied.
- `status=failed` → run `gcloud logging read 'resource.type=cloud_run_revision
  AND resource.labels.service_name=auto-workflow-ee-${ENV}' --freshness=15m`
  to inspect the worker-side stack. The most common cause is a Celery
  broker connection failure — re-check Memorystore IP reachability
  (VPC peering).
- API_Server lifespan failure due to missing `DATABASE_URL` → check
  the Cloud Run revision's env vars. Confirm that Step 3's
  `--update-env-vars` actually landed.

## Acceptance criteria (PLAN_21 §6.4)

- [ ] All 3 stages of the script pass
- [ ] Cloud Logging shows worker-instance boot log + task-pickup log
- [ ] The corresponding `executions` row has `status='success'` and
      `node_results` is populated
- [ ] Exactly 1 `woken` log across the 3 executions (throttle works)
- [ ] After destroy, idle cost = Cloud SQL + API only; Memorystore /
      Worker = 0
- [ ] `infra/reports/REPORT_21_worker_pools.md` written with measured
      cost / latency
