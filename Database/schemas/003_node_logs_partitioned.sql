-- PLAN_03 — execution_node_logs (partitioned by month on started_at)
-- Depends on: schemas/001_core.sql (executions)

CREATE TABLE IF NOT EXISTS execution_node_logs (
    id                 uuid           NOT NULL DEFAULT gen_random_uuid(),
    execution_id       uuid           NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    node_id            text           NOT NULL,
    attempt            integer        NOT NULL DEFAULT 1,
    status             text           NOT NULL,
    started_at         timestamptz    NOT NULL,
    finished_at        timestamptz    NULL,
    duration_ms        integer        NULL,
    input              jsonb          NULL,
    output             jsonb          NULL,
    error              jsonb          NULL,
    -- stdout / stderr originals live in GCS. DB stores pointers only.
    stdout_uri         text           NULL,
    stderr_uri         text           NULL,
    -- LLM observability fields promoted out of JSONB for aggregation queries.
    model              text           NULL,
    tokens_prompt      integer        NULL,
    tokens_completion  integer        NULL,
    cost_usd           numeric(10, 6) NULL,
    CONSTRAINT execution_node_logs_status_chk
        CHECK (status IN ('running', 'success', 'failed', 'skipped')),
    -- Partition key must be part of every UNIQUE / PK constraint.
    PRIMARY KEY (id, started_at)
) PARTITION BY RANGE (started_at);

CREATE INDEX IF NOT EXISTS idx_enl_execution
    ON execution_node_logs (execution_id, node_id, attempt DESC);

CREATE INDEX IF NOT EXISTS idx_enl_model
    ON execution_node_logs (model)
    WHERE model IS NOT NULL;

-- Initial partitions: current month + 11 future months (12 total).
-- Idempotent via IF NOT EXISTS — safe to re-run from schemas reload.
-- Subsequent months are managed by scripts/roll_partitions.py.
DO $$
DECLARE
    start_month date := date_trunc('month', CURRENT_DATE)::date;
    i int;
    partition_start date;
    partition_end date;
    partition_name text;
BEGIN
    FOR i IN 0..11 LOOP
        partition_start := start_month + (i || ' months')::interval;
        partition_end := partition_start + interval '1 month';
        partition_name := 'execution_node_logs_' || to_char(partition_start, 'YYYY_MM');
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF execution_node_logs '
            'FOR VALUES FROM (%L) TO (%L)',
            partition_name, partition_start, partition_end
        );
    END LOOP;
END $$;
