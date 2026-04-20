"""ADR-021 §5 — SETNX idempotency tests for _run_task.

Verified via fakeredis (monkeypatched into _make_redis_client) so the
SET NX + TTL semantics run against the real redis-py client code path
without needing a live broker. _execute and WorkerContainer are stubbed
out — this file tests only the dedup wrapper, not workflow execution.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis

from src.dispatcher import serverless


@pytest.fixture
def fake_redis():
    # One shared FakeAsyncRedis so two calls to _make_redis_client see the
    # same keyspace (matching production, where both point at Memorystore).
    return FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def patched_dispatcher(monkeypatch, fake_redis):
    """Stub out redis factory, WorkerContainer, and _execute.

    Returns a dict with an `execute_calls` counter so tests can assert on
    how many times the body ran.
    """
    state = {"execute_calls": 0, "execute_ids": []}

    def _fake_redis_client():
        return fake_redis

    class _FakeContainer:
        exec_repo = None
        wf_repo = None
        node_registry = None
        credential_store = None

        async def dispose(self):
            return None

    async def _fake_execute(execution_id, **kwargs):
        state["execute_calls"] += 1
        state["execute_ids"].append(execution_id)

    monkeypatch.setattr(serverless, "_make_redis_client", _fake_redis_client)
    monkeypatch.setattr(serverless, "WorkerContainer", _FakeContainer)
    monkeypatch.setattr(serverless, "_execute", _fake_execute)
    return state


async def test_first_call_acquires_lock_and_executes(patched_dispatcher, fake_redis):
    eid = str(uuid4())
    await serverless._run_task(eid)

    assert patched_dispatcher["execute_calls"] == 1
    # Sentinel is "completed" (not DEL) so late redeliveries still skip.
    assert await fake_redis.get(f"execution:{eid}") == "completed"


async def test_concurrent_duplicate_calls_execute_only_once(patched_dispatcher):
    eid = str(uuid4())

    await asyncio.gather(
        serverless._run_task(eid),
        serverless._run_task(eid),
        serverless._run_task(eid),
    )

    assert patched_dispatcher["execute_calls"] == 1


async def test_post_completion_redeliver_is_skipped(patched_dispatcher, fake_redis):
    eid = str(uuid4())

    await serverless._run_task(eid)
    assert patched_dispatcher["execute_calls"] == 1
    assert await fake_redis.get(f"execution:{eid}") == "completed"

    # Celery redelivery of an already-processed task: sentinel is still
    # present (24h TTL), so SETNX fails and _execute is not re-invoked.
    await serverless._run_task(eid)
    assert patched_dispatcher["execute_calls"] == 1
