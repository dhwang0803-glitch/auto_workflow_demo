"""Celery worker entry point.

Usage:
    python scripts/worker.py
    # or directly:
    celery -A src.dispatcher.serverless:celery_app worker --loglevel=info

Pool choice: `-P solo` runs one task at a time in the main process.
Prefork (Celery default) is Linux-only in practice — it breaks on Windows
with "ValueError: not enough values to unpack (expected 3, got 0)" from
billiard's fast_trace_task. Solo is also a fine fit for the current
workload (a handful of executions per minute per worker); scale out by
starting more worker processes rather than raising concurrency here.
"""
import os
import sys

from src.dispatcher.serverless import celery_app

if __name__ == "__main__":
    # ADR-021 §3 — fail fast if the broker env isn't wired. Cloud Run
    # Worker Pool provisions CELERY_BROKER_URL via Terraform; a missing
    # value means the pool rolled out without the env and tasks would
    # silently queue into the wrong place.
    if not os.environ.get("CELERY_BROKER_URL"):
        sys.stderr.write(
            "CELERY_BROKER_URL must be set before starting the worker "
            "(ADR-021 §3). In Cloud Run, check the Worker Pool's env; "
            "locally, export it to point at your docker-compose redis.\n"
        )
        sys.exit(1)

    celery_app.worker_main([
        "worker",
        "--loglevel=info",
        "--pool=solo",
        "--queues=workflow_tasks",
    ])
