-- PLAN_02 — Credentials / Agents / Webhook registry
-- Related ADRs: ADR-004 (Fernet credential encryption), ADR-009 (Agent GPU routing)
-- Depends on: schemas/001_core.sql (users, workflows)

-- ---------------------------------------------------------------------------
-- credentials — ADR-004 Fernet at rest
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS credentials (
    id              uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        uuid         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            text         NOT NULL,
    type            text         NOT NULL DEFAULT 'unknown'
        CHECK (type IN (
            'smtp', 'postgres_dsn', 'slack_webhook', 'http_bearer',
            'google_oauth', 'unknown'
        )),
    encrypted_data  bytea        NOT NULL,
    -- ADR-019: present only for type='google_oauth'. Holds access_token +
    -- expiry + scopes + account_email + client_id_hash + needs_reauth flag.
    oauth_metadata  jsonb        NULL,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT credentials_owner_name_uq UNIQUE (owner_id, name)
);

-- ---------------------------------------------------------------------------
-- agents — ADR-009 hardware routing
-- gpu_info shape (MVP):
--   { "vendor": "nvidia"|"amd"|"cpu_only",
--     "vram_gb": number,
--     "backend": "vllm"|"ktransformers"|null }
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
    id              uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        uuid         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    public_key      text         NOT NULL,
    gpu_info        jsonb        NOT NULL DEFAULT '{}'::jsonb,
    last_heartbeat  timestamptz  NULL,
    registered_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_id);

-- ---------------------------------------------------------------------------
-- webhook_registry — dynamic webhook path → workflow_id resolution
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS webhook_registry (
    id           uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id  uuid         NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    path         text         NOT NULL UNIQUE,
    secret       text         NULL,
    created_at   timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhook_path ON webhook_registry(path);
