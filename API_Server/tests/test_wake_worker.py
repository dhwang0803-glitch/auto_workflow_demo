"""ADR-021 §5-b — WakeWorker throttle + patch contract.

Unit tests only — the Admin API client is mocked. Live wake-up is
verified in the Phase 6 E2E bash script.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.services.wake_worker import WakeWorker


def _make_settings(**overrides) -> Settings:
    base = dict(
        database_url="postgresql+asyncpg://ignored",
        jwt_secret="test",
        credential_master_key="",
        gcp_project_id="test-project",
        gcp_region="asia-northeast3",
        worker_pool_name="auto-workflow-ee-staging",
        worker_wake_throttle_seconds=30.0,
    )
    base.update(overrides)
    return Settings(**base)


async def test_wake_no_op_when_pool_not_configured():
    settings = _make_settings(worker_pool_name="")
    w = WakeWorker(settings=settings)
    with patch("app.services.wake_worker.run_v2.WorkerPoolsAsyncClient") as MockClient:
        await w.wake()
        MockClient.assert_not_called()


async def test_wake_patches_on_first_call():
    settings = _make_settings()
    w = WakeWorker(settings=settings)
    fake_client = AsyncMock()
    with patch("app.services.wake_worker.run_v2.WorkerPoolsAsyncClient", return_value=fake_client):
        await w.wake()
    fake_client.update_worker_pool.assert_awaited_once()
    call = fake_client.update_worker_pool.await_args
    req = call.kwargs["request"]
    assert req.worker_pool.name == (
        "projects/test-project/locations/asia-northeast3"
        "/workerPools/auto-workflow-ee-staging"
    )
    assert req.worker_pool.scaling.manual_instance_count == 1


async def test_wake_throttles_within_window():
    settings = _make_settings()
    w = WakeWorker(settings=settings)
    fake_client = AsyncMock()
    with patch("app.services.wake_worker.run_v2.WorkerPoolsAsyncClient", return_value=fake_client), \
         patch("app.services.wake_worker.time.monotonic", side_effect=[100.0, 105.0]):
        await w.wake()  # t=100, patches
        await w.wake()  # t=105, throttled (elapsed 5s < 30s)
    assert fake_client.update_worker_pool.await_count == 1


async def test_wake_patches_again_after_throttle_window_elapses():
    settings = _make_settings()
    w = WakeWorker(settings=settings)
    fake_client = AsyncMock()
    # t=100 first patch; t=100 last_wake_at snapshot; t=135 elapsed=35 > 30 → patch again.
    with patch("app.services.wake_worker.run_v2.WorkerPoolsAsyncClient", return_value=fake_client), \
         patch("app.services.wake_worker.time.monotonic", side_effect=[100.0, 135.0]):
        await w.wake()
        await w.wake()
    assert fake_client.update_worker_pool.await_count == 2


async def test_wake_swallows_api_errors():
    settings = _make_settings()
    w = WakeWorker(settings=settings)
    fake_client = AsyncMock()
    fake_client.update_worker_pool.side_effect = RuntimeError("admin api 503")
    with patch("app.services.wake_worker.run_v2.WorkerPoolsAsyncClient", return_value=fake_client):
        # Must not raise — execute_workflow depends on this guarantee.
        await w.wake()
    # Failure also shouldn't advance last_wake_at, so the next call retries.
    with patch("app.services.wake_worker.run_v2.WorkerPoolsAsyncClient", return_value=fake_client):
        await w.wake()
    assert fake_client.update_worker_pool.await_count == 2
