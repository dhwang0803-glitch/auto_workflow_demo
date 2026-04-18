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
