"""SQLAlchemy ORM for PLAN_04 approval_notifications table."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from auto_workflow_database.models.core import Base


class ApprovalNotification(Base):
    __tablename__ = "approval_notifications"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('email','slack')",
            name="approval_notifications_channel_chk",
        ),
        CheckConstraint(
            "status IN ('queued','sent','failed','bounced')",
            name="approval_notifications_status_chk",
        ),
        Index(
            "idx_approval_notif_execution",
            "execution_id",
            "node_id",
            text("created_at DESC"),
        ),
        Index(
            "idx_approval_notif_undelivered",
            "created_at",
            postgresql_where=text("status IN ('queued','failed')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    execution_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    recipient: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempt: Mapped[int] = mapped_column(
        nullable=False, server_default=text("1")
    )
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
