-- PLAN_12 / ADR-022 — Skill Bootstrap.
-- Scope: skills + skill_sources + skill_applications +
--        policy_documents + policy_extractions
--
-- All five tables are scoped by `owner_user_id` (FK to users.id) for the MVP.
-- ADR-022 §8.5 calls for a workspace-id-scoped model where one user can be
-- a member of multiple workspaces. We collapse 1 user = 1 workspace until
-- demand justifies the membership table — a future migration will add
-- `workspaces`, backfill one-per-user, and rename owner_user_id → workspace_id.
-- Keeping the column name explicit (`owner_user_id`, not `workspace_id`)
-- prevents pretending the multi-tenant story is here when it isn't.
--
-- pgvector is installed eagerly by 001_core.sql line 11 — version 0.8.1 on
-- Cloud SQL Postgres 16 verified 2026-04-27 (PR #128 risk validation).
-- HNSW chosen over ivfflat: better recall at top-K=5 retrieval scale and
-- pgvector 0.8.1 supports both.

-- ---------------------------------------------------------------------------
-- skills — codified team policy as condition+action pair, the unit ADR-022
--          §8.1 settles on
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skills (
    id              uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id   uuid          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            text          NOT NULL,
    description     text          NULL,
    condition       jsonb         NOT NULL,
    action          jsonb         NOT NULL,
    scope           text          NOT NULL DEFAULT 'workspace',
    status          text          NOT NULL DEFAULT 'active',
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT skills_status_chk CHECK (
        status IN ('active', 'pending_review', 'rejected', 'archived')
    ),
    CONSTRAINT skills_scope_chk CHECK (
        scope IN ('workspace', 'user', 'team')
    )
);

CREATE INDEX IF NOT EXISTS idx_skills_owner_active
    ON skills(owner_user_id, created_at DESC)
    WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- skill_sources — provenance record. Append-only; multiple rows per skill
--                 when the same policy is corroborated across documents
--                 + interview turns + observed patterns.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_sources (
    id              uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id        uuid          NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    source_type     text          NOT NULL,
    source_ref      jsonb         NOT NULL,
    extracted_at    timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT skill_sources_source_type_chk CHECK (
        source_type IN ('document', 'conversation', 'observation')
    )
);

CREATE INDEX IF NOT EXISTS idx_skill_sources_skill
    ON skill_sources(skill_id);

-- ---------------------------------------------------------------------------
-- skill_applications — append-only audit trail of when a skill influenced a
--                      compose result. workflow_id is intentionally NOT a
--                      foreign key: the row is recorded at compose time
--                      before the user has saved a workflow, so the
--                      reference is best-effort metadata, not a hard link.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_applications (
    id              uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id        uuid          NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    workflow_id     uuid          NULL,
    applied_at      timestamptz   NOT NULL DEFAULT now(),
    citation        text          NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_applications_skill_recent
    ON skill_applications(skill_id, applied_at DESC);

-- ---------------------------------------------------------------------------
-- policy_documents — uploaded source artefact (PDF/MD/text). raw_content is
--                    inlined as BYTEA for the MVP team-handbook scale (~few
--                    hundred KB). Migration to GCS URI is a column add later.
--                    The (owner_user_id, content_hash) unique constraint
--                    blocks duplicate uploads of the same file by the same
--                    user — re-upload of a different revision (different
--                    hash) replays extraction so the diff/merge UI ADR-022
--                    §8.4 needs has fresh material to compare.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_documents (
    id              uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id   uuid          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename        text          NOT NULL,
    content_hash    text          NOT NULL,
    mime_type       text          NOT NULL,
    raw_content     bytea         NULL,
    uploaded_at     timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT policy_documents_owner_hash_uq
        UNIQUE (owner_user_id, content_hash)
);

-- ---------------------------------------------------------------------------
-- policy_extractions — chunked document slices with BGE-M3 embeddings.
--                      embedding is nullable so the upload-then-embed pipeline
--                      can insert text first, embed asynchronously, and
--                      patch the column when the embedding completes.
--                      extracted_skill_id links the chunk that yielded a
--                      skill back to that skill (ADR-022 §4 step 3 output).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_extractions (
    id                   uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id          uuid          NOT NULL REFERENCES policy_documents(id) ON DELETE CASCADE,
    chunk_index          int           NOT NULL,
    chunk_text           text          NOT NULL,
    embedding            vector(1024)  NULL,
    extracted_skill_id   uuid          NULL REFERENCES skills(id) ON DELETE SET NULL,
    CONSTRAINT policy_extractions_doc_chunk_uq
        UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_policy_extractions_embedding_hnsw
    ON policy_extractions USING hnsw (embedding vector_cosine_ops);
