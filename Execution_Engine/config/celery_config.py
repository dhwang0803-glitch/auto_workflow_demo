"""Celery worker configuration.

Broker + timeouts for the serverless execution path (ADR-021 §7).
"""
import os

# ADR-021 §3 — broker URL is wired by the Worker Pool env (CELERY_BROKER_URL
# comes from Terraform, composed as `redis://<broker.host>:<broker.port>/0`).
# The old `redis://localhost:6379/0` default was removed because it quietly
# pointed at a non-existent Redis in Cloud Run, turning a deploy bug into an
# opaque "task queued but never picked up" runtime bug.
#
# `.get()` (not `[...]`) so `import` succeeds in test contexts where the env
# isn't set; scripts/worker.py fail-fasts on a None broker before
# celery_app.worker_main(). For local dev set the env explicitly
# (docker-compose spins up redis on localhost:6379).
broker_url = os.environ.get("CELERY_BROKER_URL")

# Result backend shares the broker instance; DB 1 keeps SETNX idempotency
# keys (DB 0) separate from result payloads.
result_backend = os.environ.get("CELERY_RESULT_BACKEND") or (
    broker_url.rsplit("/", 1)[0] + "/1" if broker_url else None
)

task_serializer = "json"
accept_content = ["json"]
task_acks_late = True
worker_prefetch_multiplier = 1

# ADR-021 §7 — warm-shutdown budget. Cloud Run Worker Pools gives SIGTERM +
# 10s grace before SIGKILL. Soft limit raises SoftTimeLimitExceeded inside
# the task so finally-blocks (DB commit, Redis release) get a chance before
# the hard kill. Hard limit is the wall-clock ceiling — a stuck task that
# ignores the soft limit dies cleanly here instead of holding up drain.
task_soft_time_limit = 8
task_time_limit = 30
