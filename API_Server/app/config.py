"""Runtime settings — loaded from environment variables via pydantic-settings.

Every secret (`JWT_SECRET`, `DATABASE_URL`) comes from the environment. There
are no production-shaped defaults in this file — the `.env.example` template
holds dev placeholders.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 60
    jwt_verify_email_ttl_hours: int = 24

    email_sender: Literal["console", "smtp"] = "console"
    app_base_url: str = "http://localhost:8000"

    # Password policy — kept here so router/service stay numeric-free.
    password_min_length: int = Field(default=8, ge=8)
    bcrypt_cost: int = Field(default=12, ge=4, le=15)

    # PLAN_02 — per-plan workflow quotas. Override via env if the business
    # tier pricing changes. approaching_limit fires at 90% of the cap.
    agent_jwt_ttl_hours: int = 24
    celery_broker_url: str = ""

    # ADR-021 §5 stopgap — "inline" runs the DAG in-process via
    # Execution_Engine._execute (bypasses Celery) so the e2e path works in
    # environments without a Worker Pool. "celery" is the steady state.
    # PLAN_21 Phase 6 removes this field + the inline branch.
    serverless_execution_mode: Literal["celery", "inline"] = "celery"

    # ADR-021 §5-b — Cloud Run Worker Pools wake-up. Empty values disable
    # the patch call (local/CI). Terraform outputs plug these in prod.
    gcp_project_id: str = ""
    gcp_region: str = ""
    worker_pool_name: str = ""
    # 30s covers typical burst-execute patterns without hammering the Admin
    # API (quota: 60 writes/min/project as of 2026-04). Tune via env if
    # observed traffic justifies a lower ceiling.
    worker_wake_throttle_seconds: float = 30.0

    # Fernet master key (base64) for CredentialStore. ADR-004.
    # Tests may generate an ephemeral key via Fernet.generate_key().
    credential_master_key: str = ""

    # ADR-019 — Google OAuth2 (Authorization Code + Refresh Token). Client
    # registered in GCP Console; redirect_uri must match exactly (Cloud Run
    # run.app URL in testing mode).
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""

    # PLAN_02 AI Composer — Anthropic API. Empty key disables the router
    # (returns 503) so local/CI without secrets still boots. Operator-owned
    # key, NOT the per-user credential pool.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    # Local UI testing escape hatch — when true, AIComposerService uses a
    # deterministic StubLLMBackend (no network, no cost) so you can drive
    # ChatPanel end-to-end against a plain uvicorn. Takes precedence over
    # `anthropic_api_key` so you don't have to unset the key to flip modes.
    ai_composer_use_stub: bool = False
    # Per-user rate limit. PR A uses an in-memory token bucket — single Cloud
    # Run instance only. PR B replaces this with Redis-backed counter so the
    # limit holds across the autoscaler.
    ai_compose_rate_per_minute: int = Field(default=10, ge=1)
    ai_compose_max_tokens: int = Field(default=4096, ge=512, le=16384)

    # PLAN_11 PR 1 — AI_Agent split. When ai_agent_base_url is set, the
    # container prefers AIAgentHTTPBackend over the in-tree Anthropic/Stub
    # backends. Empty falls back to the PLAN_02 local-backend path so
    # tests and envs without AI_Agent still boot.
    ai_agent_base_url: str = ""
    ai_agent_timeout_s: float = Field(default=60.0, ge=1.0)
    # Bearer token attached to outbound calls. Modal endpoint requires it
    # (FastAPI BearerAuth middleware in AI_Agent gates /v1/* on a match).
    # Same value lives in GCP Secret Manager `agent-bearer-token-<env>` and
    # Modal Secret `agent-bearer-token`. Empty disables the header (local
    # AI_Agent without auth).
    agent_bearer_token: str = ""

    @property
    def scheduler_jobstore_url(self) -> str:
        # APScheduler's SQLAlchemyJobStore is sync. Route it to psycopg3 sync
        # (shipped via Database's `psycopg[binary]` dep) instead of SQLAlchemy's
        # default psycopg2, which we don't depend on — a fresh pip install from
        # pyproject.toml has no psycopg2 and startup would ImportError.
        return self.database_url.replace("+asyncpg", "+psycopg")

    workflow_limit_light: int = Field(default=100, ge=1)
    workflow_limit_middle: int = Field(default=200, ge=1)
    workflow_limit_heavy: int = Field(default=500, ge=1)

    def workflow_limit_for_tier(self, plan_tier: str) -> int:
        return {
            "light": self.workflow_limit_light,
            "middle": self.workflow_limit_middle,
            "heavy": self.workflow_limit_heavy,
        }[plan_tier]
