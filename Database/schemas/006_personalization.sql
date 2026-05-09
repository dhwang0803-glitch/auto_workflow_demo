-- PLAN_14 / ADR-023 — HITL Personalization (absorbed into PLAN_15 PR-γ).
-- Adds personalization columns to `skills` plus the two new tables PLAN_14
-- needs (workflow_revisions, personal_skill_reviews). PLAN_13 §11.7's
-- `search_personal_skills` agent tool consumes the (user_id, embedding)
-- pair on user-scoped skills; PLAN_14 will fill those rows from observed
-- user edits later.
--
-- Why bundle PLAN_14 PR-A here: PLAN_13 §11 already commits the agent to a
-- retrieval tool against this surface, so the migration is sunk-cost
-- relative to that decision. Doing it now collapses what would be a stand-
-- alone PLAN_14 PR-A and lets PLAN_14 start at PR-B.

-- ---------------------------------------------------------------------------
-- skills — personalization columns. The existing skills_scope_chk (005)
-- already permits scope='user'; we add the data the user-scoped row needs.
-- ---------------------------------------------------------------------------
ALTER TABLE skills ADD COLUMN IF NOT EXISTS user_id          uuid;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS source           text NOT NULL DEFAULT 'docs';
ALTER TABLE skills ADD COLUMN IF NOT EXISTS suggestion_hash  text;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS embedding        vector(1024);

-- FK is added separately so ADD COLUMN IF NOT EXISTS stays simple. DO block
-- guards re-apply (Postgres has no ADD CONSTRAINT IF NOT EXISTS).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'skills_user_id_fkey'
    ) THEN
        ALTER TABLE skills
            ADD CONSTRAINT skills_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'skills_source_chk'
    ) THEN
        ALTER TABLE skills
            ADD CONSTRAINT skills_source_chk
            CHECK (source IN ('docs', 'wizard', 'hitl_edit'));
    END IF;

    -- scope='user' must carry user_id; non-user scopes must not.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'skills_user_scope_chk'
    ) THEN
        ALTER TABLE skills
            ADD CONSTRAINT skills_user_scope_chk
            CHECK (
                (scope = 'user' AND user_id IS NOT NULL)
                OR (scope <> 'user' AND user_id IS NULL)
            );
    END IF;
END $$;

-- HNSW for cosine retrieval — search_personal_skills (PLAN_13 §11.3) and
-- the PLAN_14 retrieval inject (§4.6) both top-k against this index.
CREATE INDEX IF NOT EXISTS idx_skills_embedding_hnsw
    ON skills USING hnsw (embedding vector_cosine_ops);

-- Partial index for the user-retrieval hot path. search_personal_skills
-- always filters scope='user' AND user_id=$1 before the embedding scan,
-- so a partial b-tree on (user_id) lets the planner narrow before HNSW.
CREATE INDEX IF NOT EXISTS idx_skills_user_scope
    ON skills(user_id, created_at DESC)
    WHERE scope = 'user';

-- ---------------------------------------------------------------------------
-- workflow_revisions — append-only history of WorkflowSchema versions per
-- workflow. PLAN_14 §4.3: source='ai_draft' for the compose output that
-- the user accepted as a starting point, source='user_edit' for every save
-- the user made on top. parent_revision_id links the user_edit back to the
-- ai_draft it modified (NULL on the seed revision). `payload` is the full
-- WorkflowSchema JSON — diff is computed by AI_Agent at retrieval time
-- rather than stored, since the diff function (PLAN_14 §4.4) is semantic
-- not text and the schema may evolve.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workflow_revisions (
    id                    uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id           uuid          NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    revision_no           int           NOT NULL,
    source                text          NOT NULL,
    payload               jsonb         NOT NULL,
    parent_revision_id    uuid          NULL REFERENCES workflow_revisions(id) ON DELETE SET NULL,
    created_at            timestamptz   NOT NULL DEFAULT now(),
    created_by            uuid          NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT workflow_revisions_source_chk
        CHECK (source IN ('ai_draft', 'user_edit')),
    CONSTRAINT workflow_revisions_workflow_no_uq
        UNIQUE (workflow_id, revision_no)
);

CREATE INDEX IF NOT EXISTS idx_workflow_revisions_workflow_seq
    ON workflow_revisions(workflow_id, revision_no DESC);

-- ---------------------------------------------------------------------------
-- personal_skill_reviews — append-only review trail. Tracks every accept /
-- edit / reject decision per (user, suggestion_hash). PLAN_14 §4.3 uses the
-- hash to dedup recurring candidates and surface "you already rejected
-- this" so the proposer doesn't replay the same suggestion.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS personal_skill_reviews (
    id                  uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    suggestion_hash     text          NOT NULL,
    action              text          NOT NULL,
    rejection_reason    text          NULL,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT personal_skill_reviews_action_chk
        CHECK (action IN ('accept', 'edit', 'reject'))
);

CREATE INDEX IF NOT EXISTS idx_personal_skill_reviews_user_hash
    ON personal_skill_reviews(user_id, suggestion_hash);
CREATE INDEX IF NOT EXISTS idx_personal_skill_reviews_user_recent
    ON personal_skill_reviews(user_id, created_at DESC);
