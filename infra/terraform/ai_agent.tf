# AI_Agent — Modal pivot residual GCP artifact (bearer secret only).
#
# AI_Agent runtime lives on Modal (`AI_Agent/scripts/modal_app.py`). The only
# GCP-side artifact that survives is the bearer token secret, which is the
# rotation source-of-truth shared with Modal Secret `agent-bearer-token`.
#
# Earlier post-pivot state kept the AR repo + GCS bucket + agent SA "for
# backup", but Modal builds the image from Dockerfile and pulls the model from
# HF directly (`unsloth/gemma-4-26B-A4B-it-GGUF`). Neither was in any restore
# path — they were YAGNI dead weight, removed 2026-04-24.
#
# API_Server reads this secret at boot via the api SA and attaches
# `Authorization: Bearer <token>` to its Modal endpoint calls.
#
# Rotation: `gcloud secrets versions add agent-bearer-token-<env>
# --data-file=<new>` then `modal secret update agent-bearer-token
# AGENT_BEARER_TOKEN=<same>`. Both API_Server (via Cloud Run env from this
# secret) and Modal AgentService need to pick up the new value.

resource "random_password" "agent_bearer_token" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "agent_bearer_token" {
  secret_id = "agent-bearer-token-${var.environment}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "agent_bearer_token" {
  secret      = google_secret_manager_secret.agent_bearer_token.id
  secret_data = random_password.agent_bearer_token.result
}

resource "google_secret_manager_secret_iam_member" "api_sa_bearer_token" {
  secret_id = google_secret_manager_secret.agent_bearer_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}
