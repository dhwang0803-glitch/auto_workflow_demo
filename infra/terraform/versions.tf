terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    # ADR-021 — Cloud Run Worker Pools (google_cloud_run_v2_worker_pool) is
    # only in google-beta until the resource promotes to GA. Keep the
    # version constraint aligned with `google` so both providers move
    # together. Narrow provider usage: only worker.tf resources pin
    # `provider = google-beta`; everything else stays on google.
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
