output "instance_connection_name" {
  description = "Value for cloud-sql-proxy / Cloud Run --set-cloudsql-instances (project:region:instance)."
  value       = google_sql_database_instance.main.connection_name
}

output "instance_public_ip" {
  description = "IPv4 — populated only if ip_configuration.ipv4_enabled is true (i.e., var.public_ip_enabled). Use with authorized_networks or Auth Proxy."
  value       = google_sql_database_instance.main.public_ip_address
}

output "instance_private_ip" {
  description = "Private IP inside the peered VPC. Cloud Run + Auth Proxy reach the instance via this address; dev laptops cannot unless they tunnel through the VPC."
  value       = google_sql_database_instance.main.private_ip_address
}

output "database_name" {
  value = google_sql_database.app.name
}

output "db_user" {
  value = google_sql_user.app.name
}

output "db_password_secret_id" {
  description = "Secret Manager resource ID for the auto-generated app-user password. Fetch via: gcloud secrets versions access latest --secret=<this>"
  value       = google_secret_manager_secret.db_password.secret_id
}

output "database_url_secret_id" {
  description = "Secret Manager resource ID holding the composed DSN that Cloud Run injects as DATABASE_URL."
  value       = google_secret_manager_secret.database_url.secret_id
}

output "credential_master_key_secret_id" {
  description = "Secret Manager resource ID for the ADR-004 Fernet key. Populate with a real key before serving traffic."
  value       = google_secret_manager_secret.credential_master_key.secret_id
}

output "jwt_secret_secret_id" {
  description = "Secret Manager resource ID for the JWT signing key."
  value       = google_secret_manager_secret.jwt_secret.secret_id
}

output "database_url_sync_hint" {
  description = "Template for DATABASE_URL_SYNC (psycopg / migrate.py) running from a laptop that has Auth Proxy on localhost:5434. The instance itself has no public IP by default (ADR-020 §2)."
  value       = "postgresql://${google_sql_user.app.name}:<PASSWORD>@127.0.0.1:5434/${google_sql_database.app.name}"
  sensitive   = false
}

# ---- Network ---------------------------------------------------------------

output "vpc_name" {
  value = google_compute_network.vpc.name
}

output "cloudrun_subnet" {
  value = google_compute_subnetwork.cloudrun.name
}

# ---- Cloud Run / Artifact Registry -----------------------------------------

output "api_service_url" {
  description = "HTTPS URL of the Cloud Run service (null until first deploy)."
  value       = google_cloud_run_v2_service.api.uri
}

output "api_service_account_email" {
  description = "Service account the Cloud Run instance runs as (ADR-020 §4)."
  value       = google_service_account.api.email
}

output "artifact_registry_repo" {
  description = "Fully-qualified AR repo path. Image URIs land under <repo>/api:<tag> and <repo>/ee:<tag>."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

# ---- Worker Pool + Memorystore (ADR-021) -----------------------------------

output "ee_worker_pool_name" {
  description = "Cloud Run Worker Pool name. API_Server env WORKER_POOL_NAME consumes this for services.patch wake-up calls."
  value       = google_cloud_run_v2_worker_pool.ee.name
}

output "ee_service_account_email" {
  description = "Service account the Worker Pool runs as (ADR-021 §6). Distinct from the API SA to bound blast radius."
  value       = google_service_account.ee_runtime.email
}

output "broker_host" {
  description = "Memorystore private IP. Reachable only inside the peered VPC. Used by API + Worker to compose CELERY_BROKER_URL."
  value       = google_redis_instance.broker.host
}

output "broker_port" {
  description = "Memorystore port (6379 by default). Exposed as an output so scripts don't hard-code."
  value       = google_redis_instance.broker.port
}
