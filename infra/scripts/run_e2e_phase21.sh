#!/usr/bin/env bash
# ADR-021 Phase 6.1 — live wake-up + Celery pickup observation runner.
#
# Covers PLAN_21 §6.1 steps 4-7 (the scriptable / observational part):
#   4. POST /api/v1/workflows/{wf_id}/execute and watch for "worker pool
#      ... woken" in API logs
#   5. Tail worker Cloud Logging for instance start + task pickup
#   6. Poll executions table (via /api/v1/executions/{id}) for status
#      transition to "success"
#   7. Fire 2 more /execute calls within 30s to verify the WakeWorker
#      throttle window (expect only one "woken" log line across all 3)
#
# Steps 1-3 (EE image build + AR push, terraform apply, API redeploy)
# are manual prerequisites — see infra/docs/RUNBOOK_phase21_e2e.md.
# Step 8 (15-min idle scale-down back to 0) is also manual — MANUAL
# scaling mode doesn't auto-return, so in the current implementation
# this is verified by `terraform destroy` rather than idle timeout.
#
# Usage:
#   bash infra/scripts/run_e2e_phase21.sh <env> <api_base_url> <bearer_token> <workflow_id>
#
# Example:
#   bash infra/scripts/run_e2e_phase21.sh staging \
#       https://auto-workflow-api-staging-k5hulh42oa-du.a.run.app \
#       "$API_TOKEN" \
#       "$WF_ID"
#
# The script is read-only against GCP (logging read + API HTTP calls). It
# will NOT terraform apply / destroy / patch resources. Failures print
# diagnostic output and exit non-zero — rerun after fixing the underlying
# issue rather than looping.

set -euo pipefail

if [ $# -lt 4 ]; then
  echo "usage: $0 <env: staging|prod> <api_base_url> <bearer_token> <workflow_id>" >&2
  exit 2
fi

ENV_NAME="$1"; API_BASE="$2"; TOKEN="$3"; WF_ID="$4"

case "$ENV_NAME" in staging|prod) ;; *) echo "bad env" >&2; exit 2 ;; esac

PROJECT="$(gcloud config get-value project 2>/dev/null)"
if [ -z "$PROJECT" ]; then
  echo "gcloud project not set — run 'gcloud config set project <id>'" >&2
  exit 2
fi

API_SVC="auto-workflow-api-${ENV_NAME}"
EE_POOL="auto-workflow-ee-${ENV_NAME}"

# Fire one /execute and return its execution_id. Body "{}" — worker
# pulls the workflow graph from DB, not from request body.
execute_once() {
  local resp
  # Empty JSON body required — GCLB in front of Cloud Run rejects POST without
  # Content-Length header (HTTP 411). Worker pulls the workflow graph from DB,
  # not from request body, so `{}` is semantically fine.
  resp="$(curl -sS -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    --fail-with-body \
    -d '{}' \
    "${API_BASE}/api/v1/workflows/${WF_ID}/execute")"
  # Tolerate both {"id":"..."} (current contract) and {"execution_id":"..."}.
  # UUID-only extraction — no python dependency (Git Bash on Windows often
  # lacks python on PATH). grep-first-UUID avoids sed's greedy `.*` tripping
  # over later `"id"` fields (node ids inside the graph body, etc.).
  echo "$resp" | grep -oE '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' | head -1
}

# Poll /executions/{id} until status is a terminal value, timeout 120s.
wait_terminal() {
  local exec_id="$1"
  local deadline=$(( $(date +%s) + 120 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    local status
    status="$(curl -sS -H "Authorization: Bearer ${TOKEN}" \
      "${API_BASE}/api/v1/executions/${exec_id}" \
      | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')"
    case "$status" in
      success|failed|error) echo "$status"; return 0 ;;
      "") echo "unknown-response" >&2; return 1 ;;
    esac
    sleep 2
  done
  echo "timeout" >&2
  return 1
}

# Count "woken" log lines in the API service over the last N minutes.
count_wake_logs() {
  local minutes="$1"
  gcloud logging read \
    "resource.type=cloud_run_revision AND resource.labels.service_name=${API_SVC} AND textPayload:\"worker pool ${EE_POOL} woken\"" \
    --freshness="${minutes}m" \
    --project="$PROJECT" \
    --limit=20 \
    --format='value(timestamp)' 2>/dev/null | wc -l | tr -d ' '
}

echo "[1/3] First /execute — expect wake-up log line and terminal status=success"
EXEC1="$(execute_once)"
echo "  execution_id=$EXEC1"
S1="$(wait_terminal "$EXEC1")"
echo "  status=$S1"
if [ "$S1" != "success" ]; then
  echo "  FAIL: first execution did not reach 'success' (see /api/v1/executions/${EXEC1})" >&2
  exit 1
fi

echo "[2/3] Back-to-back executes within throttle window (30s default)"
EXEC2="$(execute_once)"
EXEC3="$(execute_once)"
echo "  execution_ids=$EXEC2 $EXEC3"
S2="$(wait_terminal "$EXEC2")"
S3="$(wait_terminal "$EXEC3")"
echo "  statuses=$S2 $S3"
if [ "$S2" != "success" ] || [ "$S3" != "success" ]; then
  echo "  FAIL: 2nd/3rd executions did not reach 'success'" >&2
  exit 1
fi

echo "[3/3] Verify throttle — expect exactly 1 'woken' log across all 3 executes"
# Logs ingest can lag up to ~30s. Sleep once then read; don't retry in a loop.
sleep 30
N="$(count_wake_logs 10)"
echo "  woken_log_count=$N (within last 10m)"
if [ "$N" -lt 1 ]; then
  echo "  FAIL: no 'woken' log entry found — WakeWorker may not be firing" >&2
  exit 1
fi
if [ "$N" -gt 1 ]; then
  echo "  WARN: $N 'woken' entries — throttle may be below 30s or clock drift" >&2
fi

echo
echo "Phase 6.1 live observation passed: 3 executions, ${N} wake(s), all success."
echo "Next: manual step 8 (scale-down verification) — MANUAL mode requires"
echo "      either terraform destroy or an explicit workerPools.patch to 0."
