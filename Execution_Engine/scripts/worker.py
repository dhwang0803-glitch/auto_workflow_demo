"""Celery worker entry point.

Usage:
    python scripts/worker.py
    # or directly:
    celery -A src.dispatcher.serverless:celery_app worker --loglevel=info
"""
from src.dispatcher.serverless import celery_app

if __name__ == "__main__":
    celery_app.worker_main([
        "worker",
        "--loglevel=info",
        "--concurrency=4",
        "--queues=workflow_tasks",
    ])
