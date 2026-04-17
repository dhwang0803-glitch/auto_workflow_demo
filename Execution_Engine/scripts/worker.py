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
from src.dispatcher.serverless import celery_app

if __name__ == "__main__":
    celery_app.worker_main([
        "worker",
        "--loglevel=info",
        "--pool=solo",
        "--queues=workflow_tasks",
    ])
