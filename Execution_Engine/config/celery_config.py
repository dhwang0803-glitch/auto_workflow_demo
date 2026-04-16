import os

broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
task_serializer = "json"
accept_content = ["json"]
task_acks_late = True
worker_prefetch_multiplier = 1
