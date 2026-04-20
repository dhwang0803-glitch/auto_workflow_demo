# ADR-021 — Execution_Engine Cloud Run Worker Pools deployment.
#
# Long-running Celery worker container (no HTTP listener). Pulls tasks from
# Memorystore (broker in memorystore.tf) and writes execution state to Cloud
# SQL via the same DATABASE_URL secret the API uses. Scale-to-zero by default;
# API_Server wakes this pool via the Cloud Run Admin API (ADR-021 §4, §5).
#
# Resource naming follows ADR-020 Cloud Run API pattern but with `-ee-` slug.
# SA is SEPARATE from the API SA so the blast radius of a worker compromise
# stays contained to task execution (not user-facing API routes).

# ---- Service Account for Execution_Engine worker ----------------------------

resource "google_service_account" "ee_runtime" {
  account_id   = "auto-workflow-ee-${var.environment}"
  display_name = "auto_workflow Execution_Engine worker (${var.environment})"
  description  = "ADR-021 §6 — runs Cloud Run Worker Pools. Cloud SQL + Secret Manager access only."

  depends_on = [google_project_service.runtime_apis]
}

# Cloud SQL client — worker connects to the same Postgres the API writes to.
resource "google_project_iam_member" "ee_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.ee_runtime.email}"
}

resource "google_project_iam_member" "ee_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.ee_runtime.email}"
}

resource "google_project_iam_member" "ee_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.ee_runtime.email}"
}

# Per-secret bindings. Worker needs DATABASE_URL + CREDENTIAL_MASTER_KEY
# (to decrypt OAuth tokens for Workspace nodes) + OAuth client id/secret (for
# refresh-token exchange). It does NOT get JWT_SECRET or the redirect URI —
# those are API-only concerns.
resource "google_secret_manager_secret_iam_member" "ee_database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ee_runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "ee_credential_master_key" {
  secret_id = google_secret_manager_secret.credential_master_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ee_runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "ee_google_oauth_client_id" {
  secret_id = google_secret_manager_secret.google_oauth_client_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ee_runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "ee_google_oauth_client_secret" {
  secret_id = google_secret_manager_secret.google_oauth_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ee_runtime.email}"
}

# ---- Cloud Run Worker Pool --------------------------------------------------

resource "google_cloud_run_v2_worker_pool" "ee" {
  provider = google-beta
  name     = "auto-workflow-ee-${var.environment}"
  location = var.region

  # Shared flag with Cloud SQL / API — staging teardown flips both together,
  # prod keeps the default true.
  deletion_protection = var.deletion_protection

  # Worker Pool scaling is a TOP-LEVEL block, not nested under template
  # (contrast with google_cloud_run_v2_service). scaling_mode = AUTOMATIC
  # + min=0 lets the API-triggered wake-up (ADR-021 §4) drive instance
  # count; idle timeout returns it to 0.
  scaling {
    scaling_mode       = "AUTOMATIC"
    min_instance_count = 0
    max_instance_count = var.ee_worker_max_instances
  }

  template {
    service_account = google_service_account.ee_runtime.email

    # Direct VPC Egress onto the same subnet API_Server uses. Worker needs
    # RFC1918 reachability for both Cloud SQL (peering range) and Memorystore
    # (peering range). PRIVATE_RANGES_ONLY keeps outbound HTTPS (Google APIs)
    # on the default Cloud Run egress path.
    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.cloudrun.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      name  = "worker"
      image = var.ee_image_uri

      resources {
        limits = {
          cpu    = var.ee_worker_resources.cpu
          memory = var.ee_worker_resources.memory
        }
      }

      # Celery broker URL composed from the Memorystore instance. The host
      # attribute populates after apply (reachable only inside the peered
      # VPC). DB 0 chosen by convention; no other redis consumer yet.
      env {
        name  = "CELERY_BROKER_URL"
        value = "redis://${google_redis_instance.broker.host}:${google_redis_instance.broker.port}/0"
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
        name = "CREDENTIAL_MASTER_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.credential_master_key.secret_id
            version = "latest"
          }
        }
      }

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
    }
  }

  depends_on = [
    google_project_iam_member.ee_cloudsql_client,
    google_secret_manager_secret_iam_member.ee_database_url,
    google_secret_manager_secret_iam_member.ee_credential_master_key,
    google_secret_manager_secret_iam_member.ee_google_oauth_client_id,
    google_secret_manager_secret_iam_member.ee_google_oauth_client_secret,
    google_redis_instance.broker,
  ]

  lifecycle {
    # Image updates land via CI / manual gcloud after first apply — same
    # pattern as ADR-020 §6-a on the API service.
    #
    # min_instance_count drifts every time API_Server wakes the pool
    # (services.patch bumps it to 1, idle timeout returns it to 0). Ignore
    # so `terraform plan` stays clean.
    ignore_changes = [
      template[0].containers[0].image,
      scaling[0].min_instance_count,
      client,
      client_version,
    ]
  }
}

# ---- API_Server wake-up permission ------------------------------------------
#
# API_Server's workflow_service.execute_workflow() calls Cloud Run Admin API
# `services.patch` against THIS worker pool to bump min_instance_count = 1.
# Scope is tightened two ways:
#   1. Resource-level binding (this pool only, not project-wide)
#   2. `roles/run.developer` is the least predefined role that includes
#      run.workerPools.update. Narrow to a custom role post-Phase-6 once the
#      exact permission set is validated in production logs.
resource "google_cloud_run_v2_worker_pool_iam_member" "api_wake_permission" {
  provider = google-beta
  project  = google_cloud_run_v2_worker_pool.ee.project
  location = google_cloud_run_v2_worker_pool.ee.location
  name     = google_cloud_run_v2_worker_pool.ee.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.api.email}"
}
