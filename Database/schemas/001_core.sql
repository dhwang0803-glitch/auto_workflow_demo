-- PLAN_01 — Core Schema (b′)
-- Scope: users / workflows / nodes / executions
-- Related ADRs: ADR-001 (execution mode), ADR-007 (observability + approval),
--               ADR-008 (plan-based LLM routing)
-- Out of scope: credentials, agents, webhook_registry, users.gpu_info → PLAN_02

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";
-- Installed eagerly so future RAG features (template / workflow-history search)
-- don't need a schema migration to turn on. No vector columns yet.
CREATE EXTENSION IF NOT EXISTS "vector";

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id                       uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    email                    citext       UNIQUE NOT NULL,
    plan_tier                text         NOT NULL,
    default_execution_mode   text         NOT NULL DEFAULT 'serverless',
    external_api_policy      jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at               timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT users_plan_tier_chk
        CHECK (plan_tier IN ('light', 'middle', 'heavy')),
    CONSTRAINT users_default_execution_mode_chk
        CHECK (default_execution_mode IN ('serverless', 'agent'))
);

-- ---------------------------------------------------------------------------
-- workflows
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workflows (
    id          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    uuid         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        text         NOT NULL,
    settings    jsonb        NOT NULL,
    graph       jsonb        NOT NULL,
    is_active   boolean      NOT NULL DEFAULT true,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    updated_at  timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workflows_owner
    ON workflows(owner_id)
    WHERE is_active = true;

-- ---------------------------------------------------------------------------
-- nodes — runtime node catalog (type palette for the frontend)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nodes (
    type           text         NOT NULL,
    version        text         NOT NULL,
    schema         jsonb        NOT NULL,
    registered_at  timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (type, version)
);

-- ---------------------------------------------------------------------------
-- executions — ADR-007 observability + approval state machine
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS executions (
    id              uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     uuid           NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    status          text           NOT NULL,
    execution_mode  text           NOT NULL,
    started_at      timestamptz    NULL,
    finished_at     timestamptz    NULL,
    node_results    jsonb          NOT NULL DEFAULT '{}'::jsonb,
    error           jsonb          NULL,
    token_usage     jsonb          NOT NULL DEFAULT '{}'::jsonb,
    cost_usd        numeric(10, 6) NOT NULL DEFAULT 0,
    duration_ms     integer        NULL,
    paused_at_node  text           NULL,
    CONSTRAINT executions_status_chk CHECK (
        status IN (
            'queued', 'running', 'paused', 'resumed',
            'success', 'failed', 'rejected', 'cancelled'
        )
    ),
    CONSTRAINT executions_execution_mode_chk
        CHECK (execution_mode IN ('serverless', 'agent'))
);

CREATE INDEX IF NOT EXISTS idx_executions_workflow_id
    ON executions(workflow_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_executions_paused
    ON executions(paused_at_node)
    WHERE status = 'paused';
