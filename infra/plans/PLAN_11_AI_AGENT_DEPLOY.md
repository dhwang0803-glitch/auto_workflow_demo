# PLAN_11 · PR 5 — AI_Agent Cloud Run GPU deployment (infra)

> **Branch**: `feature/infra-ai-agent-cloudrun-gpu` → `main`
> **Drafted**: 2026-04-22
> **Upstream PLAN**: `AI_Agent/plans/PLAN_11_HACKATHON_SUBMISSION.md` (hackathon submission)
> **Depends on**: AI_Agent PR 2 (Dockerfile + entrypoint, merged #114)

## 1. Goals

Add Terraform resources + a runbook so the `AI_Agent` container can run on
Cloud Run GPU (NVIDIA L4). This is the runtime foundation for the hackathon
live-demo URL.

Exit criteria:
- `terraform plan -var-file=environments/staging.tfvars` shows only `+`
  for new resources (zero changes to existing ones).
- Following the runbook in order, `curl https://<agent-url>/v1/health`
  returns 200 with a service-account ID token (actual apply in a separate
  session).

## 2. Locked decisions

| Item | Decision | Rationale |
|---|---|---|
| Region | Separate variable `agent_region = us-central1` | L4 quota only secured in us-central1 (memory `reference_cloudrun_gpu_region`). Keep the existing stack on `asia-northeast3` |
| Artifact Registry | New repo `agent_images` (us-central1) | The existing `auto-workflow` is in asia-northeast3. Avoids cross-region pull cold-start latency |
| Model weights | GCS bucket (us-central1) + Cloud Run v2 GCS volume mount | Baking into the image yields 12GB+ images → registry cost / upload latency. Mount uses built-in gcsfuse |
| Service Account | Dedicated `auto-workflow-agent-${env}` | Separate from API / EE SAs. GCS read + AR read + logging/monitoring only |
| Public invoker | **No** | Internal svc-to-svc. Only `google_service_account.api` → `roles/run.invoker` |
| GPU | `nvidia-l4 × 1`, `gpu_zonal_redundancy_disabled = true` | Single-instance hackathon. Zonal redundancy off = cost saving |
| Startup probe | `/v1/health` port 8100, `initial_delay=60s`, `failure_threshold=60`, `period=5s` → up to ≈ 5 minutes | 30–60s for 26B-A4B Q4 mmap + KV cache init plus margin |
| min / max instances | 0 / 1 | L4 quota = 1 (memory). Scale-to-zero required (budget) |
| Image drift | `lifecycle.ignore_changes = [template[0].containers[0].image]` | Matches existing api / worker pattern |

## 3. Resource inventory

### New files
- `infra/terraform/ai_agent.tf`
- `infra/docs/RUNBOOK_agent_deploy.md`
- `infra/plans/PLAN_11_AI_AGENT_DEPLOY.md` (this document)

### Modified files
- `infra/terraform/variables.tf` — add `agent_*` variables
- `infra/terraform/outputs.tf` — add `agent_*` outputs
- `infra/terraform/environments/staging.tfvars.example` — agent variable examples
- `infra/terraform/environments/prod.tfvars.example` — agent variable examples

### Terraform resources (ai_agent.tf)
1. `google_project_service.agent_apis` — enable the storage API (run / AR / iam are already enabled in `runtime_apis`)
2. `google_artifact_registry_repository.agent_images` — us-central1 docker repo
3. `google_storage_bucket.agent_models` — us-central1, uniform access, versioning off
4. `google_service_account.agent` — agent runtime SA
5. IAM bindings (agent SA):
   - `roles/logging.logWriter` (project)
   - `roles/monitoring.metricWriter` (project)
   - `roles/artifactregistry.reader` (agent_images repo)
   - `roles/storage.objectViewer` (agent_models bucket)
6. `google_cloud_run_v2_service.agent` — GPU service
7. `google_cloud_run_v2_service_iam_member.api_invokes_agent` — api SA → agent service invoker

## 4. Variable contract

```hcl
variable "agent_region"              # string, default "us-central1"
variable "agent_image_uri"           # string, REQUIRED (no default)
variable "agent_cpu"                 # string, default "8"
variable "agent_memory"              # string, default "32Gi"
variable "agent_gpu_type"            # string, default "nvidia-l4"
variable "agent_gpu_count"           # number, default 1
variable "agent_min_instances"       # number, default 0
variable "agent_max_instances"       # number, default 1
variable "agent_model_bucket_name"   # string, REQUIRED (globally unique)
variable "agent_model_object_name"   # string, default "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
variable "agent_ctx_size"            # number, default 8192
variable "agent_n_gpu_layers"        # number, default 999
```

## 5. Bootstrap order (runbook summary)

1. Apply the GCS bucket + AR repo + SA first (`-target=`):
   ```
   terraform apply -target=google_storage_bucket.agent_models \
                   -target=google_artifact_registry_repository.agent_images \
                   -target=google_service_account.agent \
                   -var-file=environments/staging.tfvars
   ```
2. `huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF gemma-4-26B-A4B-it-UD-Q4_K_M.gguf ...`
3. `gsutil cp <local.gguf> gs://<agent_model_bucket_name>/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf`
4. `docker build -f AI_Agent/Dockerfile -t <agent-image-uri> .`
5. `docker push <agent-image-uri>` (us-central1 AR)
6. Set `agent_image_uri` in `staging.tfvars`, then apply everything.
7. `curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" <agent-url>/v1/health`

## 6. Out of scope (next PR)

- **API_Server-side wiring** (`AI_AGENT_URL`, ID-token exchange, runtime
  injection of `AIAgentHTTPBackend`'s base_url) — done in a separate
  follow-up PR per branch after this PR merges.
- **Domain / custom URL** — the hackathon submission's live-demo URL is
  the API_Server run.app URL itself. AI_Agent is internal-only and needs
  no separate domain.
- **Scale-down watchdog** — with min=0 + scale-to-zero by default, no
  manual scale-down is needed (the GPU itself supports request-driven
  scale-to-zero).

## 7. Risks + mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Bounded by L4 quota = 1 → max=1 | Reduced concurrency | Single-host hackathon demo. Reconsider quota increase after submission |
| GCS-mount-dependent gcsfuse latency | Extends cold start | Stays warm after mmap. `failure_threshold=60` (5 min) accommodates |
| Cross-region (agent us-central1 ↔ api asia-northeast3) | ~150 ms latency | Compose is dominated by LLM inference (1–2 s). Relative increment negligible |
| Cloud Run GPU beta-field drift | terraform plan noise | Do not include GPU-related fields in `ignore_changes` (reconsider when the provider stabilizes) |
| Model upload latency (~13 GB @ 30–60 min) | Lengthens bootstrap | Note in the runbook + background-execution guide |

## 8. Related

- `docs/context/decisions.md` — ADR-020 Cloud Run v2 + Artifact Registry pattern (template)
- `docs/context/decisions.md` — ADR-021 Worker Pools pattern (SA-separation principle)
- auto-memory `project_gemma4_model_decisions.md` — basis for model / serving choices
- auto-memory `reference_cloudrun_gpu_region.md` — basis for region / quota
