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

# ---- Bearer token for service-to-service auth ------------------------------
#
# Pivot (2026-04-22): Cloud Run GPU project-level quota (`nvidia_l4_gpu_
# allocation*`) is unassigned on this project — the Edit Quotas UI refuses
# to even accept a raise request. Cloud Run GPU is therefore unreachable on
# the hackathon timeline. We keep the GCE L4=1 quota (already granted in
# us-central1) and pivot AI_Agent onto a plain GCE VM. The Cloud Run-style
# OIDC invoker IAM is no longer available, so we replace it with a static
# bearer token stored in Secret Manager: API_Server reads it at boot and
# attaches `Authorization: Bearer <token>`; the FastAPI app checks it.
#
# Bearer-in-env is weaker than OIDC (no rotation without redeploy, no
# per-caller identity) but acceptable for a single-caller demo. Rotation
# story and stronger auth (mTLS / signed JWT from API_Server) are follow-
# ups for the live-demo phase.

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

# Both sides need read access: the VM startup script to inject the token into
# the container env, and API_Server to attach it to outbound requests.
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

# VM also needs to read GCS (gcsfuse) + log/metric + AR pull. The project-
# level log/metric writers + AR reader + bucket reader above already cover
# it. No extra binding needed.

# ---- Firewall: open :80 globally, Bearer in FastAPI handles auth -----------
#
# Cloud Run (asia-northeast3) egresses through a NAT'd pool without a stable
# IP, so a CIDR whitelist is impractical. We open port 80 universe-wide and
# lean on the bearer-token middleware for auth. Revisit for live-demo phase
# (Serverless VPC Connector + fixed egress, or IAP TCP forwarder).

resource "google_compute_firewall" "agent_allow_http" {
  name        = "auto-workflow-agent-allow-http-${var.environment}"
  network     = "default"
  description = "Allow TCP :80 to AI_Agent VM. Bearer token in FastAPI gates access."

  allow {
    protocol = "tcp"
    ports    = ["80"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["agent-llm"]

  depends_on = [google_project_service.network_apis]
}

# ---- L4 spot VM ------------------------------------------------------------
#
# Deep Learning VM image ships with NVIDIA driver + Docker + nvidia-container-
# toolkit preinstalled — saves 5-10 minutes of driver install on every boot.
# `common-cu124-*` family lines up with the CUDA 12.4 runtime the container
# was built against.
#
# Spot (provisioning_model = "SPOT") trims cost by ~70% versus on-demand.
# instance_termination_action = "STOP" means preemption halts the VM rather
# than deleting it, so manual `gcloud compute instances start` brings it back
# with the same boot disk (and any cached docker pulls).

locals {
  agent_vm_startup_script = templatefile("${path.module}/agent_vm_startup.sh.tftpl", {
    agent_region       = var.agent_region
    agent_image_uri    = var.agent_image_uri
    model_bucket       = google_storage_bucket.agent_models.name
    model_object       = var.agent_model_object_name
    bearer_secret_name = google_secret_manager_secret.agent_bearer_token.secret_id
    n_gpu_layers       = var.agent_n_gpu_layers
    ctx_size           = var.agent_ctx_size
    container_port     = 8100
  })
}

resource "google_compute_instance" "agent" {
  provider = google-beta

  name         = "auto-workflow-agent-${var.environment}"
  machine_type = var.agent_vm_machine_type
  zone         = var.agent_vm_zone

  tags = ["agent-llm"]

  # Scheduling required for any L4 attachment: TERMINATE on host maintenance,
  # no automatic restart on spot preemption (manual start is the lifecycle).
  scheduling {
    on_host_maintenance         = "TERMINATE"
    automatic_restart           = false
    provisioning_model          = "SPOT"
    instance_termination_action = "STOP"
    preemptible                 = true
  }

  guest_accelerator {
    type  = var.agent_gpu_type
    count = var.agent_gpu_count
  }

  boot_disk {
    auto_delete = true
    initialize_params {
      # DLVM CUDA 12.4 + Ubuntu 22.04 + Python 3.10. Includes docker,
      # nvidia-container-toolkit, NVIDIA driver 550+.
      image = "projects/deeplearning-platform-release/global/images/family/common-cu124-ubuntu-2204-py310"
      size  = 100
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"

    # External IP for API_Server to reach. Ephemeral (fine — API_Server
    # refreshes AI_AGENT_URL via terraform output or config redeploy).
    access_config {}
  }

  service_account {
    email  = google_service_account.agent.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  # DLVM license agreement — required by Google for ML-optimized images.
  metadata = {
    install-nvidia-driver = "False" # already present in DLVM
    enable-oslogin        = "TRUE"
    startup-script        = local.agent_vm_startup_script
  }

  # Prefer gcloud/CI for tag bumps once the VM exists. Terraform replacing the
  # instance on every image push would be painful (loses docker cache, model
  # gcsfuse remount).
  lifecycle {
    ignore_changes = [
      metadata["startup-script"],
    ]
  }

  depends_on = [
    google_project_iam_member.agent_log_writer,
    google_artifact_registry_repository_iam_member.agent_ar_reader,
    google_storage_bucket_iam_member.agent_models_reader,
    google_secret_manager_secret_iam_member.agent_sa_bearer_token,
    google_compute_firewall.agent_allow_http,
  ]
}
