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

    # Fernet master key (base64) for CredentialStore. ADR-004.
    # Tests may generate an ephemeral key via Fernet.generate_key().
    credential_master_key: str = ""

    # ADR-019 — Google OAuth2 (Authorization Code + Refresh Token). Client
    # registered in GCP Console; redirect_uri must match exactly (Cloud Run
    # run.app URL in testing mode).
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""

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
