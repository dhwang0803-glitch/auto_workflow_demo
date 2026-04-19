# ADR-018 — GCP Cloud SQL PostgreSQL + Secret Manager.
#
# Provisions one Postgres 16 instance per environment (staging, prod) plus
# the three secrets the application needs. Secret VALUES are left as
# placeholders here on purpose: actual keys are rotated via console/CLI so
# Terraform state never contains production credentials.
#
# Apply:
#   cd Database/deploy/terraform
#   terraform init
#   terraform apply -var-file=environments/staging.tfvars
# Teardown (staging only — prod has deletion_protection):
#   terraform destroy -var-file=environments/staging.tfvars

provider "google" {
  project = var.project_id
  region  = var.region
}

# ---- API enablement ---------------------------------------------------------
# Explicit so `terraform apply` on a fresh project doesn't error on the first
# resource that needs an un-enabled API.

locals {
  required_apis = [
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each                   = toset(local.required_apis)
  service                    = each.value
  disable_dependent_services = false
  disable_on_destroy         = false
}

# ---- Cloud SQL --------------------------------------------------------------

resource "random_password" "db_app" {
  length  = 32
  special = false # asyncpg DSN quoting is less painful without special chars
}

resource "google_sql_database_instance" "main" {
  name                = "auto-workflow-${var.environment}"
  region              = var.region
  database_version    = var.postgres_version
  deletion_protection = var.deletion_protection

  settings {
    # ENTERPRISE edition only — ENTERPRISE_PLUS rejects shared-core tiers
    # (db-g1-small etc.) and starts at db-perf-optimized-N-1 (~$400+/mo).
    edition           = "ENTERPRISE"
    tier              = var.db_tier
    disk_type         = "PD_SSD"
    disk_size         = var.db_disk_size_gb
    disk_autoresize   = true
    availability_type = "ZONAL" # switch to REGIONAL when HA matters

    backup_configuration {
      enabled                        = true
      start_time                     = "17:00" # 02:00 KST
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    ip_configuration {
      # ADR-020 §2: prod keeps public IP off. staging may opt in via var to
      # allow dev laptops to hit the instance directly while Cloud Run path
      # is being validated. Auth Proxy works in either case.
      ipv4_enabled                                  = var.public_ip_enabled
      private_network                               = google_compute_network.vpc.id
      enable_private_path_for_google_cloud_services = true

      dynamic "authorized_networks" {
        for_each = var.authorized_networks
        content {
          name  = authorized_networks.value.name
          value = authorized_networks.value.value
        }
      }
    }

    database_flags {
      name  = "cloudsql.enable_pgaudit"
      value = "on"
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 18
      update_track = "stable"
    }
  }

  depends_on = [
    google_project_service.apis,
    google_service_networking_connection.private_vpc_connection,
  ]
}

# pgvector extension: instance-level flag is unavailable; extension is created
# via SQL (CREATE EXTENSION vector) from the first migration / connection.
# schemas/001_core.sql already handles it.

resource "google_sql_database" "app" {
  name     = var.db_name
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app" {
  name     = var.db_user
  instance = google_sql_database_instance.main.name
  password = random_password.db_app.result
}

# ---- Secret Manager ---------------------------------------------------------
#
# Three application secrets (ADR-018 §4). DB password is auto-generated above
# and mirrored into Secret Manager. Fernet and JWT secrets are left with
# placeholder versions — rotate them post-apply before any real traffic.

resource "google_secret_manager_secret" "db_password" {
  secret_id = "db-password-${var.environment}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_app.result
}

resource "google_secret_manager_secret" "credential_master_key" {
  secret_id = "credential-master-key-${var.environment}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "credential_master_key_placeholder" {
  secret = google_secret_manager_secret.credential_master_key.id

  # VALID-BUT-PLACEHOLDER Fernet key: 44-char URL-safe base64 of 32 bytes.
  # Chosen so Fernet.__init__ in the app container doesn't crash on first
  # deploy (that crash was the root cause of the 2026-04-19 prod bootstrap
  # /health failure when this was "REPLACE_ME_WITH_Fernet_generate_key").
  # Crucially, this key MUST be rotated before any real credential is
  # stored — the "PLACEHOLDER" substring makes that intent visible to
  # anyone who sees the secret value. Rotation: see deploy/README.md
  # "시크릿 R/W 패턴" section.
  secret_data = "PLACEHOLDERPLACEHOLDERPLACEHOLDERPLACEHOLDE="

  lifecycle {
    # Prevent terraform from overwriting a real key injected out-of-band.
    ignore_changes = [secret_data]
  }
}

resource "google_secret_manager_secret" "jwt_secret" {
  secret_id = "jwt-secret-${var.environment}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "jwt_secret_placeholder" {
  secret = google_secret_manager_secret.jwt_secret.id

  # Valid placeholder so API_Server's JWT init doesn't reject it. 64 chars
  # of explicit "PLACEHOLDER" signalling — rotate before issuing real
  # tokens. See deploy/README.md "시크릿 R/W 패턴" section.
  secret_data = "PLACEHOLDER_JWT_SECRET_REPLACE_BEFORE_REAL_TRAFFIC_XXXXXXXXXXXXX"

  lifecycle {
    ignore_changes = [secret_data]
  }
}

# Composed DSN for Cloud Run. The app reads DATABASE_URL as-is (ADR-020 §5 +
# PR #66: psycopg3 sync derived from +asyncpg). Host is fixed at 127.0.0.1
# because the Auth Proxy sidecar (cloud_run.tf) listens there.
# Keeping db-password as a separate secret too so ops scripts (migrate.py
# via a laptop-side Auth Proxy) can fetch just the password.
resource "google_secret_manager_secret" "database_url" {
  secret_id = "database-url-${var.environment}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = "postgresql+asyncpg://${google_sql_user.app.name}:${random_password.db_app.result}@127.0.0.1:5432/${google_sql_database.app.name}"
}
