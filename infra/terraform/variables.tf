variable "project_id" {
  description = "GCP project ID (e.g. auto-workflow-staging)."
  type        = string
}

variable "region" {
  description = "GCP region. Pick one close to users/devs."
  type        = string
  default     = "asia-northeast3" # Seoul
}

variable "environment" {
  description = "Environment label — staging | prod. Used in resource names."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be staging or prod (dev uses local Docker per ADR-018)."
  }
}

variable "db_tier" {
  description = "Cloud SQL machine tier. db-g1-small for MVP; bump to db-custom-N-M for scale."
  type        = string
  default     = "db-g1-small"
}

variable "db_disk_size_gb" {
  description = "Initial SSD size. auto_resize is on, so this is a floor not a cap."
  type        = number
  default     = 10
}

variable "postgres_version" {
  description = "Postgres major version. pgvector 0.7+ requires PG 16+."
  type        = string
  default     = "POSTGRES_16"
}

variable "authorized_networks" {
  description = "CIDRs allowed to reach the Cloud SQL public IP. Keep narrow (dev IPs only). Set to [] once Cloud Run private-IP path lands."
  type = list(object({
    name  = string
    value = string
  }))
  default = []
}

variable "public_ip_enabled" {
  description = "Whether the Cloud SQL instance exposes a public IPv4. Default false (ADR-020 §2 — Private IP only). Flip true on staging only if you need laptop-direct access without Auth Proxy."
  type        = bool
  default     = false
}

# ---- VPC (ADR-020 §2) -------------------------------------------------------

variable "cloudrun_subnet_cidr" {
  description = <<-EOT
    Subnet CIDR used by Cloud Run Direct VPC Egress. /26 (64 IPs) is the
    practical minimum: /28 technically satisfies the API but ran out of
    assignable IPs under min_instance_count > 0 + the region's internal
    reservations, leaving revisions stuck with "no sufficient IP addresses
    in VPC network" (observed during 2026-04-19 prod bootstrap).

    Pick a block that does NOT overlap the services peering range
    (default 10.60.0.0/26).
  EOT
  type        = string
  default     = "10.60.0.0/26"
}

# ---- Cloud Run (ADR-020 §6) -------------------------------------------------

variable "api_image_uri" {
  description = <<-EOT
    Container image URI for the API_Server. Required — no default.

    The earlier `gcr.io/cloudrun/hello` default was dropped because `hello`
    serves `/` but not `/health`, so the startup_probe below would reject the
    first revision. Forcing an explicit image means every apply lands a real,
    probe-compatible service.

    Bootstrap (before any image exists in AR):
      terraform apply -var-file=... \
        -target=google_project_service.runtime_apis \
        -target=google_artifact_registry_repository.images
      # build + push real image to AR
      # then full apply with api_image_uri = "<region>-docker.pkg.dev/<project>/auto-workflow/api:<tag>"

    Steady state: CI `gcloud run deploy --image=...` updates the image
    out-of-band; `lifecycle.ignore_changes` on the image attribute stops
    subsequent `terraform apply` from reverting that.
  EOT
  type        = string

  validation {
    condition     = length(var.api_image_uri) > 0
    error_message = "api_image_uri must be set — see variable description for the bootstrap flow."
  }
}

variable "cloudsql_proxy_image" {
  description = "Auth Proxy sidecar image (ADR-020 §3). Pin a specific tag so the sidecar doesn't move unexpectedly under us."
  type        = string
  default     = "gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.11.4"
}

variable "api_min_instances" {
  description = "Cloud Run min instances. 0 = scale-to-zero (free at rest, ~2s cold start). Use 1 for demos (ADR-020 §Consequences, ~$7/month)."
  type        = number
  default     = 0
}

variable "api_max_instances" {
  description = "Cloud Run max instances. MVP cap kept low to bound blast radius / cost."
  type        = number
  default     = 3
}

variable "api_cpu" {
  description = "CPU allocation per instance (Cloud Run v2 resource.limits)."
  type        = string
  default     = "1"
}

variable "api_memory" {
  description = "Memory per instance."
  type        = string
  default     = "512Mi"
}

variable "app_base_url" {
  description = "APP_BASE_URL the API advertises for email verification links etc. Post-apply, set this to the Cloud Run HTTPS URL and re-apply."
  type        = string
  default     = "http://localhost:8080"
}

variable "api_allow_unauthenticated" {
  description = "When true, grants roles/run.invoker to allUsers so the service is reachable without GCP IAM. MVP / demo default. Flip false to require IAM-authenticated requests."
  type        = bool
  default     = true
}

# ---- Execution_Engine Worker Pool (ADR-021) --------------------------------

variable "ee_image_uri" {
  description = <<-EOT
    Container image URI for the Execution_Engine worker. Required — same
    bootstrap flow as var.api_image_uri (ADR-020 §6-a):
      terraform apply -target=google_artifact_registry_repository.images
      # build + push worker image
      terraform apply with ee_image_uri = "<region>-docker.pkg.dev/<project>/auto-workflow/worker:<tag>"
    Steady state: CI updates out-of-band; `lifecycle.ignore_changes` on
    the worker pool's image attribute prevents Terraform from reverting.
  EOT
  type        = string

  validation {
    condition     = length(var.ee_image_uri) > 0
    error_message = "ee_image_uri must be set — see variable description for bootstrap."
  }
}

variable "ee_worker_resources" {
  description = "Worker container resource limits. Cloud Run Worker Pools run on gen2 + always-allocated CPU, which requires cpu >= 1 (API rejects < 1). Memory 512Mi is enough for the current HTTP-heavy node mix; bump if ML/embedding nodes land on the same pool."
  type = object({
    cpu    = string
    memory = string
  })
  default = {
    cpu    = "1"
    memory = "512Mi"
  }
}

# ---- Memorystore Redis broker (ADR-021) ------------------------------------

variable "broker_tier" {
  description = "Memorystore tier. BASIC (single node, no failover) for staging; STANDARD_HA for prod (ADR-021 §9 — revisit at prod entry)."
  type        = string
  default     = "BASIC"

  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.broker_tier)
    error_message = "broker_tier must be BASIC or STANDARD_HA."
  }
}

variable "broker_memory_size_gb" {
  description = "Memorystore memory size. 1GB is the minimum BASIC shape (~$35/mo in asia-northeast3). Celery queue + SETNX keys fit comfortably under 100MB at MVP scale."
  type        = number
  default     = 1
}

variable "broker_redis_version" {
  description = "Memorystore Redis version. Pinned so provider upgrades don't silently flip. Bump via PR alongside an ADR-021 Update note."
  type        = string
  default     = "REDIS_7_2"
}

variable "db_name" {
  description = "Application database name (matches what migrate.py writes to)."
  type        = string
  default     = "auto_workflow"
}

variable "db_user" {
  description = "Application role. Separate from postgres superuser."
  type        = string
  default     = "auto_workflow"
}

variable "deletion_protection" {
  description = "Block accidental terraform destroy of the instance. Set false explicitly to tear down a staging env."
  type        = bool
  default     = true
}

# ---- AI_Agent Cloud Run GPU (PLAN_11 PR 5) ---------------------------------
#
# AI_Agent is deployed in a DIFFERENT region from the rest of the stack
# because L4 GPU quota was only granted in us-central1 (memory
# `reference_cloudrun_gpu_region`). asia-northeast3 has no L4 availability as
# of 2026-04. Cross-region HTTPS calls from API_Server are acceptable — LLM
# inference (1-2s) dominates the added ~150ms RTT.

variable "agent_region" {
  description = "GCP region for AI_Agent Cloud Run GPU service. Separate from var.region because L4 quota is region-specific."
  type        = string
  default     = "us-central1"
}

variable "agent_image_uri" {
  description = <<-EOT
    Container image URI for AI_Agent. Required — no default (same bootstrap
    pattern as api_image_uri / ee_image_uri).

    Bootstrap:
      terraform apply \
        -target=google_project_service.agent_apis \
        -target=google_artifact_registry_repository.agent_images \
        -target=google_storage_bucket.agent_models \
        -target=google_service_account.agent \
        -var-file=environments/staging.tfvars
      # upload model weights to the bucket (huggingface-cli + gsutil)
      # build + push AI_Agent image to the us-central1 AR repo
      # then full apply with this variable set

    Steady state: image drift is ignored via lifecycle.ignore_changes.
  EOT
  type        = string

  validation {
    condition     = length(var.agent_image_uri) > 0
    error_message = "agent_image_uri must be set — see variable description for the bootstrap flow."
  }
}

variable "agent_cpu" {
  description = "CPU allocation per AI_Agent instance. Cloud Run GPU requires >= 4; 8 gives headroom for concurrent llama-server + FastAPI + gcsfuse."
  type        = string
  default     = "8"
}

variable "agent_memory" {
  description = "Memory per AI_Agent instance. 32Gi fits the 26B-A4B Q4 GGUF mmap (~13GB) + KV cache (~4GB at ctx=8192) + Python + gcsfuse cache, with headroom."
  type        = string
  default     = "32Gi"
}

variable "agent_gpu_type" {
  description = "GPU accelerator type. Cloud Run supports nvidia-l4 as of 2025; upgrade path (H100/A100) requires quota bump + region review."
  type        = string
  default     = "nvidia-l4"
}

variable "agent_gpu_count" {
  description = "GPUs per instance. 1 is the only currently-supported value on Cloud Run GPU."
  type        = number
  default     = 1
}

variable "agent_min_instances" {
  description = "Min instances for AI_Agent. 0 (scale-to-zero) keeps demo cost near-zero at rest; cold start ~30-60s is covered by the startup probe budget."
  type        = number
  default     = 0
}

variable "agent_max_instances" {
  description = "Max instances for AI_Agent. Bounded at 1 because project-level L4 quota = 1 (memory `reference_cloudrun_gpu_region`). Raising this without a quota increase would just queue revisions."
  type        = number
  default     = 1
}

variable "agent_model_bucket_name" {
  description = <<-EOT
    Globally-unique GCS bucket name for AI_Agent model weights. Required —
    no default because bucket names are global and must not collide.

    Convention: `<project_id>-agent-models-<env>` (e.g.
    `autoworkflowdemo-agent-models-staging`). Terraform creates the bucket
    in var.agent_region; model weights are uploaded post-bootstrap via
    `gsutil cp` (see infra/docs/RUNBOOK_agent_deploy.md).
  EOT
  type        = string

  validation {
    condition     = length(var.agent_model_bucket_name) > 0
    error_message = "agent_model_bucket_name must be set (GCS names are global — no default)."
  }
}

variable "agent_model_object_name" {
  description = "GGUF object name inside the model bucket. Mounted at /models/<this> in the container; entrypoint.sh MODEL_PATH must match."
  type        = string
  default     = "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
}

variable "agent_ctx_size" {
  description = "llama-server context window (tokens). 8192 fits KV cache + model on L4 24GB VRAM."
  type        = number
  default     = 8192
}

variable "agent_n_gpu_layers" {
  description = "llama-server --n-gpu-layers. 999 = offload all layers to GPU (26B-A4B Q4 fits fully on L4)."
  type        = number
  default     = 999
}
