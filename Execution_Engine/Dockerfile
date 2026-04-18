# Build from repo root: docker build -f Execution_Engine/Dockerfile -t auto-workflow-ee .
# Serverless Worker image — consumes Celery queue `workflow_tasks` via Redis broker.
# Not an HTTP server; do not deploy to Cloud Run directly (ADR-021 will decide
# Cloud Tasks vs. managed Celery path).

FROM python:3.13-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY Database/ ./Database/
COPY Execution_Engine/ ./Execution_Engine/

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ./Database \
    && pip install --no-cache-dir ./Execution_Engine


FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# celery_app.config_from_object("config.celery_config") imports from a top-level
# `config` package. The pyproject excludes config/ from the installed wheel (it
# stays env-specific, not code), so ship it alongside the app on PYTHONPATH.
COPY Execution_Engine/config/ /app/config/

USER appuser

CMD ["celery", "-A", "src.dispatcher.serverless:celery_app", "worker", \
     "--loglevel=info", "--queues=workflow_tasks", "--concurrency=4"]
