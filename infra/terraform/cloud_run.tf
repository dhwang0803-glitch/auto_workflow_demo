# ADR-020 — API_Server deploy on Cloud Run v2 with:
#   - Dedicated IAM service account (ADR-020 §4)
#   - Direct VPC Egress (ADR-020 §2; Serverless VPC Connector v1 rejected)
#   - Cloud SQL Auth Proxy sidecar (ADR-020 §3)
#   - Secret Manager injection via secret_key_ref (ADR-020 §5)
#
# Execution_Engine is not deployed here (ADR-021 decides Worker Pools vs
# Cloud Tasks). Only the image registry is shared.

# ---- API enablement ---------------------------------------------------------

resource "google_project_service" "runtime_apis" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ---- Artifact Registry -------------------------------------------------------
#
# One docker repo per project (region-pinned). Both API_Server and future
# Execution_Engine images live under the same repo, separated by image name.

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "auto-workflow"
  format        = "DOCKER"
  description   = "API_Server + Execution_Engine container images (ADR-020)."

  depends_on = [google_project_service.runtime_apis]
}

# ---- Service Account for API_Server -----------------------------------------

resource "google_service_account" "api" {
  account_id   = "auto-workflow-api-${var.environment}"
  display_name = "auto_workflow API_Server (${var.environment})"
  description  = "ADR-020 §4 — runs Cloud Run API service. Minimal roles only."

  depends_on = [google_project_service.runtime_apis]
}

# Project-scoped roles. IAM principle: prefer per-resource bindings where
# possible — Secret Manager bindings below are per-secret, not project-wide.
resource "google_project_iam_member" "api_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "api_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "api_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# Per-secret accessor bindings. Cheap to list explicitly; keeps blast radius
# tight if we later add secrets the API should NOT see (e.g., an admin key).
resource "google_secret_manager_secret_iam_member" "api_db_password" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_credential_master_key" {
  secret_id = google_secret_manager_secret.credential_master_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_jwt_secret" {
  secret_id = google_secret_manager_secret.jwt_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

# ADR-019 Phase 6 — OAuth secrets. Read by API_Server's Settings on boot;
# missing values make GoogleOAuthClient None, which makes /oauth/google/*
# return 503. Test containers bypass these via env overrides.
resource "google_secret_manager_secret_iam_member" "api_google_oauth_client_id" {
  secret_id = google_secret_manager_secret.google_oauth_client_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_google_oauth_client_secret" {
  secret_id = google_secret_manager_secret.google_oauth_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_google_oauth_redirect_uri" {
  secret_id = google_secret_manager_secret.google_oauth_redirect_uri.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

# ---- Cloud Run service ------------------------------------------------------
#
# Two containers:
#   1. api       — our API_Server image. var.api_image_uri is REQUIRED (no
#                  default); the earlier gcr.io/cloudrun/hello bootstrap was
#                  dropped because it fails the /health startup_probe below.
#                  Bootstrap flow lives in variables.tf. After first apply, CI
#                  updates the image out-of-band; ignore_changes prevents
#                  Terraform from reverting it on the next plan.
#   2. cloudsql-proxy — the Auth Proxy sidecar. App talks to localhost:5432.

locals {
  # Non-secret env. DATABASE_URL comes from Secret Manager (main.tf) so the
  # Terraform state never holds the DSN with password. APP_BASE_URL defaults
  # to the Cloud Run URL post-apply; pre-apply we feed a var.
  api_static_env = {
    APP_BASE_URL = var.app_base_url
    EMAIL_SENDER = "console"
  }
}

resource "google_cloud_run_v2_service" "api" {
  name     = "auto-workflow-api-${var.environment}"
  location = var.region

  # Reuse the Cloud SQL deletion_protection flag — in practice we tear both
  # down together (demo / staging) or keep both (prod).
  deletion_protection = var.deletion_protection

  # INGRESS_TRAFFIC_ALL lets the public internet hit the service; auth/authz
  # is handled by the app itself. For internal-only deployments, switch to
  # INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER.
  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    # Direct VPC Egress (ADR-020 §2). PRIVATE_RANGES_ONLY keeps public
    # egress via the default Cloud Run path — we only tunnel through the
    # VPC when hitting RFC1918 destinations (i.e., Cloud SQL private IP).
    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.cloudrun.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      name  = "api"
      image = var.api_image_uri

      ports {
        container_port = 8080
      }

      dynamic "env" {
        for_each = local.api_static_env
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "JWT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt_secret.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "CREDENTIAL_MASTER_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.credential_master_key.secret_id
            version = "latest"
          }
        }
      }

      # ADR-019 Phase 6 — Google OAuth2. Upload real values via
      # `gcloud secrets versions add` after registering a client in the
      # GCP Console (see deploy/README_oauth.md). The API keeps serving
      # non-OAuth traffic if these stay at placeholder values; only the
      # /oauth/google/* routes and google_oauth credential_type break.
      env {
        name = "GOOGLE_OAUTH_CLIENT_ID"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_oauth_client_id.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "GOOGLE_OAUTH_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_oauth_client_secret.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "GOOGLE_OAUTH_REDIRECT_URI"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_oauth_redirect_uri.secret_id
            version = "latest"
          }
        }
      }

      # ADR-021 — wake-up target + broker URL. Both non-secret. WORKER_POOL_NAME
      # is the bare name (not the fully-qualified resource path); wake_worker.py
      # composes `projects/<project>/locations/<region>/workerPools/<name>` from
      # the three GCP_* env vars below. All three are required — wake_worker's
      # `_configured()` check short-circuits to a no-op if any is empty, which
      # silently disables the wake path in a deployed environment.
      env {
        name  = "WORKER_POOL_NAME"
        value = google_cloud_run_v2_worker_pool.ee.name
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "GCP_REGION"
        value = var.region
      }

      env {
        name  = "CELERY_BROKER_URL"
        value = "redis://${google_redis_instance.broker.host}:${google_redis_instance.broker.port}/0"
      }

      # PLAN_11 — AI_Agent (Modal) endpoint + bearer. Empty AI_AGENT_BASE_URL
      # falls back to the in-tree Anthropic/Stub backend (container.py guard),
      # so a fresh staging boot before Modal is deployed still works.
      env {
        name  = "AI_AGENT_BASE_URL"
        value = var.ai_agent_base_url
      }

      env {
        name = "AGENT_BEARER_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.agent_bearer_token.secret_id
            version = "latest"
          }
        }
      }

      resources {
        limits = {
          cpu    = var.api_cpu
          memory = var.api_memory
        }
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 12
        timeout_seconds       = 3
      }
    }

    containers {
      name  = "cloudsql-proxy"
      image = var.cloudsql_proxy_image

      # Proxy listens on 127.0.0.1:5432 for the app container. --private-ip
      # because Cloud SQL only has a private IP now (ADR-020 §2).
      args = [
        "--private-ip",
        "--structured-logs",
        "--port=5432",
        google_sql_database_instance.main.connection_name,
      ]

      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
      }
    }
  }

  # Initial deploys may race with IAM propagation; the retry on apply works,
  # but making the dep explicit avoids a flaky first `terraform apply`.
  depends_on = [
    google_project_iam_member.api_cloudsql_client,
    google_secret_manager_secret_iam_member.api_database_url,
    google_secret_manager_secret_iam_member.api_credential_master_key,
    google_secret_manager_secret_iam_member.api_jwt_secret,
    google_secret_manager_secret_iam_member.api_google_oauth_client_id,
    google_secret_manager_secret_iam_member.api_google_oauth_client_secret,
    google_secret_manager_secret_iam_member.api_google_oauth_redirect_uri,
    google_secret_manager_secret_iam_member.api_sa_bearer_token,
  ]

  lifecycle {
    # The image is updated out-of-band by CI (release branch push) or by a
    # manual `gcloud run deploy --image=...` on the development branch.
    # Terraform re-planning would otherwise want to roll back to var.api_image_uri.
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}

# ---- Public invoker (toggleable) --------------------------------------------
#
# Without this binding Cloud Run rejects unauthenticated callers with 403.
# For MVP / demo we need the public HTTPS URL reachable by browsers and
# external webhooks. Flip `api_allow_unauthenticated = false` to require IAM.

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  count    = var.api_allow_unauthenticated ? 1 : 0
  project  = google_cloud_run_v2_service.api.project
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
