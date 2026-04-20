#!/usr/bin/env bash
# Run Database/scripts/migrate.py against a Cloud SQL instance through the
# Auth Proxy, fetching the DB password from Secret Manager into a shell
# variable without printing it.
#
# WHY: the obvious two-liner
#     PW=$(gcloud secrets versions access latest --secret=db-password-prod)
#     DATABASE_URL_SYNC="postgresql://u:${PW}@..." python migrate.py
# is one shell-echo away from leaking PW into terminal scrollback / shell
# history / agent conversation logs. This wrapper keeps the secret inside
# the script's environment and never writes it to a file, argv, or stdout.
#
# Usage:
#   infra/scripts/migrate_via_proxy.sh <environment> [proxy-port] [migrate.py args...]
#
# Examples:
#   infra/scripts/migrate_via_proxy.sh prod              # apply
#   infra/scripts/migrate_via_proxy.sh prod 15432 --status
#
# Defaults:
#   proxy-port = 15432 (5432/5433 are often held on Windows dev machines)

set -euo pipefail

if [ "${1-}" = "-h" ] || [ "${1-}" = "--help" ] || [ $# -lt 1 ]; then
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

ENV_NAME="$1"; shift
case "$ENV_NAME" in
  staging|prod) ;;
  *) echo "error: environment must be 'staging' or 'prod' (got: ${ENV_NAME})" >&2; exit 2 ;;
esac

PORT="${1-15432}"
if [[ "${PORT}" =~ ^[0-9]+$ ]]; then
  shift || true
else
  PORT=15432
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TF_DIR="${REPO_ROOT}/infra/terraform"

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [ -z "$PROJECT" ]; then
  echo "error: no gcloud project set — run 'gcloud config set project <id>'" >&2
  exit 2
fi

INSTANCE="$(cd "$TF_DIR" && terraform output -raw instance_connection_name 2>/dev/null || true)"
if [ -z "$INSTANCE" ]; then
  echo "error: terraform output instance_connection_name is empty — did you 'terraform apply' yet?" >&2
  exit 2
fi

# Locate cloud-sql-proxy: repo-local .tmp/ first (Windows dev convention), then PATH
PROXY=""
for candidate in \
  "${REPO_ROOT}/.tmp/cloud-sql-proxy" \
  "${REPO_ROOT}/.tmp/cloud-sql-proxy.exe" \
  "$(command -v cloud-sql-proxy 2>/dev/null || true)"
do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then PROXY="$candidate"; break; fi
done
if [ -z "$PROXY" ]; then
  echo "error: cloud-sql-proxy binary not found. Install: https://cloud.google.com/sql/docs/postgres/sql-proxy#install" >&2
  exit 2
fi

PROXY_LOG="$(mktemp)"
# No --private-ip: from a dev machine outside the VPC, the proxy needs the
# public-IP path. For prod (private-only instance per ADR-020 §2) run this
# from inside the VPC — Cloud Run Job, Cloud Shell w/ Private Google Access,
# or a GCE bastion — and pass --private-ip there.
"$PROXY" --port="$PORT" "$INSTANCE" > "$PROXY_LOG" 2>&1 &
PROXY_PID=$!
cleanup() {
  kill "$PROXY_PID" 2>/dev/null || true
  rm -f "$PROXY_LOG"
}
trap cleanup EXIT INT TERM

# Wait for proxy ready (max ~20s)
for _ in $(seq 1 20); do
  if grep -q "ready for new connections" "$PROXY_LOG" 2>/dev/null; then break; fi
  sleep 1
done
if ! grep -q "ready for new connections" "$PROXY_LOG" 2>/dev/null; then
  echo "error: cloud-sql-proxy failed to start within 20s. Log:" >&2
  cat "$PROXY_LOG" >&2
  exit 1
fi

DB_USER="$(cd "$TF_DIR" && terraform output -raw db_user)"
DB_NAME="$(cd "$TF_DIR" && terraform output -raw database_name)"

# Fetch password into env var — never printed, never written to disk.
# Disable xtrace just in case the caller enabled it.
{ set +x; } 2>/dev/null
PW="$(gcloud secrets versions access latest --secret="db-password-${ENV_NAME}" --project="$PROJECT")"
export DATABASE_URL_SYNC="postgresql://${DB_USER}:${PW}@127.0.0.1:${PORT}/${DB_NAME}"
unset PW

cd "${REPO_ROOT}/Database"
python scripts/migrate.py "$@"
