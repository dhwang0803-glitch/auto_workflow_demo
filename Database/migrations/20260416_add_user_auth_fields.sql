-- Migration: add local password auth + email verification fields to users
-- Consumer: API_Server PLAN_01 (auth + user management)
-- Related: docs/context/decisions.md (no new ADR; extends ADR-001 user model)

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_hash bytea NULL,
    ADD COLUMN IF NOT EXISTS is_verified   boolean NOT NULL DEFAULT false;

-- Existing seed/test rows were created before password auth. Mark them
-- verified so legacy fixtures and integration tests don't hit the
-- "email not verified" login gate.
UPDATE users SET is_verified = true WHERE password_hash IS NULL;
