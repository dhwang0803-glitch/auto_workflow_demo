# ADR-021 Phase 6 — Worker Pool Wake-up Live Debugging Log

**Date:** 2026-04-20
**Environment:** staging (`autoworkflowdemo` / asia-northeast3)
**Goal:** Verify the live pipeline where API_Server wakes the
Execution_Engine deployed as Cloud Run Worker Pools when `/execute` is
called — PLAN_21 §6.1 (3 runs + wake-throttle check)
**Result:** 3/3 executions `status=success`,
`woken_log_count=1` (the 30 s throttle worked). **The three layered
root causes only surfaced one after another.**

---

## 1. Expected flow vs. actual experience

### Expected

```
docker build + push  →  terraform apply  →  API redeploy
                                                   │
                                                   ▼
    bash infra/scripts/run_e2e_phase21.sh staging ... WF_ID
                                                   │
                                                   ▼
    3 executions all success, 1 wake log, done.
```

The runner script had already been written during the Phase 6 prep
(commit `462ac9e`), and the Terraform IaC for it was already declared
in `cloud_run.tf` / `worker.tf` / `memorystore.tf`. Code-wise we were
"done".

### Actual

The first run timed out. The worker pool didn't scale up from 0. The
only logs were uvicorn access logs — no app-logger output reached
Cloud Logging at all. From here, **three layers of root causes were
stacked on top of each other, each one masking the next**.

---

## 2. The three bottleneck layers (surface → depth)

| # | Layer | Surface symptom | Actual cause |
|---|-------|------------------|--------------|
| 1 | **Runtime state** | Worker pool refused to scale (`manual_instance_count=0` stuck) | The API server simply had no wake-triggering code — the Cloud Run Admin API `workerPools.patch` call was never being made |
| 2 | **Config surface** | After (1), still no wake logs | `wake_worker._configured()` silently returned early because two of the three env vars were missing |
| 3 | **GCP IAM** | After (2), wake was firing but got `PermissionDenied` | The Cloud Run Admin API validates actAs on the compute-default SA even when the `update_mask` only touches scaling — a proto3-default-init quirk |

Each layer can only be observed after fixing the previous one, so
**it was impossible to see them all at once** — sequential debugging
was the only way.

---

## 3. Specific tech-stack pain we hit

### 3-1. Cloud Run Worker Pools (ADR-021 §4)

- **`cpu < 1` rejected**: I set
  `ee_worker_resources.cpu = "0.5"` and `apply` failed with
  `Invalid value specified for cpu. Total cpu < 1 is not supported
  with gen2`. Worker Pools force the Cloud Run v2 gen2 runtime with
  always-allocated CPU, so the minimum shape is `cpu=1`. Cloud Run
  **Service** (v1/v2) allows ≤0.5 — a real divergence. → Pinned to
  `cpu = "1"` in `variables.tf`.
- **AUTOMATIC scaling not available**: In
  `google-cloud-run` Python SDK 0.16.0, the only writable field on
  `WorkerPoolScaling` is `manual_instance_count`. `min_instance_count`
  / `max_instance_count` aren't in the generated proto yet. So
  scaling_mode is pinned to MANUAL and wake is implemented as "patch
  count=1". Scale-down back to 0 isn't automatic — in Phase 6 today
  the only options are `terraform destroy` or an explicit patch. A
  separate idle watchdog is a post-Phase-6 TODO.
- **Server-side interpretation of `template.service_account=""`**:
  proto3 serializes an unset string as `""`. The Cloud Run Admin API
  server reads that as "use the compute-default SA" and includes that
  SA in actAs validation. Even when `update_mask` only covers
  scaling, the server validates the full proto. **Net effect**: the
  API SA needs `iam.serviceAccountUser` on the compute-default SA
  too. Not documented officially — you only find it after a 403.

### 3-2. Celery + Memorystore Redis broker

- **VPC peering latency**: Creating a Memorystore BASIC instance took
  5 min 13 s. It was the longest single action in `terraform apply`.
  Retries aren't free (recreating the same name has a cooldown).
- **Broker URL composition**:
  `redis://${google_redis_instance.broker.host}:${google_redis_instance.broker.port}/0`.
  `host` is only known after apply, so Memorystore must be created
  before the Worker Pool + API env render. Enforced with
  `depends_on = [google_redis_instance.broker]`.
- **Missing queue routing**: The worker subscribes only to the
  `workflow_tasks` queue, but if the API's `send_task` doesn't pass
  `queue=`, the task goes to the default `celery` queue and waits
  forever. That was already fixed in `fb220de`, but the regression
  potential is worth being aware of.

### 3-3. Cloud SQL Auth Proxy sidecar

- **Sidecar missing on the worker**: The API service in
  `cloud_run.tf` has a `cloudsql-proxy` sidecar, but `worker.tf`
  didn't. The `DATABASE_URL` secret was composed against
  `localhost:5432`, so the worker container called localhost
  directly → `ConnectionRefusedError: ('127.0.0.1', 5432)` on every
  Celery task. The worker container and the proxy container share
  the same Cloud Run instance's pod-like network namespace, so
  localhost-to-proxy works **only if both are declared in the same
  `template` block**. Fix: add
  `containers { name = "cloudsql-proxy" ... }` to `worker.tf`'s
  `template`.
- **Port-clash false alarm**: The user suggested "since my PC has
  5432/5433 in use, let's standardize on 5435". But the Cloud Run
  container's localhost is a completely isolated network namespace
  from the host PC — there can be no port collision. We kept the
  default 5432.

### 3-4. Git Bash on Windows — host environment issues for the E2E runner

| Symptom | Cause | Fix |
|---------|-------|-----|
| `gcloud: exec: python: not found` | gcloud's bash wrapper looks for `python` on PATH, but Windows only has `python.exe` | `export CLOUDSDK_PYTHON="/c/Users/user/AppData/Local/Google/Cloud SDK/google-cloud-sdk/platform/bundledpython/python.exe"` |
| `bash: !2026: event not found` | bash history expansion treats `!2026` (in a password) as a history reference | Single-quote the value or `set +H` |
| `VERIFY_TOKEN=` ends up empty | Writing `VAR=$(...) curl ...` on one line tells bash "this is an env-only assignment for the following command" — the next line never sees it | Split into two lines |
| `WF_ID=tail` | sed's greedy match scoops up a nested `"id":"..."` from the graph body | Extract by UUID regex: `grep -oE '[a-f0-9]{8}-[a-f0-9]{4}-...'` |

### 3-5. GCLB 411 on empty POST

`POST /api/v1/workflows/{id}/execute` doesn't actually need a body,
but GCLB sits in front of Cloud Run and rejects bodyless POSTs
without `Content-Length` with 411. Fix: force an empty JSON body with
`-d '{}'`. The script keeps a comment explaining why.

### 3-6. Deployed image SHA vs. code commit SHA

The most insidious one. The deployed API image tag
`logging-fix-632d8f8` had been built from commit `632d8f8`
(`fix(ee): Sheets node resolves first-sheet name ...`). But the
wake_worker code only landed in `f9ecbda` (ADR-021 Phase 5).
`git merge-base --is-ancestor 632d8f8 f9ecbda` returned True —
meaning **the deployed image predated the wake code**. The repo
showed the wake logic, but the running container didn't have it.

The clues that surfaced it:
- After fixing `_configured()` and injecting env vars, still zero
  wake logs
- No Admin API error logs either (if wake is never called, the
  `except` never fires)
- Decisive evidence:
  `gcloud run services describe ... --format='value(...image)'`
  showed the image tag → cross-reference with git history.

Fix: rebuild from current HEAD → push as
`phase6-wake-462ac9e` → `gcloud run deploy --image=...` →
revision `00010-hlv`.

A derived issue uncovered during the rebuild:

- **Dockerfile missed installing Execution_Engine**:
  `API_Server/pyproject.toml` declares
  `auto-workflow-execution-engine` as an inline-mode stopgap dep
  (ADR-021 §5), but `API_Server/Dockerfile` only `pip install`s
  `./Database` + `./API_Server`. PyPI has no such package, so the
  build failed with `No matching distribution found for
  auto-workflow-execution-engine`. The previously deployed image
  `logging-fix-632d8f8` predated the addition of that dep to
  `pyproject.toml`, so production never tripped it — **a latent bug
  that only surfaces on rebuild**. Fix: add
  `COPY Execution_Engine/` + `pip install ./Execution_Engine`
  between the Database and API_Server steps (PR #98).

---

## 4. Debugging log (chronological)

```
t+0    docker build (EE) + push → OK
       terraform apply → Memorystore 5m13s, Worker Pool fail: cpu<1
       → fix variables.tf cpu="1", reapply OK
t+15   inject env into the API (CELERY_BROKER_URL, WORKER_POOL_NAME)
       → /execute returns, status=queued forever, worker pool stays at 0
t+20   manually wake via gcloud alpha run worker-pools update --instances=1
       → task picks up immediately, but 'left_field' KeyError (condition node schema mismatch)
t+25   recreate workflow with the correct (left_field/right_value/operator) schema
       → 3/3 success! but woken_log_count=0
t+30   [question] "3 successes but no wake log" — start investigating
       → because of the manual scale, the pool was already up, so wake never had to fire
t+35   resume session after compaction
       compare API image SHA (632d8f8) vs f9ecbda → discover the image is stale
       attempt Dockerfile rebuild → auto-workflow-execution-engine missing error
       → add COPY Execution_Engine + install, rebuild + push (phase6-wake-462ac9e)
t+50   deploy new image (revision 00010-hlv), scale pool→0, rerun E2E
       still no wake logs. status=queued.
t+55   dump API env → GCP_PROJECT_ID / GCP_REGION missing
       → inject both, redeploy
t+60   rerun E2E → still status=queued, pool stays at 0
       widen the log filter to severity>=ERROR
       → PermissionDenied: iam.serviceaccounts.actAs on
          1038450396751-compute@developer.gserviceaccount.com
       Wake is firing; the Admin API is the one rejecting.
t+65   grant API SA roles/iam.serviceAccountUser twice
         - on the EE SA
         - on the compute-default SA
       scale pool→0, rerun E2E
       → 3/3 success, woken_log_count=1 (exact), throttle confirmed
t+70   encode into IaC:
         - cloud_run.tf: GCP_PROJECT_ID + GCP_REGION env
         - worker.tf: actAs IAM × 2, cloudsql-proxy sidecar
         - variables.tf: cpu=1
       terraform plan → 0 add / 2 change / 0 destroy
       (the 2 changes are cosmetic provider drift, no behavior impact)
```

---

## 5. Final resolution (encoded in IaC)

### `infra/terraform/cloud_run.tf`

```hcl
env {
  name  = "GCP_PROJECT_ID"
  value = var.project_id
}
env {
  name  = "GCP_REGION"
  value = var.region
}
```

All three wake env vars (`WORKER_POOL_NAME` was already there) must
be present before `wake_worker._configured()` returns True.

### `infra/terraform/worker.tf`

```hcl
# Add the cloudsql-proxy sidecar next to the worker container
containers {
  name  = "cloudsql-proxy"
  image = var.cloudsql_proxy_image
  args = ["--private-ip", "--structured-logs", "--port=5432",
          google_sql_database_instance.main.connection_name]
  resources { limits = { cpu = "1", memory = "256Mi" } }
}

# actAs IAM bindings (both required)
resource "google_service_account_iam_member" "api_actas_ee_runtime" {
  service_account_id = google_service_account.ee_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.api.email}"
}

data "google_project" "current" { project_id = var.project_id }

resource "google_service_account_iam_member" "api_actas_compute_default" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${data.google_project.current.number}-compute@developer.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.api.email}"
}
```

### `API_Server/Dockerfile`

```dockerfile
COPY Database/ ./Database/
COPY Execution_Engine/ ./Execution_Engine/    # added
COPY API_Server/ ./API_Server/

RUN pip install --no-cache-dir ./Database \
    && pip install --no-cache-dir ./Execution_Engine \   # added
    && pip install --no-cache-dir ./API_Server
```

### `infra/scripts/run_e2e_phase21.sh`

- `-d '{}'` to dodge GCLB 411 on empty POST
- Python-free parsing (UUID regex + sed)
- Avoid catching the graph body's other `"id"` fields when extracting
  `execution_id` — use the first-UUID-match strategy

---

## 6. Postmortem — how to prevent the same problem next time

### 6-1. Silent no-ops are debugging hell

`wake_worker._configured()` was "return silently if env vars are
missing" — that's correct behavior for local / CI runs. But **when
this check fails in deployment, there are zero logs to answer "why
isn't it running?"** If the same pattern appears elsewhere, at least
log a DEBUG line like "skipping because X is missing".

### 6-2. Don't assume the deployed image matches the repo's current
code

What's in the repo and what was built into the deployed image are
**completely independent**. Merging Phase N's code doesn't mean the
running image has Phase N. The convention of putting the git SHA in
the image tag (`<feature>-<sha7>`) is the one practical mechanism
for auditing this — extract the SHA via
`gcloud run services describe | grep image` and compare with
`git log`. Takes 10 seconds.

### 6-3. GCP proto3 quirks are not in the docs

The compute-default-SA actAs check inside `workerPools.patch` is
nowhere in the official docs. It comes from how the Python SDK
serializes a partial WorkerPool — proto3 default values lead to a
server-side interpretation issue. The same trap probably exists in
other Cloud Run Admin API calls, so any future symptom of
"update_mask is narrow but I'm getting PermissionDenied on an
unrelated SA" should suspect this pattern first.

### 6-4. The 3-layer structure precludes seeing it all at once

Each layer surfaces sequentially from the outside in — fix the
image, then the env-var issue appears; fix the env vars, then the
IAM issue appears. Each layer looks like "OK, done now" until the
next one blocks. **Phase 6 lesson**: "E2E is not run once at the
end; it is run after every layer is resolved." That is the only path
to fast convergence.

### 6-5. How to verify IaC encoded the fix

Final state:
```
terraform plan -var-file=environments/staging.tfvars
→ 0 to add, 2 to change (provider cosmetic), 0 to destroy
```

That means **a clean-environment apply reproduces the same result**.
Previously, apply required three manual gcloud commands afterwards
(env injection, IAM × 2). Now one apply is enough.

---

## 7. Related PRs / commits

| Issue | PR | Commit |
|-------|----|--------|
| Encode infra IaC fully (env vars, IAM × 2, sidecar, cpu=1, runner fixes) | [#97](https://github.com/dhwang0803-glitch/teamlift/pull/97) | `30bf307` |
| Dockerfile: install Execution_Engine | [#98](https://github.com/dhwang0803-glitch/teamlift/pull/98) | `778f998` |
| (planned) ADR-021 `Update (2026-04-20)` — actAs dependency | docs branch | — |

## 8. Follow-ups

- [ ] Have `wake_worker.py` send the full `template.service_account`
      explicitly → can drop the compute-default-SA actAs binding
      (smaller blast radius)
- [ ] Add an idle scale-down watchdog (Cloud Scheduler + Cloud
      Functions, or container-side self-terminate) — MANUAL scaling
      doesn't auto-return to 0
- [ ] Write the ADR-021 `Update (2026-04-20)` section — 3-layer wake
      path requirements + the actAs quirk
- [ ] In `.github/workflows/`'s release pipeline, validate image-tag
      ↔ git-sha consistency (stamp the build SHA into a revision
      label)
