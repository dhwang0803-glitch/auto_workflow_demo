"""Mint a fresh access JWT for local UI testing.

Why this exists:
    `Frontend/.env.local` has `NEXT_PUBLIC_DEV_TOKEN=...` so a developer can
    skip the register/login UI when poking at /editor. The JWT TTL is short
    (60 min by default), so the token rots between sessions. Manually
    re-running curl /register + /login + grep for the token is tedious.

What it does:
    1. Loads API_Server `Settings` from `.env`
    2. Upserts a verified user `dev@local.test` (password is irrelevant —
       JWT auth doesn't touch it after issuance)
    3. Mints an access JWT via `AuthService.issue_access_token`
    4. Rewrites `Frontend/.env.local` so `NEXT_PUBLIC_DEV_TOKEN` reflects
       the new token (other keys preserved)

Run from API_Server/ so `.env` resolves correctly:
    python scripts/mint_dev_token.py

Then restart `pnpm dev` to pick up the new env var.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

# Allow `python scripts/mint_dev_token.py` from API_Server/ — the repo
# package layout doesn't put scripts/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.container import AppContainer

DEV_EMAIL = "dev@local.test"
DEV_PASSWORD = "dev-local-only-not-a-secret"


async def _amain() -> None:
    settings = Settings()
    container = AppContainer(settings=settings)
    try:
        existing = await container.user_repo.get_by_email(DEV_EMAIL)
        if existing is None:
            password_hash = container.auth_service._hash_password(DEV_PASSWORD)
            user = await container.user_repo.create(
                email=DEV_EMAIL,
                password_hash=password_hash,
            )
            await container.user_repo.mark_verified(user.id)
            print(f"created dev user {DEV_EMAIL} (id={user.id})")
        else:
            user = existing
            if not user.is_verified:
                await container.user_repo.mark_verified(user.id)
            print(f"reusing dev user {DEV_EMAIL} (id={user.id})")

        token = container.auth_service.issue_access_token(user.id)
    finally:
        await container.dispose()

    env_local = Path(__file__).resolve().parents[2] / "Frontend" / ".env.local"
    _write_token(env_local, token)
    print(f"wrote NEXT_PUBLIC_DEV_TOKEN to {env_local}")
    print(f"TTL = {settings.jwt_access_ttl_minutes} min - restart `pnpm dev` to pick it up")


def _write_token(path: Path, token: str) -> None:
    line = f"NEXT_PUBLIC_DEV_TOKEN={token}\n"
    if not path.exists():
        path.write_text(line, encoding="utf-8")
        return
    contents = path.read_text(encoding="utf-8")
    pattern = re.compile(r"^NEXT_PUBLIC_DEV_TOKEN=.*$", re.MULTILINE)
    if pattern.search(contents):
        new = pattern.sub(f"NEXT_PUBLIC_DEV_TOKEN={token}", contents)
    else:
        new = contents.rstrip("\n") + "\n" + line
    path.write_text(new, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(_amain())
