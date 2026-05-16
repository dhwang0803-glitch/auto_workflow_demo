# PLAN_21 — Execution_Engine deployment (Cloud Run Worker Pools + Memorystore Redis)

> **Branch**: `infra` (Terraform/IAM) + `Execution_Engine` (worker tweaks) + `API_Server` (inline + wake-up) · **Drafted**: 2026-04-20 · **Status**: Draft
>
> Execution decomposition for the structure decided in ADR-021 (docs branch, PR #91 merged). Phase 1 (ADR) / Phase 2 (this PLAN) are documentation; Phases 3–6 are the implementation. This PLAN **1:1-maps Phases 3–6** and pins down the files / resources / test gates. If anything diverges from the ADR, fix the ADR's Update section first, then re-adjust this PLAN.

## 1. Goals

1. Describe a Memorystore Redis Basic 1GB instance + Cloud Run Worker Pools (min=0, max=5) in Terraform
2. Make the Execution_Engine Celery worker run on Memorystore as broker (keep the local-Docker-Redis path)
3. On execute trigger, have API_Server wake the Worker Pools via the Cloud Run Admin API `services.patch` + throttle
4. Round out the `execution_mode = inline` temporary stopgap, then **remove it entirely** in Phase 6
5. After full E2E success in staging (`/execute` → wake → queue → worker → DB write), verify the destroy cycle

## 2. Scope

**In**
- `infra/terraform/memorystore.tf` (new), `worker.tf` (new), `variables.tf` extension, `outputs.tf` extension
- IAM: bind `run.workerPools.update` to the API_Server SA with constraints (IAM condition scoping to a specific worker pool resource)
- `infra/scripts/deploy_worker_pool.sh` (image build + push + apply wrapper)
- Execution_Engine: env-var-ize broker URL in `scripts/worker.py` + soft timeout + SETNX idempotency
- API_Server: `settings.execution_mode` switch, inline branch in `services/workflow_service.py::execute_workflow`, new `services/wake_worker.py` (Cloud Run patch wrapper + throttle)
- bats unit tests (Phase 3), pytest (Phases 4/5), live E2E bash (Phase 6)
- Add a "Worker Pools deployment runbook" section to `infra/docs/README.md`

**Out**
- GPU / LLM inference path (ADR-008) — separate ADR
- Agent-mode deployment — separate ADR (ADR-023 planned)
- Custom-Cloud-Monitoring-metric-based queue-depth autoscaling — rejected in ADR-021 §4; not covered here either
- Celery Beat (APScheduler replacement) migration — deferred in ADR-021 §10
- Frontend Phase C itself — this PLAN only provides inline mode; the Phase C E2E implementation belongs to a separate Frontend PLAN
- Memorystore Standard tier promotion — separate ADR Update on prod entry

## 3. Phase 3 — Terraform (infra branch)

### 3.1 New files

**`infra/terraform/memorystore.tf`**

```hcl
resource "google_redis_instance" "broker" {
  name               = "auto-workflow-broker-${var.environment}"
  tier               = "BASIC"
  memory_size_gb     = 1
  region             = var.region
  authorized_network = google_compute_network.vpc.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"
  redis_version      = "REDIS_7_2"
  reserved_ip_range  = google_compute_global_address.service_networking.name

  lifecycle {
    prevent_destroy = true   # Basic has no delete protection — defend via Terraform guard only
  }
}
```

- `connect_mode = "PRIVATE_SERVICE_ACCESS"` — reuses the `google-managed-services-*` allocated range already created by ADR-020 (no new subnet needed)
- `redis_version` pinned to the latest Memorystore-supported stable (same on staging and prod)

**`infra/terraform/worker.tf`**

```hcl
resource "google_cloud_run_v2_worker_pool" "ee" {
  provider = google-beta   # Worker Pools needs beta in some regions; switch to google once GA-confirmed
  name     = "auto-workflow-ee-${var.environment}"
  location = var.region

  template {
    service_account = google_service_account.ee_runtime.email

    containers {
      image = var.ee_image_uri   # AR path, required — same pattern as ADR-020's api_image_uri
      resources {
        limits = { cpu = "0.5", memory = "512Mi" }
      }

      env {
        name  = "CELERY_BROKER_URL"
        value = "redis://${google_redis_instance.broker.host}:${google_redis_instance.broker.port}/0"
      }
      env {
        name  = "DATABASE_URL"
        value_source { secret_key_ref { secret = google_secret_manager_secret.database_url.secret_id, version = "latest" } }
      }
      env {
        name  = "CREDENTIAL_MASTER_KEY"
        value_source { secret_key_ref { secret = google_secret_manager_secret.credential_master_key.secret_id, version = "latest" } }
      }
      env {
        name  = "GOOGLE_OAUTH_CLIENT_ID"
        value_source { secret_key_ref { secret = google_secret_manager_secret.google_oauth_client_id.secret_id, version = "latest" } }
      }
      env {
        name  = "GOOGLE_OAUTH_CLIENT_SECRET"
        value_source { secret_key_ref { secret = google_secret_manager_secret.google_oauth_client_secret.secret_id, version = "latest" } }
      }
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.cloudrun_direct.id   # shared with API_Server
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
  }

  lifecycle {
    ignore_changes = [
      # Don't treat instance_count changes from wake-up as Terraform state drift
      template[0].scaling[0].min_instance_count,
    ]
  }
}

# Grant the API_Server SA Worker-Pools patch permission
resource "google_cloud_run_v2_worker_pool_iam_member" "api_wake_permission" {
  provider = google-beta
  project  = var.project_id
  location = google_cloud_run_v2_worker_pool.ee.location
  name     = google_cloud_run_v2_worker_pool.ee.name
  role     = "roles/run.developer"   # minimal role that includes workerPools.update; tighten to a custom role later
  member   = "serviceAccount:${google_service_account.api_runtime.email}"
}
```

**`infra/terraform/variables.tf`** — 3 new variables

```hcl
variable "ee_image_uri" {
  description = "Execution_Engine image path (AR full path). Fails apply if empty."
  type        = string
}

variable "ee_worker_max_instances" {
  description = "Worker Pools max_instance_count"
  type        = number
  default     = 5
}

variable "ee_worker_resources" {
  description = "Worker-container resource limits"
  type        = object({ cpu = string, memory = string })
  default     = { cpu = "0.5", memory = "512Mi" }
}
```

**`infra/terraform/outputs.tf`** — 2 new outputs

```hcl
output "ee_worker_pool_name" {
  value       = google_cloud_run_v2_worker_pool.ee.name
  description = "Inject into API_Server as the WORKER_POOL_NAME env var"
}

output "broker_host" {
  value       = google_redis_instance.broker.host
  description = "Memorystore host (used to assemble API_Server's CELERY_BROKER_URL)"
  sensitive   = false
}
```

**`infra/terraform/cloud_run.tf` change (API_Server)** — new env entries

```hcl
env {
  name  = "WORKER_POOL_NAME"
  value = google_cloud_run_v2_worker_pool.ee.name
}
env {
  name  = "CELERY_BROKER_URL"
  value = "redis://${google_redis_instance.broker.host}:${google_redis_instance.broker.port}/0"
}
```

### 3.2 tfvars example updates

Add `ee_image_uri` to `staging.tfvars.example` / `prod.tfvars.example`. Real values go into the gitignored tfvars.

### 3.3 Acceptance criteria

- `terraform validate` + `terraform plan -var-file=staging.tfvars.example` succeed (dry-run)
- `tflint` + `checkov` infra rules pass (reuse the ones already in CI)
- 2 new `bats` tests: verify Memorystore `authorized_network`; verify the Worker-Pools scaling block
- Worker-Pools deployment runbook section added to `infra/docs/README.md` (≤ 50 lines) — image build · push · apply · log-check order

## 4. Phase 4 — Execution_Engine (Execution_Engine branch)

### 4.1 File modifications

**`Execution_Engine/scripts/worker.py`**
- Read the Celery broker URL from `os.environ["CELERY_BROKER_URL"]` (remove the current hardcoded path)
- `CELERYD_TASK_SOFT_TIME_LIMIT = 8` (margin within the 10-second SIGTERM grace)
- `CELERYD_TASK_TIME_LIMIT = 30` (hard-kill ceiling)
- SIGTERM handler uses Celery's default `warm_shutdown` — no explicit code needed, just one log line (`logger.info("SIGTERM received, warm shutdown")`) for observability

**`Execution_Engine/src/dispatcher/serverless.py`**
- Add **SETNX idempotency** to the task entrypoint wrapper:

```python
async def execute_with_idempotency(execution_id: UUID, ...):
    key = f"execution:{execution_id}"
    if not await redis.set(key, "running", nx=True, ex=86400):
        logger.info("execution %s already running/completed, skipping duplicate", execution_id)
        return
    try:
        return await _do_execute(...)
    finally:
        await redis.set(key, "completed", ex=86400)
```

- The Redis client is a module singleton `redis.asyncio.from_url(os.environ["CELERY_BROKER_URL"])` (shares the broker instance)

**`Execution_Engine/tests/test_dispatcher_idempotency.py` (new)**
- 3 cases: first call executes / two concurrent calls → executes once / re-call after prior completion with the same id → skip
- Local verification with fakeredis (`pip install fakeredis`)

### 4.2 Acceptance criteria

- `pytest Execution_Engine/tests/` all green (existing + 3 new cases)
- Local `docker-compose up redis worker` runs `scripts/worker.py` → enqueue a fake task → confirm pickup logs (manual)
- Missing broker URL fails fast with a clear error message (`KeyError: CELERY_BROKER_URL`)

## 5. Phase 5 — API_Server inline (temporary) + 5-b wake-up (API_Server branch)

### 5.1 Phase 5 — inline mode (temporary)

**`API_Server/app/config.py`**
```python
execution_mode: Literal["celery", "inline"] = Field(default="celery", description="temporary — remove at end of ADR-021 Phase 6")
```

**`API_Server/app/services/workflow_service.py::execute_workflow`**
- In the `settings.execution_mode == "inline"` branch, call `runtime.executor.execute_dag(...)` directly via `await` and return the result synchronously
- The `"celery"` branch keeps the existing `Celery .delay()` + adds the wake-up call from §5.2

**Tests** — `tests/test_execute_inline.py` (new)
- 3-node DAG runs inline → confirm sync-returned result
- A node that times out → confirm `WorkflowTimeoutError` is raised inline

### 5.2 Phase 5-b — wake-up wiring (celery mode)

**`API_Server/app/services/wake_worker.py` (new)**

```python
import time
from google.cloud import run_v2

_last_wake_at: float = 0.0
_WAKE_THROTTLE_S = 30.0   # skip duplicate wakes within 30s

async def wake_worker_pool() -> None:
    global _last_wake_at
    now = time.monotonic()
    if now - _last_wake_at < _WAKE_THROTTLE_S:
        return   # assume still warm; skip the patch call
    client = run_v2.WorkerPoolsAsyncClient()
    pool_name = f"projects/{settings.project_id}/locations/{settings.region}/workerPools/{settings.worker_pool_name}"
    await client.update_worker_pool(
        worker_pool=run_v2.WorkerPool(
            name=pool_name,
            template=run_v2.WorkerPoolRevisionTemplate(
                scaling=run_v2.WorkerPoolScaling(min_instance_count=1)
            ),
        ),
        update_mask={"paths": ["template.scaling.min_instance_count"]},
    )
    _last_wake_at = now
    logger.info("worker pool %s woken", settings.worker_pool_name)
```

- With no tasks to process, Worker Pools fall back to 0 automatically after the idle timeout (default 15 minutes) — no explicit sleep call needed
- 30-second throttle: prevents patch-API storms under bursty requests. 30 seconds is a safe assumption for "already warm"

**`execute_workflow` `celery` branch**
```python
if settings.execution_mode == "celery":
    await wake_worker_pool()
    execute_task.delay(workflow_id, ...)
```

### 5.3 Acceptance criteria

- Unit: `tests/test_wake_worker.py` — first call patches once, immediate re-call skips, re-call after 30s patches once (monotonic mock)
- Contract: if the patch fails (network / permissions), `execute_workflow` does **not** fail entirely — log + fallback (the task is still enqueued; the next call retries wake). The Celery task itself can wait several minutes due to worker boot lag and still be picked up in order
- Security: the API SA's `roles/run.developer` scope → don't touch other worker pools in the same project; `settings.worker_pool_name` accepts only the exact name injected by the deployer

## 6. Phase 6 — Live E2E + inline removal + destroy verification (infra branch)

### 6.1 Live E2E order (staging)

1. Build the EE image + push to AR → inject `ee_image_uri` into tfvars
2. `terraform apply -var-file=staging.tfvars` → provision Memorystore + Worker Pools (~5 min)
3. Redeploy the API (pick up the new `WORKER_POOL_NAME`, `CELERY_BROKER_URL` envs)
4. Call `/workflows/{id}/execute` → confirm `worker pool ... woken` in API logs
5. Trace Worker-Pools instance startup logs + Celery task pickup logs in Cloud Logging
6. Confirm `succeeded` status in the DB `executions` table
7. Two more calls → within the 30-second throttle, wake is skipped; warm pickup confirmed
8. Wait 15 minutes → confirm Worker-Pools `instance_count` falls back to 0 (Cloud Console)

### 6.2 Inline-mode removal

- Delete the `settings.execution_mode` field + the inline branch + the `test_execute_inline.py` file
- Add `.github/workflows/inline-guard.yml` to CI:
  ```yaml
  - run: |
      if grep -rn "execution_mode" API_Server/app/ Frontend/src/; then
        echo "inline mode residual detected" && exit 1
      fi
  ```
- Update the Phase 5/5-b/6 status in the `ADR-021` Phase table to `✅` (separate PR on the docs branch)

### 6.3 Destroy-cycle verification

- Partial destroy via `terraform destroy -var-file=staging.tfvars -target=google_redis_instance.broker -target=google_cloud_run_v2_worker_pool.ee` (keep Cloud SQL)
- Memorystore is guarded by `prevent_destroy = true` → confirm destroy is blocked → release the guard and re-destroy
- Record total destroy time (planned addition to ADR-021 Consequences as a measurement — verify whether the `serverless-ipv4-*` GC delay seen on Cloud Run Direct VPC Egress applies to Worker Pools as well)

### 6.4 Acceptance criteria

- All of Live E2E steps 4–8 succeed
- Inline-guard CI green
- After destroy, confirm the idle bill — only Cloud SQL (`db-g1-small`) + API_Server min=1 remain; Memorystore charges 0
- Write `infra/reports/REPORT_21_worker_pools.md` (measured costs, regressions, lessons — REPORTER agent output format)

## 7. Phase dependencies & branch boundaries

```
Phase 3 (infra)   ┐
                  ├─→ Phase 6 (infra: E2E + destroy)
Phase 4 (EE)      ┤        ↑
                  │        │
Phase 5 (API)     ┘        │
                           │
Phase 5-b (API) ←──────────┘  (needs the worker_pool_name output)
```

- Phase 3's `ee_worker_pool_name` output → wires into Phase 5-b's `settings.worker_pool_name` → env injected on API redeploy
- Phases 4 and 5 are independent (different branches) — parallel PRs are fine
- Phase 6 starts only after Phases 3/4/5/5-b are all merged. Wrong order means we hit a worker without wake-up (fail)

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Worker Pools SKU still beta in some regions / has constraints | Explicit `google-beta` provider + early fail at `terraform plan`. Record any issue in the ADR-021 Update section |
| `run.workerPools.update` IAM scope wider than intended (`roles/run.developer`) | After real-world verification, narrow to a custom role `workerPool.updateOnly` — Phase 6 follow-up |
| Memorystore `prevent_destroy = true` blocks destroy and halts CI | Without `-target` the whole-destroy attempt fails → wrapper script offers a `--force-destroy-broker` opt-out |
| Task waits forever if wake-up fails | wake_worker failure → log + Celery task still enqueues normally. Picked up when the worker boots. If we see "5 consecutive wake failures" in production, revisit retry logic after Phase 6 |
| Worker Pools cold start 10–20s feels slow during the demo | Frontend progress UI shows "queued for execution" copy (contract pinned in the Frontend PLAN) |
| Inline-mode code lingers after removal | CI `inline-guard.yml` + Phase 6 PR review checklist. Force an ADR-021 status update on the docs branch |
| Redis SETNX idempotency-key TTL of 24h causes collisions (same execution_id retried within 24h) | execution_id is a UUID → 0 collision probability. Retries land as a separate execution row with a new UUID. OK |

## 9. Work order (for actual execution)

1. [infra] Write Phase 3 TF files + `terraform validate/plan` + bats tests → PR
2. After merge, [EE] Phase 4 broker URL swap + SETNX idempotency + pytest → PR (parallelizable)
3. After merge, [API] Phase 5 inline mode + tests → PR
4. After merge, [API] Phase 5-b wake-up module + throttle tests → PR
5. After merge, [infra] Phase 6 Live E2E → write REPORT → docs PR updating ADR-021 Phase table to ✅
6. After merge, [API] remove inline + CI guard → PR

6 PRs total expected. ADR-021 was already merged, so this PLAN's PRs are PR #7; combined with the ADR that's 8 PRs to close out ADR-021 end to end.

## 10. Related

- ADR: [`ADR-021`](../../docs/context/decisions.md) (docs branch PR #91)
- Predecessor ADRs: ADR-003 (Celery + Redis broker), ADR-018 (VPC + Service Networking), ADR-020 (Cloud Run deployment pattern)
- Related PLANs: API_Server `PLAN_03_EXECUTION_TRIGGER.md` (Celery task entrypoint contract), Database `PLAN_07_DB_RESILIENCE.md` (timeout assumptions when Worker shares the DB connection pool)
- Follow-up ADRs planned: ADR-022 (Frontend deployment — tied to inline-mode lifetime), ADR-023 (Agent deployment — independent of this Worker-Pools path)
