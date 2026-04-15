"""ExecutionNodeLog integration tests — PLAN_03."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from sqlalchemy import text

from Database.src.models.core import Execution as ExecutionORM
from Database.src.models.core import User as UserORM
from Database.src.models.core import Workflow as WorkflowORM
from Database.src.repositories._session import build_engine, build_sessionmaker
from Database.src.repositories.base import ExecutionNodeLog
from Database.src.repositories.execution_node_log_repository import (
    PostgresExecutionNodeLogRepository,
)

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — node-log integration tests require live DB",
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
            status="running",
            execution_mode="serverless",
        )
        s.add(ex)
        await s.flush()
        return ex


async def test_two_phase_write_and_retry_ordering(sm):
    ex = await _seed_execution(sm)
    repo = PostgresExecutionNodeLogRepository(sm)

    now = datetime.now(timezone.utc)
    attempts = []
    for i in (1, 2, 3):
        log = ExecutionNodeLog(
            id=uuid4(),
            execution_id=ex.id,
            node_id="http_1",
            attempt=i,
            status="running",
            started_at=now + timedelta(seconds=i),
        )
        await repo.record_start(log)
        attempts.append(log)

    # Finish attempt 1 and 2 as failed, 3 as success.
    for a in attempts[:2]:
        await repo.record_finish(
            a.id,
            a.started_at,
            status="failed",
            finished_at=a.started_at + timedelta(milliseconds=500),
            duration_ms=500,
            error={"type": "Timeout", "message": "upstream slow"},
        )
    last = attempts[2]
    await repo.record_finish(
        last.id,
        last.started_at,
        status="success",
        finished_at=last.started_at + timedelta(milliseconds=200),
        duration_ms=200,
        output={"ok": True},
    )

    rows = await repo.list_for_execution(ex.id)
    assert [r.attempt for r in rows] == [3, 2, 1]  # attempt DESC
    assert rows[0].status == "success"
    assert rows[1].status == "failed" and rows[1].error["type"] == "Timeout"


async def test_llm_usage_summarization(sm):
    ex = await _seed_execution(sm)
    repo = PostgresExecutionNodeLogRepository(sm)
    now = datetime.now(timezone.utc)

    plan = [
        ("node_a", "gpt-4o", 100, 50, 0.01),
        ("node_a", "gpt-4o", 200, 80, 0.02),
        ("node_b", "claude-opus-4-6", 300, 120, 0.05),
    ]
    for idx, (node, model, pt, ct, cost) in enumerate(plan):
        log = ExecutionNodeLog(
            id=uuid4(),
            execution_id=ex.id,
            node_id=node,
            attempt=1,
            status="running",
            started_at=now + timedelta(seconds=idx),
        )
        await repo.record_start(log)
        await repo.record_finish(
            log.id,
            log.started_at,
            status="success",
            finished_at=log.started_at + timedelta(milliseconds=100),
            duration_ms=100,
            model=model,
            tokens_prompt=pt,
            tokens_completion=ct,
            cost_usd=cost,
        )

    summary = await repo.summarize_llm_usage(ex.id)
    assert summary["gpt-4o"] == {
        "tokens_prompt": 300,
        "tokens_completion": 130,
        "cost_usd": pytest.approx(0.03),
        "calls": 2,
    }
    assert summary["claude-opus-4-6"]["calls"] == 1


async def test_rows_land_in_expected_month_partitions(sm):
    """Insert two rows in different months and verify Postgres routed them."""
    ex = await _seed_execution(sm)
    repo = PostgresExecutionNodeLogRepository(sm)

    this_month = datetime.now(timezone.utc).replace(
        day=1, hour=12, minute=0, second=0, microsecond=0
    )
    # Pick two months that are definitely inside the 12-month initial window.
    month_a = this_month
    # Add 31 days then snap to day=1 to get next month reliably.
    raw_next = month_a + timedelta(days=40)
    month_b = raw_next.replace(day=1, hour=12)

    log_a = ExecutionNodeLog(
        id=uuid4(),
        execution_id=ex.id,
        node_id="n",
        attempt=1,
        status="running",
        started_at=month_a,
    )
    log_b = ExecutionNodeLog(
        id=uuid4(),
        execution_id=ex.id,
        node_id="n",
        attempt=2,
        status="running",
        started_at=month_b,
    )
    await repo.record_start(log_a)
    await repo.record_start(log_b)

    async with sm() as s:
        result = await s.execute(
            text(
                "SELECT id, tableoid::regclass::text AS partition "
                "FROM execution_node_logs WHERE execution_id = :eid"
            ),
            {"eid": ex.id},
        )
        rows = {r.id: r.partition for r in result.all()}

    assert rows[log_a.id].startswith("execution_node_logs_")
    assert rows[log_b.id].startswith("execution_node_logs_")
    assert rows[log_a.id] != rows[log_b.id], (
        f"expected different partitions, got {rows}"
    )
