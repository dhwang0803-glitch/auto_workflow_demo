# Build from repo root: docker build -f API_Server/Dockerfile -t auto-workflow-api .
# Cloud Run binds $PORT (default 8080) on 0.0.0.0.

FROM python:3.13-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY Database/ ./Database/
COPY Execution_Engine/ ./Execution_Engine/
COPY API_Server/ ./API_Server/

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# Execution_Engine is an install-time dep of API_Server only for
# serverless_execution_mode="inline" (ADR-021 §5 stopgap). PLAN_21 Phase 6
# will drop both the dep and this COPY.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ./Database \
    && pip install --no-cache-dir ./Execution_Engine \
    && pip install --no-cache-dir ./API_Server


FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app
USER appuser

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
