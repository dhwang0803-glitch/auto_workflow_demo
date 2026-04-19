#!/usr/bin/env bash
# E2E Gmail send against the staging Cloud SQL credential. Wraps
# Execution_Engine/scripts/e2e_gmail_send.py with proxy + Secret Manager.
#
# Usage:
#   bash Database/deploy/scripts/run_e2e_gmail_send.sh <env> <cred_id> <to> <subject> <body>

set -euo pipefail

if [ $# -lt 5 ]; then
  echo "usage: $0 <env: staging|prod> <credential_id> <to> <subject> <body>" >&2
  exit 2
fi

ENV_NAME="$1"; CRED_ID="$2"; TO_ADDR="$3"; SUBJECT="$4"; BODY="$5"

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

# Pull secrets without echoing
DB_PASS="$(gcloud secrets versions access latest --secret="db-password-${ENV_NAME}" --project="$PROJECT")"
CRED_MASTER="$(gcloud secrets versions access latest --secret="credential-master-key-${ENV_NAME}" --project="$PROJECT")"
OAUTH_CID="$(gcloud secrets versions access latest --secret="google-oauth-client-id-${ENV_NAME}" --project="$PROJECT")"
OAUTH_SECRET="$(gcloud secrets versions access latest --secret="google-oauth-client-secret-${ENV_NAME}" --project="$PROJECT")"

export DATABASE_URL="postgresql+asyncpg://auto_workflow:${DB_PASS}@127.0.0.1:${PORT}/auto_workflow"
export CREDENTIAL_MASTER_KEY="$CRED_MASTER"
export GOOGLE_OAUTH_CLIENT_ID="$OAUTH_CID"
export GOOGLE_OAUTH_CLIENT_SECRET="$OAUTH_SECRET"
export CRED_ID="$CRED_ID"
export TO_ADDR="$TO_ADDR"
export SUBJECT="$SUBJECT"
export BODY="$BODY"

unset DB_PASS CRED_MASTER OAUTH_CID OAUTH_SECRET

cd "${REPO_ROOT}/Execution_Engine"
export PYTHONPATH="$(pwd)"
"$PYBIN" scripts/e2e_gmail_send.py
