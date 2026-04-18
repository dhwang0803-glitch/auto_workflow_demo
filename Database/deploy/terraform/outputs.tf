output "instance_connection_name" {
  description = "Value for cloud-sql-proxy / Cloud Run --set-cloudsql-instances (project:region:instance)."
  value       = google_sql_database_instance.main.connection_name
}

output "instance_public_ip" {
  description = "IPv4 — populated only if ip_configuration.ipv4_enabled is true. Use with authorized_networks or Auth Proxy."
  value       = google_sql_database_instance.main.public_ip_address
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

output "credential_master_key_secret_id" {
  description = "Secret Manager resource ID for the ADR-004 Fernet key. Populate with a real key before serving traffic."
  value       = google_secret_manager_secret.credential_master_key.secret_id
}

output "jwt_secret_secret_id" {
  description = "Secret Manager resource ID for the JWT signing key."
  value       = google_secret_manager_secret.jwt_secret.secret_id
}

output "database_url_sync_hint" {
  description = "Template for DATABASE_URL_SYNC (psycopg / migrate.py). Actual password comes from Secret Manager."
  value       = "postgresql://${google_sql_user.app.name}:<PASSWORD>@${google_sql_database_instance.main.public_ip_address}:5432/${google_sql_database.app.name}"
  sensitive   = false
}
