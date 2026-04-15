"""ApprovalNotificationRepository integration tests — PLAN_04."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from sqlalchemy.exc import IntegrityError

from auto_workflow_database.models.core import Execution as ExecutionORM
from auto_workflow_database.models.core import User as UserORM
from auto_workflow_database.models.core import Workflow as WorkflowORM
from auto_workflow_database.models.notifications import (
    ApprovalNotification as NotificationORM,
)
from auto_workflow_database.repositories._session import build_engine, build_sessionmaker
from auto_workflow_database.repositories.approval_notification_repository import (
    PostgresApprovalNotificationRepository,
)
from auto_workflow_database.repositories.base import ApprovalNotification

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — approval notification tests require live DB",
)


@pytest.fixture
async def sm():
    engine = build_engine(DATABASE_URL)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _seed_execution(sm) -> ExecutionORM:
    async with sm() as s, s.begin():
        u = UserORM(email=f"{uuid4()}@test.local", plan_tier="light")
        s.add(u)
        await s.flush()
        wf = WorkflowORM(
            owner_id=u.id,
            name="wf",
            settings={},
            graph={"nodes": [], "edges": []},
        )
        s.add(wf)
        await s.flush()
        ex = ExecutionORM(
            workflow_id=wf.id,
            status="paused",
            execution_mode="serverless",
            paused_at_node="approval_1",
        )
        s.add(ex)
        await s.flush()
        return ex


def _make(execution_id, *, status="queued", channel="email", attempt=1, recipient="alice@x.test"):
    return ApprovalNotification(
        id=uuid4(),
        execution_id=execution_id,
        node_id="approval_1",
        recipient=recipient,
        channel=channel,
        status=status,
        attempt=attempt,
    )


async def test_append_and_list_for_execution(sm):
    ex = await _seed_execution(sm)
    repo = PostgresApprovalNotificationRepository(sm)

    # 1st attempt: queued
    n1 = _make(ex.id, status="queued", attempt=1)
    await repo.record(n1)
    # 2nd attempt: failed with error payload
    n2 = _make(ex.id, status="failed", attempt=2)
    n2.error = {"provider": "sendgrid", "code": 550}
    await repo.record(n2)
    # 3rd attempt: sent
    n3 = _make(ex.id, status="sent", attempt=3)
    n3.sent_at = datetime.now(timezone.utc)
    await repo.record(n3)

    rows = await repo.list_for_execution(ex.id)
    assert len(rows) == 3
    # Ordered (node_id, created_at DESC) — latest first within node
    assert [r.status for r in rows] == ["sent", "failed", "queued"]
    assert rows[1].error == {"provider": "sendgrid", "code": 550}


async def test_list_undelivered_filters_by_age_and_status(sm):
    ex = await _seed_execution(sm)
    repo = PostgresApprovalNotificationRepository(sm)

    # Fresh queued (should NOT show up with older_than=1h)
    fresh = _make(ex.id, status="queued", recipient="fresh@x.test")
    await repo.record(fresh)

    # Force an "old" row by inserting via ORM with explicit created_at in the past.
    old_queued_id = uuid4()
    old_failed_id = uuid4()
    sent_long_ago_id = uuid4()
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    async with sm() as s, s.begin():
        s.add_all(
            [
                NotificationORM(
                    id=old_queued_id,
                    execution_id=ex.id,
                    node_id="approval_1",
                    recipient="old-queued@x.test",
                    channel="email",
                    status="queued",
                    attempt=1,
                    created_at=two_hours_ago,
                ),
                NotificationORM(
                    id=old_failed_id,
                    execution_id=ex.id,
                    node_id="approval_1",
                    recipient="old-failed@x.test",
                    channel="slack",
                    status="failed",
                    attempt=1,
                    created_at=two_hours_ago,
                ),
                NotificationORM(
                    id=sent_long_ago_id,
                    execution_id=ex.id,
                    node_id="approval_1",
                    recipient="sent@x.test",
                    channel="email",
                    status="sent",
                    attempt=1,
                    sent_at=two_hours_ago,
                    created_at=two_hours_ago,
                ),
            ]
        )

    undelivered = await repo.list_undelivered(older_than=timedelta(hours=1))
    ids = {n.id for n in undelivered}
    assert old_queued_id in ids
    assert old_failed_id in ids
    assert sent_long_ago_id not in ids  # sent filtered out
    assert fresh.id not in ids  # too recent filtered out


async def test_check_constraints_reject_bad_values(sm):
    ex = await _seed_execution(sm)
    async with sm() as s, s.begin():
        s.add(
            NotificationORM(
                id=uuid4(),
                execution_id=ex.id,
                node_id="n",
                recipient="x@x.test",
                channel="sms",  # invalid
                status="queued",
                attempt=1,
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()
