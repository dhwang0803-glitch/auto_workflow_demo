#!/usr/bin/env bash
# Inject Google OAuth client_id / client_secret / redirect_uri from a
# downloaded GCP Console credential JSON into Secret Manager.
#
# WHY a dedicated script: Windows Git Bash often lacks `python` in PATH even
# when conda is active in PowerShell. gcloud itself also needs python on
# Windows. Inline pastes also corrupt multi-line quoting. A file avoids both.
#
# Usage:
#   bash infra/scripts/inject_oauth_secrets.sh <env> <json_path>
#
# Example:
#   bash infra/scripts/inject_oauth_secrets.sh staging \
#     "/c/Users/user/Documents/GitHub/client_secret_XXX.json"

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <env: staging|prod> <json_path>" >&2
  exit 2
fi

ENV_NAME="$1"
JSON_PATH="$2"

case "$ENV_NAME" in
  staging|prod) ;;
  *) echo "error: env must be 'staging' or 'prod'" >&2; exit 2 ;;
esac

if [ ! -f "$JSON_PATH" ]; then
  echo "error: JSON file not found: $JSON_PATH" >&2
  exit 2
fi

# Find Python — prefer conda, then system python3, then bundled gcloud python.
PYBIN=""
for candidate in \
  "/c/Users/user/anaconda3/python.exe" \
  "$(command -v python3 2>/dev/null || true)" \
  "$(command -v python 2>/dev/null || true)" \
  "/c/Program Files/Google/Cloud SDK/google-cloud-sdk/platform/bundledpython/python.exe" \
  "/c/Users/user/AppData/Local/Google/Cloud SDK/google-cloud-sdk/platform/bundledpython/python.exe"
do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then PYBIN="$candidate"; break; fi
done
if [ -z "$PYBIN" ]; then
  echo "error: no python interpreter found" >&2
  exit 2
fi

# gcloud on Windows needs python too — point it at the same one
export CLOUDSDK_PYTHON="$PYBIN"

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [ -z "$PROJECT" ]; then
  echo "error: no gcloud project set" >&2
  exit 2
fi

# Extract client_id / client_secret without echoing them
CID="$("$PYBIN" -c "import json,sys; d=json.load(open(sys.argv[1]))['web']; print(d['client_id'])" "$JSON_PATH")"
CSECRET="$("$PYBIN" -c "import json,sys; d=json.load(open(sys.argv[1]))['web']; print(d['client_secret'])" "$JSON_PATH")"

if [ -z "$CID" ] || [ -z "$CSECRET" ]; then
  echo "error: failed to extract client_id/client_secret from JSON" >&2
  exit 1
fi

# Cloud Run URL for the api service in this env — read from terraform output
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TF_DIR="${REPO_ROOT}/infra/terraform"
RUN_URL="$(cd "$TF_DIR" && terraform output -raw api_service_url 2>/dev/null || true)"
if [ -z "$RUN_URL" ]; then
  echo "error: terraform output api_service_url is empty" >&2
  exit 1
fi
REDIRECT_URI="${RUN_URL}/api/v1/oauth/google/callback"

echo "project:       $PROJECT"
echo "env:           $ENV_NAME"
echo "client_id:     ${CID:0:16}... (len=${#CID})"
echo "client_secret: [hidden, len=${#CSECRET}]"
echo "redirect_uri:  $REDIRECT_URI"
echo

# Pipe via stdin — never touches argv, never echoed.
printf '%s' "$CID"          | gcloud secrets versions add "google-oauth-client-id-${ENV_NAME}"     --data-file=- --project="$PROJECT"
printf '%s' "$CSECRET"      | gcloud secrets versions add "google-oauth-client-secret-${ENV_NAME}" --data-file=- --project="$PROJECT"
printf '%s' "$REDIRECT_URI" | gcloud secrets versions add "google-oauth-redirect-uri-${ENV_NAME}"  --data-file=- --project="$PROJECT"

unset CID CSECRET

# Securely delete the downloaded JSON (shred overwrites before unlink)
if command -v shred >/dev/null 2>&1; then
  shred -u "$JSON_PATH"
  echo "shredded: $JSON_PATH"
else
  rm -f "$JSON_PATH"
  echo "deleted (shred unavailable): $JSON_PATH"
fi

echo
echo "done. Verify versions:"
echo "  gcloud secrets versions list google-oauth-client-id-${ENV_NAME}     --project=$PROJECT"
echo "  gcloud secrets versions list google-oauth-client-secret-${ENV_NAME} --project=$PROJECT"
echo "  gcloud secrets versions list google-oauth-redirect-uri-${ENV_NAME}  --project=$PROJECT"
