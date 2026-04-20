#!/usr/bin/env bash
# Generic E2E runner for any Google Workspace node against the staging
# Cloud SQL credential. Wraps Execution_Engine/scripts/e2e_workspace_node.py
# with proxy + Secret Manager bootstrap. Same shape as run_e2e_gmail_send.sh
# but parametrized by node_type + JSON config so all 6 ADR-019 nodes share
# one entry point.
#
# Usage:
#   bash Database/deploy/scripts/run_e2e_workspace_node.sh \
#       <env: staging|prod> <credential_id> <node_type> <config_json>
#
# Examples (single-quote the JSON to keep bash from eating the braces):
#   ... staging <cred> gmail_send \
#       '{"to":"x@y.com","subject":"hi","body":"hello"}'
#   ... staging <cred> google_drive_upload_file \
#       '{"name":"smoke.txt","content":"hello from workflow"}'
#   ... staging <cred> google_sheets_append_row \
#       '{"spreadsheet_id":"<id>","range":"Sheet1!A:B","values":["x","y"]}'
#   ... staging <cred> google_docs_append_text \
#       '{"document_id":"<id>","text":"appended\n"}'
#   ... staging <cred> google_slides_create_presentation \
#       '{"title":"E2E smoke deck"}'
#   ... staging <cred> google_calendar_create_event \
#       '{"summary":"E2E","start_date":"2026-04-21","end_date":"2026-04-22"}'

set -euo pipefail

if [ $# -lt 4 ]; then
  echo "usage: $0 <env: staging|prod> <credential_id> <node_type> <config_json>" >&2
  exit 2
fi

ENV_NAME="$1"; CRED_ID="$2"; NODE_TYPE="$3"; NODE_CONFIG="$4"

case "$ENV_NAME" in staging|prod) ;; *) echo "bad env" >&2; exit 2 ;; esac

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PYBIN="/c/Users/user/anaconda3/python.exe"
export CLOUDSDK_PYTHON="$PYBIN"
PROXY="${REPO_ROOT}/.tmp/cloud-sql-proxy.exe"
[ -x "$PROXY" ] || PROXY="${REPO_ROOT}/.tmp/cloud-sql-proxy"

PROJECT="$(gcloud config get-value project)"
INSTANCE="$(cd "${REPO_ROOT}/Database/deploy/terraform" && terraform output -raw instance_connection_name)"
PORT=15434
PROXY_LOG="$(mktemp)"

"$PROXY" --port="$PORT" "$INSTANCE" > "$PROXY_LOG" 2>&1 &
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; rm -f "$PROXY_LOG"; }
trap cleanup EXIT INT TERM

for _ in $(seq 1 20); do
  grep -q "ready for new connections" "$PROXY_LOG" 2>/dev/null && break
  sleep 1
done
grep -q "ready for new connections" "$PROXY_LOG" || { cat "$PROXY_LOG" >&2; exit 1; }

# Pull secrets without echoing — see feedback_secret_read_pipe memory.
DB_PASS="$(gcloud secrets versions access latest --secret="db-password-${ENV_NAME}" --project="$PROJECT")"
CRED_MASTER="$(gcloud secrets versions access latest --secret="credential-master-key-${ENV_NAME}" --project="$PROJECT")"
OAUTH_CID="$(gcloud secrets versions access latest --secret="google-oauth-client-id-${ENV_NAME}" --project="$PROJECT")"
OAUTH_SECRET="$(gcloud secrets versions access latest --secret="google-oauth-client-secret-${ENV_NAME}" --project="$PROJECT")"

export DATABASE_URL="postgresql+asyncpg://auto_workflow:${DB_PASS}@127.0.0.1:${PORT}/auto_workflow"
export CREDENTIAL_MASTER_KEY="$CRED_MASTER"
export GOOGLE_OAUTH_CLIENT_ID="$OAUTH_CID"
export GOOGLE_OAUTH_CLIENT_SECRET="$OAUTH_SECRET"
export CRED_ID="$CRED_ID"
export NODE_TYPE="$NODE_TYPE"
export NODE_CONFIG="$NODE_CONFIG"

unset DB_PASS CRED_MASTER OAUTH_CID OAUTH_SECRET

cd "${REPO_ROOT}/Execution_Engine"
export PYTHONPATH="$(pwd)"
"$PYBIN" scripts/e2e_workspace_node.py
