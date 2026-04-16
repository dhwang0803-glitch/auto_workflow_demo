"""SQLAlchemy 2.0 async ORM — PLAN_01 §5.

Mirrors `Database/schemas/001_core.sql`. The SQL file is the source of truth
for DDL; these models exist so the Postgres Repository implementations
(PLAN_02) and integration tests can query through SQLAlchemy.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, CITEXT, JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "plan_tier IN ('light','middle','heavy')",
            name="users_plan_tier_chk",
        ),
        CheckConstraint(
            "default_execution_mode IN ('serverless','agent')",
            name="users_default_execution_mode_chk",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    plan_tier: Mapped[str] = mapped_column(String, nullable=False)
    default_execution_mode: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'serverless'")
    )
    external_api_policy: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    password_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        Index(
            "idx_workflows_owner",
            "owner_id",
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    graph: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Node(Base):
    __tablename__ = "nodes"

    type: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','paused','resumed',"
            "'success','failed','rejected','cancelled')",
            name="executions_status_chk",
        ),
        CheckConstraint(
            "execution_mode IN ('serverless','agent')",
            name="executions_execution_mode_chk",
        ),
        Index(
            "idx_executions_workflow_id",
            "workflow_id",
            text("started_at DESC"),
        ),
        Index(
            "idx_executions_workflow_created",
            "workflow_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "idx_executions_paused",
            "paused_at_node",
            postgresql_where=text("status = 'paused'"),
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
    status: Mapped[str] = mapped_column(String, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    node_results: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    token_usage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 6), nullable=False, server_default=text("0")
    )
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    paused_at_node: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
