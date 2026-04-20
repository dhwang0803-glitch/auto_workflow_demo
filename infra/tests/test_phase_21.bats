#!/usr/bin/env bats
# ADR-021 Phase 3 — Worker Pools + Memorystore Terraform structure tests.
#
# These are intentionally lightweight: they grep the HCL source for the shape
# contracts that ADR-021 locks in, rather than running `terraform plan` (which
# would hit GCP). The terraform validate pass in CI is the true syntax gate;
# this bats suite guards against silent ADR drift — e.g., someone flipping
# min_instance_count to 1 "to fix cold starts" without updating the ADR.
#
# Run locally:
#   bats infra/tests/test_phase_21.bats
#
# The suite is also runnable as plain bash (bats' `run` / `@test` macros are
# near-bash), so it works without a bats binary as a sanity grep.

TF_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../terraform" && pwd)"

@test "memorystore.tf: broker is BASIC + 1GB by default" {
  grep -q 'tier               = var.broker_tier' "$TF_DIR/memorystore.tf"
  grep -q 'memory_size_gb     = var.broker_memory_size_gb' "$TF_DIR/memorystore.tf"
  grep -q 'default     = "BASIC"' "$TF_DIR/variables.tf"
  grep -q 'default     = 1' "$TF_DIR/variables.tf"
}

@test "memorystore.tf: uses PRIVATE_SERVICE_ACCESS on shared peering range" {
  grep -q 'connect_mode       = "PRIVATE_SERVICE_ACCESS"' "$TF_DIR/memorystore.tf"
  grep -q 'reserved_ip_range  = google_compute_global_address.private_services_range.name' "$TF_DIR/memorystore.tf"
}

@test "memorystore.tf: prevent_destroy lifecycle guard present" {
  grep -q 'prevent_destroy = true' "$TF_DIR/memorystore.tf"
}

@test "worker.tf: Worker Pool scales from 0 (ADR-021 §4 wake-up contract)" {
  grep -q 'min_instance_count = 0' "$TF_DIR/worker.tf"
  ! grep -q 'min_instance_count = 1' "$TF_DIR/worker.tf"
}

@test "worker.tf: provider pinned to google-beta (Worker Pools is beta-only)" {
  grep -q 'provider = google-beta' "$TF_DIR/worker.tf"
}

@test "worker.tf: separate ee_runtime SA distinct from api SA (ADR-021 §6)" {
  grep -q 'resource "google_service_account" "ee_runtime"' "$TF_DIR/worker.tf"
  grep -q 'account_id   = "auto-workflow-ee-' "$TF_DIR/worker.tf"
}

@test "worker.tf: ee_runtime has NO jwt_secret access (API-only secret)" {
  ! grep -q 'ee_runtime.email.*jwt_secret' "$TF_DIR/worker.tf"
  ! grep -q 'jwt_secret.*ee_runtime' "$TF_DIR/worker.tf"
}

@test "worker.tf: API SA has scoped wake permission on this pool only" {
  grep -q 'google_cloud_run_v2_worker_pool_iam_member' "$TF_DIR/worker.tf"
  grep -q 'member   = "serviceAccount:\${google_service_account.api.email}"' "$TF_DIR/worker.tf"
}

@test "worker.tf: ignore_changes on image + min_instance_count (drift protection)" {
  grep -q 'template\[0\].containers\[0\].image' "$TF_DIR/worker.tf"
  grep -q 'scaling\[0\].min_instance_count' "$TF_DIR/worker.tf"
}

@test "cloud_run.tf: API container gets WORKER_POOL_NAME + CELERY_BROKER_URL" {
  grep -q 'name  = "WORKER_POOL_NAME"' "$TF_DIR/cloud_run.tf"
  grep -q 'name  = "CELERY_BROKER_URL"' "$TF_DIR/cloud_run.tf"
}

@test "versions.tf: google-beta provider declared alongside google" {
  grep -q 'google-beta = {' "$TF_DIR/versions.tf"
  grep -q 'source  = "hashicorp/google-beta"' "$TF_DIR/versions.tf"
}

@test "outputs.tf: ee_worker_pool_name + broker_host exported" {
  grep -q 'output "ee_worker_pool_name"' "$TF_DIR/outputs.tf"
  grep -q 'output "broker_host"' "$TF_DIR/outputs.tf"
}
