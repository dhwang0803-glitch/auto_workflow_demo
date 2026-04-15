"""SQLAlchemy ORM for PLAN_03 partitioned log table.

Only the parent table is mapped. Postgres routes inserts to the correct
partition based on `started_at`; SQLAlchemy never touches partition children
directly. Alembic autogen will NOT understand `PARTITION BY` — treat the DDL
in `schemas/003_node_logs_partitioned.sql` as the source of truth and keep
this mapping in sync manually.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from Database.src.models.core import Base


class ExecutionNodeLog(Base):
    __tablename__ = "execution_node_logs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','success','failed','skipped')",
            name="execution_node_logs_status_chk",
        ),
        Index(
            "idx_enl_execution",
            "execution_id",
            "node_id",
            text("attempt DESC"),
        ),
        Index(
            "idx_enl_model",
            "model",
            postgresql_where=text("model IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    # `started_at` is part of the PK because it's the partition key.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    execution_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    attempt: Mapped[int] = mapped_column(
        nullable=False, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stdout_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    stderr_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    tokens_prompt: Mapped[int | None] = mapped_column(nullable=True)
    tokens_completion: Mapped[int | None] = mapped_column(nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
