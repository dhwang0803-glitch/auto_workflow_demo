-- Migration: add google_oauth credential type + oauth_metadata column (ADR-019)
-- Blueprint: docs/context/decisions.md ADR-019 §3 (storage schema)
-- Consumers: API_Server (OAuth authorize/callback), Execution_Engine (GoogleWorkspaceNode)
--
-- Storage model:
--   - encrypted_data  = Fernet({"refresh_token": "..."})
--   - oauth_metadata  = { provider, account_email, scopes, access_token,
--                         token_expires_at, client_id_hash, needs_reauth }
--   Access-token is short-lived and sits in oauth_metadata; refresh-token is
--   the long-lived secret and stays Fernet-encrypted at rest.

ALTER TABLE credentials DROP CONSTRAINT IF EXISTS credentials_type_check;

ALTER TABLE credentials
    ADD CONSTRAINT credentials_type_check
    CHECK (type IN (
        'smtp',
        'postgres_dsn',
        'slack_webhook',
        'http_bearer',
        'google_oauth',
        'unknown'
    ));

ALTER TABLE credentials
    ADD COLUMN IF NOT EXISTS oauth_metadata JSONB NULL;
