-- PLAN_04 — approval_notifications (append-only audit trail)
-- Depends on: schemas/001_core.sql (executions)

CREATE TABLE IF NOT EXISTS approval_notifications (
    id            uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id  uuid         NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    node_id       text         NOT NULL,
    recipient     text         NOT NULL,
    channel       text         NOT NULL,
    status        text         NOT NULL,
    attempt       integer      NOT NULL DEFAULT 1,
    error         jsonb        NULL,
    sent_at       timestamptz  NULL,
    created_at    timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT approval_notifications_channel_chk
        CHECK (channel IN ('email', 'slack')),
    CONSTRAINT approval_notifications_status_chk
        CHECK (status IN ('queued', 'sent', 'failed', 'bounced'))
);

CREATE INDEX IF NOT EXISTS idx_approval_notif_execution
    ON approval_notifications (execution_id, node_id, created_at DESC);

-- Partial index: ops dashboard query path for stuck / failed notifications.
CREATE INDEX IF NOT EXISTS idx_approval_notif_undelivered
    ON approval_notifications (created_at)
    WHERE status IN ('queued', 'failed');
