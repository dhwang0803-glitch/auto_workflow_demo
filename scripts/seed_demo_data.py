"""seed_demo_data.py — fixture seed for the 30-second demo recording.

After running, the local Postgres has exactly the surface the recording
script expects:

  - users:    alice@demo.local + bob@demo.local (both verified)
  - skills:   1 active workspace skill — "Notify finance on invoices"
              (owner=alice; visible to anyone via the workspace pool)
  - workflows: 1 alice-owned "Invoice Pipeline" at revision_source=ai_draft
               with the minimal http_request → slack_notify graph PR-K's
               compose path renders by default

The personal-skill side of the recording (Track B) is created LIVE on
camera by editing the workflow + saving — so the seed deliberately does
NOT pre-populate any scope='user' rows. Track C just shares whatever
Track B produced; no extra seed needed.

Output prints both passwords so you can paste them into the alice/bob
browser windows. Re-running truncates `users` first (CASCADE), so each
take starts identical.

Usage (PowerShell, secret-safe):

    \$env:PYTHONUTF8 = "1"
    python -c "
    import os, runpy
    from dotenv import dotenv_values
    for k, v in dotenv_values('AI_Agent/.env').items():
        if v: os.environ.setdefault(k, v)
    os.environ['DATABASE_URL'] = 'postgresql+asyncpg://auto_workflow:auto_workflow@localhost:5435/auto_workflow'
    runpy.run_path('scripts/seed_demo_data.py', run_name='__main__')
    "

The script does NOT call the live Modal endpoint — it only writes to
Postgres. Modal warm-up is a separate step before recording.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from uuid import UUID

REPO = pathlib.Path(__file__).resolve().parent.parent
# AI_Agent has its own `app/` package — keep it off sys.path so the
# import here resolves to API_Server's `app`.
sys.path.insert(0, str(REPO / "Database"))
sys.path.insert(0, str(REPO / "API_Server"))


# Same env prep as `run_demo_scenarios.py` — `app.main` builds an
# `app = create_app()` at module load so Settings() needs the JWT bits
# in the env before any `from app...` import lands.
def _prep_env() -> None:
    from cryptography.fernet import Fernet

    os.environ.setdefault(
        "JWT_SECRET", "demo-seed-jwt-secret-32-bytes-min!"
    )
    os.environ.setdefault(
        "CREDENTIAL_MASTER_KEY", Fernet.generate_key().decode("utf-8")
    )
    os.environ.setdefault("JWT_ALGORITHM", "HS256")
    os.environ.setdefault("JWT_ACCESS_TTL_MINUTES", "60")
    os.environ.setdefault("JWT_VERIFY_EMAIL_TTL_HOURS", "24")
    os.environ.setdefault("EMAIL_SENDER", "console")
    os.environ.setdefault("APP_BASE_URL", "http://testserver")
    os.environ.setdefault("BCRYPT_COST", "4")
    os.environ.setdefault("AI_COMPOSE_RATE_PER_MINUTE", "200")


_prep_env()

from cryptography.fernet import Fernet  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402


ALICE_EMAIL = "alice@example.com"
BOB_EMAIL = "bob@example.com"
PASSWORD = "demo-take-five-9"  # both accounts, same value for simplicity


def _make_settings():
    from app.config import Settings

    return Settings(
        database_url=os.environ["DATABASE_URL"],
        jwt_secret=os.environ["JWT_SECRET"],
        jwt_algorithm="HS256",
        jwt_access_ttl_minutes=60,
        jwt_verify_email_ttl_hours=24,
        email_sender="console",
        app_base_url="http://testserver",
        bcrypt_cost=4,
        credential_master_key=os.environ["CREDENTIAL_MASTER_KEY"],
        anthropic_api_key="",
        ai_compose_rate_per_minute=200,
        ai_compose_max_tokens=2048,
        # Modal endpoint isn't required for seed — the script only
        # writes to Postgres — but the Settings model requires SOMETHING
        # for `ai_agent_base_url` to be falsy-or-set. Empty string keeps
        # the personalization service unwired, which is fine here.
        ai_agent_base_url=os.environ.get("AGENT_URL", ""),
        agent_bearer_token=os.environ.get("AGENT_BEARER_TOKEN", ""),
    )


async def _truncate(app) -> None:
    sm = app.state.sessionmaker
    async with sm() as s, s.begin():
        await s.execute(text("TRUNCATE users CASCADE"))


async def _register_and_verify(
    client: AsyncClient, app, *, email: str
) -> UUID:
    """Register + email-verify, no login. Returns the user's UUID for
    direct ORM inserts (the script seeds skills/workflows by repo, not
    HTTP, so we don't need a JWT)."""
    from urllib.parse import parse_qs, urlparse

    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD},
    )
    assert r.status_code == 201, r.text
    link = next(l for (to, l) in app.state.email_sender.sent if to == email)
    token = parse_qs(urlparse(link).query)["token"][0]
    v = await client.get("/api/v1/auth/verify", params={"token": token})
    assert v.status_code == 200, v.text

    # Fetch user_id from the users table — the verify endpoint doesn't
    # echo it back and the seed needs it for FK use.
    sm = app.state.sessionmaker
    async with sm() as s:
        from auto_workflow_database.models.core import User as UserORM
        from sqlalchemy import select

        row = (
            await s.execute(select(UserORM).where(UserORM.email == email))
        ).scalar_one()
        return row.id


async def _seed_workspace_skill(app, *, owner_id: UUID) -> None:
    await app.state.skill_repo.create(
        owner_user_id=owner_id,
        name="Notify finance on invoices",
        condition={
            "text": "an invoice document arrives in the shared inbox",
        },
        action={
            "text": "post a one-line summary to slack #finance",
        },
        description="When a new invoice arrives, alert the finance "
        "team in Slack so AP can pick it up.",
        status="active",
    )


async def _seed_alice_workflow_v1(app, *, owner_id: UUID) -> UUID:
    """Create the 'Invoice Pipeline' workflow at revision_source='ai_draft'.

    Track B's recording opens this workflow, edits one node config, and
    saves — the save handler then flips revision_source to 'user_edit'
    and PR-G's auto-trigger pulls the candidate.
    """
    graph = {
        "nodes": [
            {
                "id": "fetch_invoices",
                "type": "http_request",
                "config": {
                    "url": "https://api.acme.test/invoices",
                    "method": "GET",
                },
            },
            {
                "id": "notify_finance",
                "type": "slack_notify",
                "config": {"channel": "#finance"},
            },
        ],
        "edges": [
            {"source": "fetch_invoices", "target": "notify_finance"},
        ],
    }

    # `WorkflowRepository.save` takes the full DTO, not a payload —
    # we mint the id client-side so we can also use it for the matching
    # revision row in the same shot. PR-Ba's WorkflowRevisionRepository
    # writes the initial ai_draft revision so the editor opens at v1.
    from auto_workflow_database.repositories.base import Workflow
    from uuid import uuid4

    wf_id = uuid4()
    await app.state.workflow_repo.save(
        Workflow(
            id=wf_id,
            owner_id=owner_id,
            name="Invoice Pipeline",
            settings={},
            graph=graph,
        )
    )
    await app.state.workflow_revision_repo.record(
        workflow_id=wf_id,
        source="ai_draft",
        payload=graph,
        parent_revision_id=None,
        created_by=owner_id,
    )
    return wf_id


async def _async_main() -> int:
    from app.main import create_app
    from app.services.email_sender import NoopEmailSender

    settings = _make_settings()
    app = create_app(settings, email_sender=NoopEmailSender())
    transport = ASGITransport(app=app)

    async with app.router.lifespan_context(app), AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        print("== seed_demo_data ==")
        await _truncate(app)
        print("[1/4] truncated users (cascade)")

        alice_id = await _register_and_verify(c, app, email=ALICE_EMAIL)
        print(f"[2/4] alice registered + verified  ({alice_id})")
        bob_id = await _register_and_verify(c, app, email=BOB_EMAIL)
        print(f"[2/4] bob   registered + verified  ({bob_id})")

        await _seed_workspace_skill(app, owner_id=alice_id)
        print("[3/4] workspace skill seeded — 'Notify finance on invoices'")

        wf_id = await _seed_alice_workflow_v1(app, owner_id=alice_id)
        print(f"[4/4] alice workflow v1 seeded     ({wf_id})")

    print()
    print("== ready to record ==")
    print(f"  alice email   : {ALICE_EMAIL}")
    print(f"  bob   email   : {BOB_EMAIL}")
    print(f"  password      : {PASSWORD}")
    print(f"  workflow      : Invoice Pipeline ({wf_id})")
    print()
    print("Next:")
    print("  1. modal warm-up (one /v1/health call) so the take's first")
    print("     compose isn't a 30-50s cold-start.")
    print("  2. open alice in chrome, bob in incognito, both at /skills")
    print("     → log in with the credentials above.")
    return 0


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL must be set", file=sys.stderr)
        return 2
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())
