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
  description = "Subnet CIDR used by Cloud Run Direct VPC Egress. /28 is the min Cloud Run accepts. Pick a block that does NOT overlap the services peering range (default 10.60.0.0/28)."
  type        = string
  default     = "10.60.0.0/28"
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
