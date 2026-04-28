"""SQLAlchemy ORM for PLAN_12 / ADR-022 Skill Bootstrap tables.

Mirrors `schemas/005_skill_bootstrap.sql`. Reuses the `Base` declared in
`core.py` so a single MetaData object describes every table.

owner_user_id mirrors the SQL choice — see the schema file's header for the
MVP-vs-workspace_id rationale.
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
        Index(
            "idx_skills_owner_active",
            "owner_user_id",
            text("created_at DESC"),
            postgresql_where=text("status = 'active'"),
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
