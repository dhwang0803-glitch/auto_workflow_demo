# AI_Agent — GCP-side artifacts (Modal pivot 2026-04-24).
#
# AI_Agent runtime moved off GCP after the project's GPU quota stack-up
# (Cloud Run GPU 쿼터 미할당 → GCE L4 spot capacity 부족 → on-demand
# GPUS_ALL_REGIONS=0). It now runs on Modal L4 (`AI_Agent/scripts/modal_app.py`).
# This file keeps only the GCP artifacts that survive the pivot:
#
#   1. AR repo `agent_images` — pre-Modal Docker image backup (`agent:b07d8b0`).
#      Modal builds from Dockerfile directly, so the repo is no longer in the
#      hot path. Keep for rollback / future re-host.
#   2. GCS bucket `agent_models` — 15.7 GiB GGUF backup. Modal uses its own
#      Volume now, but having a GCS copy avoids re-downloading from HF if Modal
#      Volume is lost.
#   3. Service account `agent` + log/metric/AR/bucket IAM — used by anything
#      that needs to read the GCS GGUF or pull the AR image (e.g. local
#      smoke from a workstation).
#   4. Secret `agent_bearer_token` + IAM — the value also lives as the Modal
#      Secret `agent-bearer-token`. API_Server reads it from Secret Manager
#      and attaches `Authorization: Bearer <token>` when calling the Modal
#      endpoint. Keeping the secret here keeps rotation a single-write op
#      (gcloud secrets versions add → modal secret update from same value).

# ---- API enablement --------------------------------------------------------
#
# run / artifactregistry / iam are already enabled by cloud_run.tf's
# `runtime_apis` block. We only need storage here for the model bucket.

resource "google_project_service" "agent_apis" {
  for_each = toset([
    "storage.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ---- Regional Artifact Registry (us-central1) ------------------------------
#
# Pre-Modal Docker image (`agent:b07d8b0`) lives here. Modal builds from
# Dockerfile directly; this repo stays as a frozen artifact for rollback.

resource "google_artifact_registry_repository" "agent_images" {
  location      = var.agent_region
  repository_id = "auto-workflow-agent"
  format        = "DOCKER"
  description   = "AI_Agent container images (pre-Modal backup of agent:b07d8b0)."

  depends_on = [google_project_service.runtime_apis]
}

# ---- GCS bucket for model weights ------------------------------------------
#
# 15.7 GiB UD-Q4_K_M GGUF backup. Modal Volume `agent-models` is the live
# source; this bucket survives in case the Modal Volume is dropped.

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

  depends_on = [google_project_service.agent_apis]
}

# ---- Service account for AI_Agent runtime ----------------------------------
#
# Originally for the Cloud Run / GCE service. After Modal pivot the SA is no
# longer attached to a runtime, but the IAM bindings still gate access to the
# AR repo + GCS bucket above for any workstation / future rehost that needs them.

resource "google_service_account" "agent" {
  account_id   = "auto-workflow-agent-${var.environment}"
  display_name = "auto_workflow AI_Agent (${var.environment})"
  description  = "Read access to AI_Agent AR + model bucket. No runtime use after Modal pivot."

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

# ---- Bearer token for API_Server → Modal endpoint --------------------------
#
# Value lives in two places that must stay in sync:
#   - GCP Secret Manager (this resource) — API_Server fetches at boot
#   - Modal Secret `agent-bearer-token` (key AGENT_BEARER_TOKEN) — Modal
#     injects into the AgentService container env, FastAPI middleware checks
#
# Rotation: `gcloud secrets versions add agent-bearer-token-<env>
# --data-file=<new>` then `modal secret update agent-bearer-token
# AGENT_BEARER_TOKEN=<same>`. Both API_Server (Cloud Run env from this
# secret) and Modal need to redeploy / pick up the new value.

resource "random_password" "agent_bearer_token" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "agent_bearer_token" {
  secret_id = "agent-bearer-token-${var.environment}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "agent_bearer_token" {
  secret      = google_secret_manager_secret.agent_bearer_token.id
  secret_data = random_password.agent_bearer_token.result
}

# Both sides need read access: agent SA (kept for parity / future rehost),
# and api SA (API_Server fetches the token at boot to attach to Modal calls).
resource "google_secret_manager_secret_iam_member" "agent_sa_bearer_token" {
  secret_id = google_secret_manager_secret.agent_bearer_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent.email}"
}

resource "google_secret_manager_secret_iam_member" "api_sa_bearer_token" {
  secret_id = google_secret_manager_secret.agent_bearer_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}
