# PLAN_11 PR 5 — AI_Agent Cloud Run GPU deployment (Kaggle Gemma 4 Hackathon).
#
# AI_Agent runs Gemma 4 26B-A4B Q4 GGUF via llama.cpp on NVIDIA L4. It is
# deployed in a DIFFERENT region (var.agent_region, default us-central1) from
# the rest of the stack (var.region, default asia-northeast3) because L4 quota
# is only available in us-central1 as of 2026-04 (memory
# `reference_cloudrun_gpu_region`). Cross-region HTTPS calls from API_Server
# are acceptable — LLM inference latency (1-2s) dominates the added ~150ms RTT.
#
# Notable divergence from the API_Server pattern:
#   - No VPC egress. AI_Agent doesn't hit Cloud SQL or Memorystore; keeping it
#     out of the VPC avoids cross-region peering complexity.
#   - No Secret Manager env. Model weights are public (Gemma 4) and there is
#     no per-env credential — API_Server mediates all authenticated I/O.
#   - Dedicated regional Artifact Registry (var.agent_region) so L4 cold start
#     doesn't wait on a cross-region image pull from asia-northeast3.
#   - GCS volume mount for model weights — baking a 13GB GGUF into the image
#     would make pushes painful and AR storage cost irrational.

# ---- API enablement --------------------------------------------------------
#
# run / artifactregistry / iam are already enabled by cloud_run.tf's
# `runtime_apis` block. We only need to add storage here for the model bucket.

resource "google_project_service" "agent_apis" {
  for_each = toset([
    "storage.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ---- Regional Artifact Registry (us-central1) ------------------------------

resource "google_artifact_registry_repository" "agent_images" {
  location      = var.agent_region
  repository_id = "auto-workflow-agent"
  format        = "DOCKER"
  description   = "AI_Agent container images (PLAN_11 PR 5). Region-pinned to agent_region so Cloud Run GPU cold start doesn't wait on cross-region image pull."

  depends_on = [google_project_service.runtime_apis]
}

# ---- GCS bucket for model weights ------------------------------------------
#
# Model weights are mmap'd from a GCS bucket via Cloud Run v2's native gcsfuse
# volume mount (google-beta). Versioning is off — GGUF files are immutable by
# filename; promoting a new quantization means a new object name, not a new
# version of the old one.

resource "google_storage_bucket" "agent_models" {
  name                        = var.agent_model_bucket_name
  location                    = var.agent_region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = !var.deletion_protection
  public_access_prevention    = "enforced"

  versioning {
    enabled = false
  }

  # Model weights are large (~13GB) and rarely change. No lifecycle transition
  # here — STANDARD reads are what llama.cpp needs. Revisit with NEARLINE once
  # weights stabilize.

  depends_on = [google_project_service.agent_apis]
}

# ---- Service account for AI_Agent runtime ----------------------------------

resource "google_service_account" "agent" {
  account_id   = "auto-workflow-agent-${var.environment}"
  display_name = "auto_workflow AI_Agent (${var.environment})"
  description  = "PLAN_11 PR 5 — runs the AI_Agent Cloud Run GPU service. Read-only access to AR + model bucket, no DB/Secret Manager."

  depends_on = [google_project_service.runtime_apis]
}

resource "google_project_iam_member" "agent_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.agent.email}"
}

resource "google_project_iam_member" "agent_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.agent.email}"
}

# Per-resource IAM: agent pulls its own image from the us-central1 AR, and
# reads model objects from the us-central1 bucket. Neither binding is project-
# wide — other buckets / registries stay invisible to this SA.
resource "google_artifact_registry_repository_iam_member" "agent_ar_reader" {
  project    = google_artifact_registry_repository.agent_images.project
  location   = google_artifact_registry_repository.agent_images.location
  repository = google_artifact_registry_repository.agent_images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.agent.email}"
}

resource "google_storage_bucket_iam_member" "agent_models_reader" {
  bucket = google_storage_bucket.agent_models.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.agent.email}"
}

# ---- Cloud Run GPU service -------------------------------------------------
#
# google-beta because GCS volume mounts and GPU zonal-redundancy flag are
# still beta-only in the Terraform provider as of 2026-04. The Worker Pool in
# worker.tf already uses google-beta, so the provider alias is configured.
#
# Ingress INTERNAL only (svc-to-svc). Public access is explicitly blocked by
# the absence of an `allUsers` roles/run.invoker binding — API_Server SA is
# the sole caller.

resource "google_cloud_run_v2_service" "agent" {
  provider = google-beta

  name     = "auto-workflow-agent-${var.environment}"
  location = var.agent_region

  deletion_protection = var.deletion_protection

  # Internal only — not reachable from the public internet. API_Server
  # (asia-northeast3) calls this with an OIDC ID token scoped to its SA.
  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.agent.email

    # Cloud Run GPU is gen2-only; request-scoped concurrency is low because
    # llama-server is compute-bound on the GPU. One concurrent request per
    # instance keeps tail latency predictable.
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    max_instance_request_concurrency = 1

    scaling {
      min_instance_count = var.agent_min_instances
      max_instance_count = var.agent_max_instances
    }

    # Pin the GPU to the node selector. Provider version must support this
    # field (google-beta >= 5.x). Without it the container schedules on CPU-
    # only nodes and llama-server fails to load layers onto any GPU.
    node_selector {
      accelerator = var.agent_gpu_type
    }

    # Cloud Run GPU has separate quota buckets for zonally-redundant vs
    # non-redundant. Non-redundant saves on hot-standby cost but requires its
    # own quota line — the default `L4 GPUs` quota only covers the redundant
    # variant (2026-04 GCP staging). Keep redundancy ON until the non-redundant
    # quota lands; scale-to-zero means idle cost is $0 regardless of variant.
    gpu_zonal_redundancy_disabled = false

    # Model weights live in GCS. Cloud Run v2 gcsfuse volume does the mount;
    # the container treats /models as a read-only filesystem.
    volumes {
      name = "models"
      gcs {
        bucket    = google_storage_bucket.agent_models.name
        read_only = true
      }
    }

    containers {
      name  = "agent"
      image = var.agent_image_uri

      # FastAPI listens on 8100 per AI_Agent/scripts/entrypoint.sh. Cloud Run
      # sets the PORT env to container_port, which entrypoint then threads
      # through to uvicorn.
      ports {
        container_port = 8100
      }

      env {
        name  = "LLM_BACKEND"
        value = "llamacpp"
      }

      env {
        name  = "LLAMA_SERVER_URL"
        value = "http://127.0.0.1:8080"
      }

      env {
        name  = "MODEL_PATH"
        value = "/models/${var.agent_model_object_name}"
      }

      env {
        name  = "N_GPU_LAYERS"
        value = tostring(var.agent_n_gpu_layers)
      }

      env {
        name  = "CTX_SIZE"
        value = tostring(var.agent_ctx_size)
      }

      resources {
        limits = {
          cpu              = var.agent_cpu
          memory           = var.agent_memory
          "nvidia.com/gpu" = tostring(var.agent_gpu_count)
        }
        # Cloud Run GPU requires always-allocated CPU (not request-scoped).
        # CPU-idle is incompatible with llama-server holding VRAM warm.
        cpu_idle          = false
        startup_cpu_boost = true
      }

      volume_mounts {
        name       = "models"
        mount_path = "/models"
      }

      # Startup budget sized for cold gcsfuse + 13GB GGUF mmap + full GPU
      # layer offload. Worst observed on similar setups is ~5 minutes; the
      # 10-minute ceiling here leaves headroom without masking a hang.
      startup_probe {
        http_get {
          path = "/v1/health"
          port = 8100
        }
        initial_delay_seconds = 30
        period_seconds        = 10
        failure_threshold     = 60
        timeout_seconds       = 5
      }

      liveness_probe {
        http_get {
          path = "/v1/health"
          port = 8100
        }
        period_seconds    = 30
        failure_threshold = 3
        timeout_seconds   = 5
      }
    }

    # Cloud Run default request timeout is 5 minutes. Compose calls can run
    # long under low-concurrency streaming; 15 minutes leaves room for a
    # multi-round compose without hitting the ceiling.
    timeout = "900s"
  }

  depends_on = [
    google_project_iam_member.agent_log_writer,
    google_artifact_registry_repository_iam_member.agent_ar_reader,
    google_storage_bucket_iam_member.agent_models_reader,
  ]

  lifecycle {
    # Image drift ignored — CI (or manual `gcloud run deploy`) bumps the tag
    # out-of-band, same pattern as api_image_uri / ee_image_uri.
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}

# ---- Invoker IAM: only API_Server SA may call /v1/* ------------------------
#
# No `allUsers` binding — AI_Agent is not exposed to the public internet.
# API_Server's `AIAgentHTTPBackend` will mint an OIDC ID token for this
# service's audience and pass it as `Authorization: Bearer <token>`. That
# wiring lands in a follow-up API_Server PR (PLAN_11 post-PR-5).

resource "google_cloud_run_v2_service_iam_member" "api_invokes_agent" {
  provider = google-beta

  project  = google_cloud_run_v2_service.agent.project
  location = google_cloud_run_v2_service.agent.location
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.api.email}"
}
