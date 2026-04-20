"""Cloud Run Worker Pool wake-up — ADR-021 §5-b.

Patches the target worker pool's `scaling.manual_instance_count` to 1
via Cloud Run Admin API so an idle pool (count=0) spins up an instance
to consume the Celery task that execute_workflow just enqueued.

Design notes:
- 30s throttle guards the Admin API quota (60 writes/min/project) against
  burst-execute patterns. Within the window we assume the pool is still
  warm (first task drives startup ≤ ~15s).
- Patch failure must NOT fail execute_workflow. The task is already
  queued — a missed wake just delays pickup until the next execute lands
  (Celery's at-least-once delivery picks it up eventually).
- WorkerPools use **manual scaling** (not auto-scale) — the only field
  on `WorkerPoolScaling` is `manual_instance_count`, which is an exact
  count, not a min floor. Setting it to 1 wakes the pool; returning to
  0 is a separate concern (scale-down cron or idle watchdog; see
  ADR-021 §3).
- Terraform's worker.tf ignores drift on this field (see
  `lifecycle.ignore_changes`), so the Admin API patch doesn't fight
  `terraform apply`.
"""
from __future__ import annotations

import logging
import time

from google.cloud import run_v2

from app.config import Settings

logger = logging.getLogger(__name__)


class WakeWorker:
    """Throttled Cloud Run Worker Pool wake-up helper.

    One instance per app process. Holds `_last_wake_at` monotonic
    timestamp; `wake()` is a no-op inside the throttle window.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._last_wake_at: float = 0.0
        self._client: run_v2.WorkerPoolsAsyncClient | None = None

    def _configured(self) -> bool:
        s = self._settings
        return bool(s.gcp_project_id and s.gcp_region and s.worker_pool_name)

    async def wake(self) -> None:
        if not self._configured():
            # Local dev / CI — no GCP creds, no wake-up. Silent return.
            return

        now = time.monotonic()
        elapsed = now - self._last_wake_at
        if elapsed < self._settings.worker_wake_throttle_seconds:
            logger.debug(
                "worker pool wake skipped (%.1fs since last, throttle %.1fs)",
                elapsed, self._settings.worker_wake_throttle_seconds,
            )
            return

        s = self._settings
        pool_name = (
            f"projects/{s.gcp_project_id}/locations/{s.gcp_region}"
            f"/workerPools/{s.worker_pool_name}"
        )

        try:
            if self._client is None:
                self._client = run_v2.WorkerPoolsAsyncClient()
            await self._client.update_worker_pool(
                request=run_v2.UpdateWorkerPoolRequest(
                    worker_pool=run_v2.WorkerPool(
                        name=pool_name,
                        scaling=run_v2.WorkerPoolScaling(manual_instance_count=1),
                    ),
                    update_mask={"paths": ["scaling.manual_instance_count"]},
                ),
            )
            self._last_wake_at = now
            logger.info("worker pool %s woken", s.worker_pool_name)
        except Exception:
            # Swallow — execute_workflow already enqueued the task, so
            # a failed wake only delays pickup. Next execute retries.
            logger.exception(
                "worker pool wake failed for %s — task will pick up on next wake",
                s.worker_pool_name,
            )
