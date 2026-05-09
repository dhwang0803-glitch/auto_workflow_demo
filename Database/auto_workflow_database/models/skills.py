"""SQLAlchemy ORM for the Skill Bootstrap + Personalization tables.

Mirrors `schemas/005_skill_bootstrap.sql` (PLAN_12 / ADR-022) and
`schemas/006_personalization.sql` (PLAN_14 / ADR-023, absorbed into
PLAN_15 PR-γ). Reuses the `Base` declared in `core.py` so a single
MetaData object describes every table.

owner_user_id mirrors the SQL choice — see 005 header for the MVP-vs-
workspace_id rationale. Personalization-scoped rows additionally carry
`user_id` (FK users) which is non-null only when scope='user'.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from auto_workflow_database.models.core import Base


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','pending_review','rejected','archived')",
            name="skills_status_chk",
        ),
        CheckConstraint(
            "scope IN ('workspace','user','team')",
            name="skills_scope_chk",
        ),
        CheckConstraint(
            "source IN ('docs','wizard','hitl_edit')",
            name="skills_source_chk",
        ),
        CheckConstraint(
            "(scope = 'user' AND user_id IS NOT NULL) "
            "OR (scope <> 'user' AND user_id IS NULL)",
            name="skills_user_scope_chk",
        ),
        Index(
            "idx_skills_owner_active",
            "owner_user_id",
            text("created_at DESC"),
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_skills_user_scope",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("scope = 'user'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scope: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'workspace'")
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'active'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # Personalization (006_personalization.sql). user_id is non-null only
    # when scope='user' — DB constraint skills_user_scope_chk enforces.
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'docs'")
    )
    suggestion_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024), nullable=True
    )


class SkillSource(Base):
    __tablename__ = "skill_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('document','conversation','observation')",
            name="skill_sources_source_type_chk",
        ),
        Index("idx_skill_sources_skill", "skill_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    skill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SkillApplication(Base):
    __tablename__ = "skill_applications"
    __table_args__ = (
        Index(
            "idx_skill_applications_skill_recent",
            "skill_id",
            text("applied_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    skill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    # workflow_id is intentionally not a foreign key — the row is recorded at
    # compose time, before the user has saved a workflow.
    workflow_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    citation: Mapped[str] = mapped_column(Text, nullable=False)


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "content_hash",
            name="policy_documents_owner_hash_uq",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    raw_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class PolicyExtraction(Base):
    __tablename__ = "policy_extractions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="policy_extractions_doc_chunk_uq",
        ),
        # HNSW index lives in the SQL DDL — SQLAlchemy's Index() does not
        # cleanly express USING hnsw with operator class. Migration handles it.
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("policy_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    extracted_skill_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )


class WorkflowRevision(Base):
    """Append-only WorkflowSchema history per workflow.

    Mirrors `schemas/006_personalization.sql`. PLAN_14 §4.3: source records
    whether the version came from a fresh AI compose ('ai_draft') or from
    the user editing on top of one ('user_edit'). parent_revision_id links
    a user_edit back to the ai_draft it modified — NULL on the seed.
    """

    __tablename__ = "workflow_revisions"
    __table_args__ = (
        CheckConstraint(
            "source IN ('ai_draft','user_edit')",
            name="workflow_revisions_source_chk",
        ),
        UniqueConstraint(
            "workflow_id",
            "revision_no",
            name="workflow_revisions_workflow_no_uq",
        ),
        Index(
            "idx_workflow_revisions_workflow_seq",
            "workflow_id",
            text("revision_no DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    parent_revision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflow_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class PersonalSkillReview(Base):
    """User accept/edit/reject decisions per suggestion_hash.

    Mirrors `schemas/006_personalization.sql`. PLAN_14 §4.3: a separate
    table (rather than columns on skills) so rejected suggestions — which
    never become skills rows — are still recorded for dedup, and so the
    full per-user decision history is queryable independently of which
    suggestions made it into skills.
    """

    __tablename__ = "personal_skill_reviews"
    __table_args__ = (
        CheckConstraint(
            "action IN ('accept','edit','reject')",
            name="personal_skill_reviews_action_chk",
        ),
        Index(
            "idx_personal_skill_reviews_user_hash",
            "user_id",
            "suggestion_hash",
        ),
        Index(
            "idx_personal_skill_reviews_user_recent",
            "user_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    suggestion_hash: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
