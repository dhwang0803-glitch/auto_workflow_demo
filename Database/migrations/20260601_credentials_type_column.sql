-- Migration: add type column to credentials (PLAN_09 — credential pipeline)
-- Blueprint: docs/context/PLAN_credential_pipeline.md §1.4
-- Consumer: API_Server PLAN_07 (credential CRUD + execute_workflow resolution)

ALTER TABLE credentials
    ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT 'unknown'
    CHECK (type IN ('smtp', 'postgres_dsn', 'slack_webhook', 'http_bearer', 'unknown'));
