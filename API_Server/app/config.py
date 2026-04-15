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
