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
      ipv4_enabled = true
      # authorized_networks = [] in default var keeps the instance reachable
      # only via Cloud SQL Auth Proxy until explicit CIDRs are added.
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

  depends_on = [google_project_service.apis]
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
  secret      = google_secret_manager_secret.credential_master_key.id
  secret_data = "REPLACE_ME_WITH_Fernet_generate_key"

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
  secret      = google_secret_manager_secret.jwt_secret.id
  secret_data = "REPLACE_ME_WITH_openssl_rand_base64_48"

  lifecycle {
    ignore_changes = [secret_data]
  }
}
