# Google OAuth2 Deploy Runbook — ADR-019 Phase 6

> Procedure for wiring the ADR-019 implementation into a real GCP
> project. Terraform creates the three secrets, IAM bindings, and
> Cloud Run env injection, but **the actual values for OAuth Client
> ID / Secret / redirect URI can only be obtained after a manual
> registration in the GCP Console**. This document covers those
> manual steps and how to inject them into Secret Manager safely.
>
> Predecessor ADRs: ADR-018 (Secret Manager) · ADR-019 (OAuth design)
> · ADR-020 (Cloud Run deployment). For the general secret R/W rules
> see [the "Secret R/W patterns" section of
> `README.md`](README.md#secret-rw-patterns).

## 0. Prerequisites

- `terraform apply` has run at least once, so the three placeholder
  secrets already exist:
  - `google-oauth-client-id-<env>`
  - `google-oauth-client-secret-<env>`
  - `google-oauth-redirect-uri-<env>`
- The Cloud Run service `auto-workflow-api-<env>` is deployed and
  returning 200 from `/health` (we need its URL to pin the redirect
  URI).
- ADR-019 §7: **stay in testing mode**. Verification submission is
  deferred until there's demand.

## 1. GCP Console — OAuth consent screen (one-time, per project)

Console: **APIs & Services → OAuth consent screen**

1. **User Type = External**, **Publishing status = Testing**.
   Per ADR-019 §7 we skip verification. In testing mode only the
   accounts listed under Test users can complete consent.
2. App name should be recognizable (`auto-workflow-<env>`); support
   email is your gmail.
3. Add the demo/dev Google accounts to **Test users**. Cap is 100.
   Until prod, that's developers + people who watch the demo.
4. In **Scopes**, pick the following (ADR-019 §3, least privilege):
   - `https://www.googleapis.com/auth/gmail.send` — `gmail_send` node
   - `https://www.googleapis.com/auth/drive.file` —
     `google_drive_upload_file` node (only files the app creates /
     uploads)
   - `https://www.googleapis.com/auth/spreadsheets` —
     `google_sheets_append_row` node
   - `https://www.googleapis.com/auth/documents` —
     `google_docs_append_text` node
   - `https://www.googleapis.com/auth/presentations` —
     `google_slides_create_presentation` node
   - `https://www.googleapis.com/auth/calendar.events` —
     `google_calendar_create_event` node

   Broad scopes like `drive` or `gmail.readonly` are **deliberately
   excluded** — that pushes back the moment we have to leave testing
   mode and submit for verification.

## 2. GCP Console — issue an OAuth 2.0 Client ID

Console: **APIs & Services → Credentials → + CREATE CREDENTIALS →
OAuth client ID**

1. **Application type = Web application**.
2. Under **Authorized redirect URIs**, add exactly one callback
   based on the Cloud Run URL:
   ```
   https://auto-workflow-api-<env>-<hash>-an.a.run.app/api/v1/oauth/google/callback
   ```
   Look up the Cloud Run-issued `run.app` URL via
   `gcloud run services describe`:
   ```bash
   BASE_URL=$(gcloud run services describe auto-workflow-api-<env> \
     --region=asia-northeast3 --format='value(status.url)')
   echo "${BASE_URL}/api/v1/oauth/google/callback"
   ```
   **If the string isn't exact, Google rejects with
   `redirect_uri_mismatch`** — trailing slash, casing, and path must
   all match the Cloud Run URL down to the character.

   ADR-019 §7: when switching to a custom domain, **add a new URI**
   to this list (don't remove the old one) → swap Frontend traffic →
   then remove the old URI. Simultaneous registration is allowed, so
   downtime stays at zero.
3. Pressing Create surfaces a dialog that shows **Client ID** and
   **Client Secret** once. **The moment that dialog closes you can
   no longer read Client Secret — only rotate** it. Run step 3 below
   (Secret Manager injection) in the same terminal session to chain
   straight through.

## 3. Inject into Secret Manager — stdin pipe required

**Rule**: never let the OAuth Client Secret pass through the
clipboard or `echo`. Copying the value from the Console dialog into
the terminal already leaves traces in the clipboard buffer,
scrollback, and shell history. Use `gcloud`'s `--data-file=-` stdin
pipe so the value never becomes visible.

### 3-1. Client ID (semi-public, but use the same pipe by convention)

```bash
ENV=staging    # or prod
PROJECT=$(gcloud config get-value project)

# Copy "Client ID" from the dialog → pass it via the single-line arg.
# A heredoc is the safest approach (the value never lands in argv;
# only in shell history — be aware).
gcloud secrets versions add "google-oauth-client-id-${ENV}" \
  --project="$PROJECT" --data-file=- <<< "PASTE_CLIENT_ID_HERE"
```

### 3-2. Client Secret — never echo / print

In the terminal, read input with **`read -s`** into a variable and
pipe it straight in. Nothing shows on screen, the value never lands
in argv, and the variable is destroyed by the trailing `unset`.

```bash
# ❌ Bad — leaves plaintext in stdout / argv / history
echo "GOCSPX-abc123..." | gcloud secrets versions add "google-oauth-client-secret-${ENV}" --data-file=-

# ✅ Good — tty input only, consumed immediately
read -rs -p "Paste Google OAuth Client Secret: " CSEC; echo
printf '%s' "$CSEC" | gcloud secrets versions add "google-oauth-client-secret-${ENV}" \
  --project="$PROJECT" --data-file=-
unset CSEC
```

After this, close the Console dialog and **overwrite your
clipboard** (copy any other text).

### 3-3. Redirect URI

The value is a public URL, but use the same pipe for consistency:

```bash
BASE_URL=$(gcloud run services describe "auto-workflow-api-${ENV}" \
  --region=asia-northeast3 --project="$PROJECT" --format='value(status.url)')
REDIRECT="${BASE_URL}/api/v1/oauth/google/callback"

printf '%s' "$REDIRECT" | gcloud secrets versions add "google-oauth-redirect-uri-${ENV}" \
  --project="$PROJECT" --data-file=-
```

## 4. Redeploy Cloud Run — pick up the new secret version

The Cloud Run env Terraform wrote uses `secret_key_ref.version =
"latest"`, but **a running revision caches the value from the moment
it booted**. To pick up the placeholder → real-value flip, a new
revision must be created.

```bash
# Option 1: re-run the deploy pipeline (push to the release branch)
#   .github/workflows/deploy-prod.yml deploys a new revision

# Option 2: don't change the image, force a new revision via "update"
gcloud run services update "auto-workflow-api-${ENV}" \
  --project="$PROJECT" --region=asia-northeast3 \
  --update-env-vars=_OAUTH_REFRESH=$(date +%s)
```

`_OAUTH_REFRESH` is a dummy key the app ignores; it exists only so
Cloud Run notices an env-var change and rolls a new revision.

## 5. Verification

### 5-1. Confirm the secrets are no longer placeholders

Single-bit check that the placeholder prefix
(`PLACEHOLDER_GOOGLE_OAUTH_`) is gone. **Never dump the value to
stdout.**

```bash
for NAME in google-oauth-client-id google-oauth-client-secret google-oauth-redirect-uri; do
  VAL=$(gcloud secrets versions access latest \
    --secret="${NAME}-${ENV}" --project="$PROJECT")
  case "$VAL" in
    PLACEHOLDER_*) echo "$NAME = ⚠  PLACEHOLDER (not injected)";;
    *)             echo "$NAME = ✅ real value (length ${#VAL})";;
  esac
  unset VAL
done
```

### 5-2. Call the authorize endpoint

Verify that API_Server loaded the three secrets by hitting
`/api/v1/oauth/google/authorize`. A logged-in JWT is required, so
first obtain a token using an existing user account.

```bash
TOKEN="<existing JWT>"
curl -sS -X POST "${BASE_URL}/api/v1/oauth/google/authorize" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"credential_name":"gmail-test","scopes":["https://www.googleapis.com/auth/gmail.send"]}' | jq .
```

Expected response:
```json
{ "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=...&scope=...&state=..." }
```

- A `503` means `GoogleOAuthClient` is `None` → secret is still a
  placeholder or the revision wasn't refreshed. Loop back to §4.
- Paste the `authorize_url` into a browser, log in as a test user,
  approve consent → Google redirects to the Cloud Run service's
  `/api/v1/oauth/google/callback` → a row should appear in the
  `credentials` table.

### 5-3. Dry-test a node run

After consent, use the resulting `credential_id` to run `gmail_send`
and confirm the refresh gate works. Workflow JSON example:

```json
{
  "nodes": [{
    "id": "n1", "type": "gmail_send",
    "config": {
      "credential_id": "<credential_id>",
      "to": "self@example.com", "subject": "adr-019 phase6 smoke", "body": "ok"
    }
  }],
  "connections": []
}
```

Cloud Run logs should show
`POST https://oauth2.googleapis.com/token` (refresh) followed by
`POST gmail.googleapis.com/gmail/v1/users/me/messages/send`.

## 6. Troubleshooting

| Symptom | Cause | Action |
|---------|-------|--------|
| `invalid_grant` (on refresh) | In testing mode the refresh_token expires after 6 months of inactivity / the user revoked access from their Google account | The credential is marked `status = needs_reauth`; surface re-consent in UI / API. Re-call `/oauth/google/authorize` for that credential |
| `redirect_uri_mismatch` | Even a single-character mismatch between the Console's Authorized redirect URIs and `google-oauth-redirect-uri-<env>` in Secret Manager | Diff both. Usually trailing slash / `run.app` hash. Re-check §2-2 |
| `invalid_scope` | The scope isn't listed on the consent screen's Scopes step | Go back to §1-4, add the scope, save. Existing credentials don't pick up new scopes — **re-consent required** |
| `/authorize` returns 503 | `GOOGLE_OAUTH_CLIENT_ID` env is empty or a placeholder — `GoogleOAuthClient = None` in Settings | Inject in §3 + redeploy revision per §4 |
| `access_denied` on the consent screen | Logging in as a Google account that isn't on the test-users list | Add the account in §1-3 (max 100 users) |
| Cloud Run logs show `Permission 'secretmanager.versions.access' denied` | The Terraform IAM bindings (`google_secret_manager_secret_iam_member.api_google_oauth_*`) haven't propagated yet — a few-minute race right after the first apply | Re-roll a revision with `gcloud run services update` to retry. If it persists, check IAM state |

## 7. Rotate (replace the Client Secret)

When you suspect compromise, or for periodic rotation:

1. Console: **Credentials → that Client ID → RESET SECRET** —
   issues a new Client Secret in a one-time dialog.
2. Use §3-2 to add a new version to
   `google-oauth-client-secret-<env>`.
3. Redeploy a revision per §4.
4. In the Console, **DISABLE the old secret** (protects in-flight
   requests during the grace period Google honors).
5. Monitor, then go DISABLE → DELETE.

Rotating the Client ID itself is effectively issuing a new OAuth
client → credential rows must be re-issued. Plan around the lifetime
of any running workflows.

## 8. Teardown

After the demo, tear down in this order:

1. Drain the API_Server revision (Cloud Run traffic = 0%).
2. The three OAuth secrets in Secret Manager are removed by
   `terraform destroy` — no separate `gcloud secrets delete` needed.
3. GCP Console → Credentials → **DELETE** the OAuth Client ID (it's
   outside Terraform's management, so manual). The test-users list
   disappears with it.
4. `terraform destroy` — cleans up the rest of GCP. See [`README.md`
   "Destroy time budget"](README.md#destroy-time-budget) for timing
   warnings.

## Related docs

- `docs/context/decisions.md` ADR-019 §3 (scopes) · §7 (testing
  mode) · §9 (Client Secret management) · §10 (testing)
- `docs/context/decisions.md` ADR-018 — Secret Manager-based design
- `infra/docs/README.md` — Cloud SQL + general secret R/W rules
- `infra/terraform/main.tf` — the three OAuth secrets +
  placeholders
- `infra/terraform/cloud_run.tf` — IAM accessor + `secret_key_ref`
  env injection
