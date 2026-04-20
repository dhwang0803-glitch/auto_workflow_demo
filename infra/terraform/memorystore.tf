# ADR-021 — Memorystore Redis 1GB Basic.
#
# Broker for Celery (Execution_Engine worker.py pulls tasks) plus three SETNX
# uses: execution_id idempotency (ADR-021 §5), OAuth refresh distributed lock
# (ADR-019 §1 deferred → resolved), OAuth state nonce multi-instance binding
# (ADR-019 §5 deferred → resolved).
#
# Connect mode is PRIVATE_SERVICE_ACCESS, reusing the peering range that
# ADR-018/020 already allocated for Cloud SQL (google_compute_global_address
# .private_services_range). No extra subnet is needed — both Cloud SQL and
# Memorystore sit on the same Service Networking peering.
#
# Prod entry reminder: bump tier to STANDARD_HA before serving real traffic
# (ADR-021 §9). BASIC has no failover and loses the broker on zonal outage —
# acceptable for staging / demo, not for prod.

resource "google_project_service" "redis_api" {
  service            = "redis.googleapis.com"
  disable_on_destroy = false
}

resource "google_redis_instance" "broker" {
  name               = "auto-workflow-broker-${var.environment}"
  tier               = var.broker_tier
  memory_size_gb     = var.broker_memory_size_gb
  region             = var.region
  authorized_network = google_compute_network.vpc.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"
  reserved_ip_range  = google_compute_global_address.private_services_range.name

  # Pinned so a provider upgrade doesn't silently flip versions under us.
  # Bump deliberately via PR when Memorystore makes a newer line the default.
  redis_version = var.broker_redis_version

  # BASIC has no built-in deletion_protection attribute — this lifecycle guard
  # is the only thing that stops a `terraform destroy` from wiping the broker.
  # Staging teardown scripts explicitly disable this via an out-of-band
  # comment-out (see infra/docs/README.md "Worker Pools 배포 runbook").
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    google_project_service.apis,
    google_project_service.redis_api,
    google_service_networking_connection.private_vpc_connection,
  ]
}
