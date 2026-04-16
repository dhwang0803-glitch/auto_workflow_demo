-- PLAN_06 — add created_at to executions for keyset pagination.
-- started_at is nullable (queued rows have no start time), so we need an
-- immutable NOT NULL timestamp for stable cursor ordering.

ALTER TABLE executions
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_executions_workflow_created
    ON executions(workflow_id, created_at DESC, id DESC);
