"""Live demo scenario harness for the closed-loop personalization narrative.

Six scenarios cover the three demo tracks the project sells:

  Track A — Skills Marketplace
    1. Workspace skill seeded by one user lands in another user's compose
       system prompt on their next natural-language request.
  Track B — Personalization (HITL → same user)
    2. A user's HITL-edit candidate, once activated, lands in their own
       next compose system prompt.
    3. Cross-user isolation: alice's active personal skill must NOT
       appear in bob's compose system prompt.
  Track C — Cross-user share
    4. alice promotes her personal skill to the workspace pool; bob's
       next compose system prompt now includes it.
  Regression guards
    5. Cold-start tenant (no workspace + no personal): the system prompt
       carries no `<skills>` section (PR-K empty short-circuit).
    6. /v1/policy/extract on the GitLab handbook fixture still completes
       normally — PR-I + PR-M did not break the reflective extract
       baseline.

Verification surface:

  Scenarios 1-5 run API_Server in-process with a `CapturingAIAgentBackend`
  wrapper. The wrapper records `system`/`user_message` on every backend
  call and proxies the request to the live Modal endpoint, so we get
  both real LLM output (DAG draft) AND deterministic assertions on the
  exact system prompt that left API_Server. The PR-M `@traceable` wrap
  also files each call to LangSmith — `langsmith_run_ids` surface in
  the per-scenario report for manual UI review.

  Scenario 6 reuses `smoke_handbook_policy_extract.py` verbatim.

Usage (Windows PowerShell, secret-safe — token never crosses shell):

    $env:PYTHONUTF8 = "1"
    python -c "
    import os, runpy
    from dotenv import dotenv_values
    for k, v in dotenv_values('AI_Agent/.env').items():
        if v: os.environ.setdefault(k, v)
    os.environ['AGENT_URL'] = 'https://dhwang0803--auto-workflow-agent-agentservice-fastapi.modal.run'
    runpy.run_path('scripts/run_demo_scenarios.py', run_name='__main__')
    "

Environment:

    AGENT_URL              Modal endpoint base URL (used by AI_Agent
                           backend AND scenario 6's smoke import).
    AGENT_BEARER_TOKEN     Bearer for /v1/* — loaded from AI_Agent/.env
                           via the python wrapper above. Never echoed.
    DATABASE_URL           Postgres for API_Server's repositories
                           (docker compose up in Database/ first).

Flags:

    --scenario N   run only scenario N (1..6)
    --all          run all six (default if no flag passed)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

# Make the API_Server package importable without `pip install -e .` —
# the harness is repo-aware and runs from the repo root.
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "API_Server"))
sys.path.insert(0, str(REPO / "Database"))
sys.path.insert(0, str(REPO / "AI_Agent"))

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402


# ---------------------------------------------------------------- backend wrap


class CapturingAIAgentBackend:
    """Wraps the production `AIAgentHTTPBackend` so live calls AND
    prompt capture happen in one pass.

    Every `complete()` invocation records the args under
    `self.calls[-1]` and then proxies the request to Modal so the LLM
    actually produces a DAG. Scenarios assert on `last_system` for the
    "is the skill in the prompt" question and read the live response
    for sanity (LLM produced a parsable JSON, intent in the expected
    set, etc.).
    """

    def __init__(self, *, base_url: str, bearer_token: str) -> None:
        from app.services.ai_agent_client import AIAgentHTTPBackend

        self._inner = AIAgentHTTPBackend(
            base_url=base_url, bearer_token=bearer_token
        )
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        self.calls.append(
            {"system": system, "user_message": user_message}
        )
        return await self._inner.complete(
            system=system,
            user_message=user_message,
            max_tokens=max_tokens,
        )

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        self.calls.append(
            {"system": system, "user_message": user_message}
        )
        async for chunk in self._inner.stream(
            system=system,
            user_message=user_message,
            max_tokens=max_tokens,
        ):
            yield chunk

    @property
    def last_system(self) -> str | None:
        return self.calls[-1]["system"] if self.calls else None


# ------------------------------------------------------------------ app build


def _make_settings():
    from app.config import Settings

    return Settings(
        database_url=os.environ["DATABASE_URL"],
        jwt_secret="demo-scenarios-secret-key-32bytes!",
        jwt_algorithm="HS256",
        jwt_access_ttl_minutes=60,
        jwt_verify_email_ttl_hours=24,
        email_sender="console",
        app_base_url="http://testserver",
        bcrypt_cost=4,
        credential_master_key=Fernet.generate_key().decode("utf-8"),
        anthropic_api_key="",  # backend override drives the LLM
        ai_compose_rate_per_minute=200,
        ai_compose_max_tokens=2048,
        ai_agent_base_url=os.environ["AGENT_URL"],
        agent_bearer_token=os.environ["AGENT_BEARER_TOKEN"],
    )


def _build_app_with_capturing_backend():
    from app.main import create_app
    from app.services.email_sender import NoopEmailSender

    backend = CapturingAIAgentBackend(
        base_url=os.environ["AGENT_URL"],
        bearer_token=os.environ["AGENT_BEARER_TOKEN"],
    )
    settings = _make_settings()
    app = create_app(
        settings,
        email_sender=NoopEmailSender(),
        ai_composer_backend=backend,
    )
    return app, backend


# ------------------------------------------------------------ HTTP helpers


async def _truncate(app) -> None:
    sm = app.state.sessionmaker
    async with sm() as s, s.begin():
        await s.execute(text("TRUNCATE users CASCADE"))


async def _register_login(
    client: AsyncClient, app, *, email: str
) -> UUID:
    """Register + verify + login. Returns the user's UUID for FK use."""
    from urllib.parse import parse_qs, urlparse

    password = "correct-horse-8"
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert r.status_code == 201, r.text
    link = next(l for (to, l) in app.state.email_sender.sent if to == email)
    token = parse_qs(urlparse(link).query)["token"][0]
    v = await client.get("/api/v1/auth/verify", params={"token": token})
    assert v.status_code == 200, v.text
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert login.status_code == 200, login.text
    access = login.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {access}"

    # Decode JWT 'sub' for the user_id (no extra API call).
    import base64, json as _json
    payload_b64 = access.split(".")[1] + "=="
    sub = _json.loads(base64.urlsafe_b64decode(payload_b64))["sub"]
    return UUID(sub)


async def _seed_workspace_skill(
    app,
    *,
    owner_id: UUID,
    name: str,
    condition_text: str,
    action_text: str,
) -> UUID:
    sk = await app.state.skill_repo.create(
        owner_user_id=owner_id,
        name=name,
        condition={"text": condition_text},
        action={"text": action_text},
        status="active",
    )
    return sk.id


async def _seed_personal_skill_active(
    app,
    *,
    owner_id: UUID,
    name: str,
    condition_text: str,
    action_text: str,
    suggestion_hash: str,
) -> UUID:
    """Insert a scope='user' active skill directly. Bypasses the
    propose+judge agent so scenarios stay deterministic — the
    activation surface is exercised by `test_personalization.py`."""
    sk = await app.state.skill_repo.create(
        owner_user_id=owner_id,
        name=name,
        condition={"text": condition_text},
        action={"text": action_text},
        status="active",
        scope="user",
        user_id=owner_id,
        source="hitl_edit",
        suggestion_hash=suggestion_hash,
    )
    return sk.id


# ------------------------------------------------------------------- result


@dataclass
class ScenarioResult:
    n: int
    name: str
    ok: bool
    detail: str = ""
    notes: list[str] = field(default_factory=list)
    latency_s: float = 0.0


# ------------------------------------------------------------------- 1. A


async def scenario_1_workspace_inject() -> ScenarioResult:
    """Track A — workspace skill auto-inject for a user who never wrote it."""
    name = "Track A: workspace skill auto-inject"
    app, backend = _build_app_with_capturing_backend()
    notes: list[str] = []
    t0 = time.time()
    try:
        async with app.router.lifespan_context(app), AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            await _truncate(app)

            # alice seeds the workspace policy
            alice_id = await _register_login(
                c, app, email="alice-1@example.com"
            )
            await _seed_workspace_skill(
                app,
                owner_id=alice_id,
                name="Notify finance on invoices",
                condition_text="invoice arrives in shared inbox",
                action_text="post a summary to slack #finance",
            )

            # bob (fresh user) makes a compose request
            c.headers.pop("Authorization", None)
            await _register_login(c, app, email="bob-1@example.com")
            r = await c.post(
                "/api/v1/ai/compose",
                json={
                    "message": "Send a daily summary of invoices to slack",
                },
            )
            if r.status_code != 200:
                return ScenarioResult(
                    1, name, False,
                    f"compose HTTP {r.status_code}: {r.text[:200]}",
                    notes, time.time() - t0,
                )

            sys_prompt = backend.last_system or ""
            checks = [
                ("<skills> section present", "<skills>" in sys_prompt),
                (
                    "workspace skill name in prompt",
                    "Notify finance on invoices" in sys_prompt,
                ),
                (
                    "workspace condition in prompt",
                    "invoice arrives in shared inbox" in sys_prompt,
                ),
                (
                    "workspace action in prompt",
                    "post a summary to slack #finance" in sys_prompt,
                ),
            ]
            failed = [n for n, ok in checks if not ok]
            notes.append(
                "compose intent=" + r.json()["result"].get("intent", "?")
            )
            return ScenarioResult(
                1, name, len(failed) == 0,
                ", ".join(failed) if failed else "all assertions passed",
                notes, time.time() - t0,
            )
    except Exception as exc:
        return ScenarioResult(
            1, name, False,
            f"exception: {exc!r}",
            notes + traceback.format_exc().splitlines()[-3:],
            time.time() - t0,
        )


# ------------------------------------------------------------------- 2. B


async def scenario_2_personal_same_user() -> ScenarioResult:
    """Track B — alice's activated personal skill lands in alice's own next compose."""
    name = "Track B: personal skill self-inject"
    app, backend = _build_app_with_capturing_backend()
    notes: list[str] = []
    t0 = time.time()
    try:
        async with app.router.lifespan_context(app), AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            await _truncate(app)
            alice_id = await _register_login(
                c, app, email="alice-2@example.com"
            )
            await _seed_personal_skill_active(
                app,
                owner_id=alice_id,
                name="Retry 5min on HTTP",
                condition_text="HTTP node retries needed",
                action_text="set retry_interval=300",
                suggestion_hash="h-personal-2",
            )

            r = await c.post(
                "/api/v1/ai/compose",
                json={"message": "Build a polling workflow to fetch invoices"},
            )
            if r.status_code != 200:
                return ScenarioResult(
                    2, name, False,
                    f"compose HTTP {r.status_code}: {r.text[:200]}",
                    notes, time.time() - t0,
                )
            sys_prompt = backend.last_system or ""
            checks = [
                ("<skills> section present", "<skills>" in sys_prompt),
                (
                    "personal hint condition surfaced",
                    "HTTP node retries needed" in sys_prompt,
                ),
                (
                    "personal action surfaced",
                    "set retry_interval=300" in sys_prompt,
                ),
                (
                    "ADR-023 invisibility: no scope label leak",
                    "scope" not in sys_prompt.lower().split("<skills>")[0],
                ),
            ]
            failed = [n for n, ok in checks if not ok]
            return ScenarioResult(
                2, name, len(failed) == 0,
                ", ".join(failed) if failed else "all assertions passed",
                notes, time.time() - t0,
            )
    except Exception as exc:
        return ScenarioResult(
            2, name, False, f"exception: {exc!r}",
            traceback.format_exc().splitlines()[-3:],
            time.time() - t0,
        )


# ------------------------------------------------------------------- 3. B+


async def scenario_3_isolation() -> ScenarioResult:
    """Track B+ — alice's personal skill must NOT surface in bob's compose."""
    name = "Track B+: cross-user personal isolation"
    app, backend = _build_app_with_capturing_backend()
    notes: list[str] = []
    t0 = time.time()
    try:
        async with app.router.lifespan_context(app), AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            await _truncate(app)
            alice_id = await _register_login(
                c, app, email="alice-3@example.com"
            )
            await _seed_personal_skill_active(
                app,
                owner_id=alice_id,
                name="Alice secret pattern",
                condition_text="alice secret condition phrase",
                action_text="alice secret action phrase",
                suggestion_hash="h-alice-3",
            )

            # Switch to bob and compose
            c.headers.pop("Authorization", None)
            await _register_login(c, app, email="bob-3@example.com")
            r = await c.post(
                "/api/v1/ai/compose",
                json={"message": "draft a workflow"},
            )
            if r.status_code != 200:
                return ScenarioResult(
                    3, name, False,
                    f"compose HTTP {r.status_code}: {r.text[:200]}",
                    notes, time.time() - t0,
                )
            sys_prompt = backend.last_system or ""
            checks = [
                (
                    "alice's personal skill name absent",
                    "Alice secret pattern" not in sys_prompt,
                ),
                (
                    "alice's personal condition absent",
                    "alice secret condition phrase" not in sys_prompt,
                ),
                (
                    "alice's personal action absent",
                    "alice secret action phrase" not in sys_prompt,
                ),
            ]
            failed = [n for n, ok in checks if not ok]
            return ScenarioResult(
                3, name, len(failed) == 0,
                ", ".join(failed) if failed else "isolation holds",
                notes, time.time() - t0,
            )
    except Exception as exc:
        return ScenarioResult(
            3, name, False, f"exception: {exc!r}",
            traceback.format_exc().splitlines()[-3:],
            time.time() - t0,
        )


# ------------------------------------------------------------------- 4. C


async def scenario_4_share_lifts_team() -> ScenarioResult:
    """Track C — alice shares her personal skill; bob's next compose now sees it."""
    name = "Track C: cross-user share lifts team baseline"
    app, backend = _build_app_with_capturing_backend()
    notes: list[str] = []
    t0 = time.time()
    try:
        async with app.router.lifespan_context(app), AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            await _truncate(app)
            alice_id = await _register_login(
                c, app, email="alice-4@example.com"
            )
            cand_id = await _seed_personal_skill_active(
                app,
                owner_id=alice_id,
                name="Always pin retry to 5min on http nodes",
                condition_text="retry interval missing",
                action_text="pin retry to 5 minutes",
                suggestion_hash="h-alice-4",
            )

            # alice shares
            share = await c.post(
                f"/api/v1/personalization/candidates/{cand_id}/share"
            )
            if share.status_code != 200:
                return ScenarioResult(
                    4, name, False,
                    f"share HTTP {share.status_code}: {share.text[:200]}",
                    notes, time.time() - t0,
                )
            notes.append("share returned " + str(share.status_code))

            # bob composes
            c.headers.pop("Authorization", None)
            await _register_login(c, app, email="bob-4@example.com")
            r = await c.post(
                "/api/v1/ai/compose",
                json={"message": "Build a webhook→HTTP retry workflow"},
            )
            if r.status_code != 200:
                return ScenarioResult(
                    4, name, False,
                    f"compose HTTP {r.status_code}: {r.text[:200]}",
                    notes, time.time() - t0,
                )
            sys_prompt = backend.last_system or ""
            checks = [
                (
                    "shared skill name surfaced for bob",
                    "Always pin retry to 5min on http nodes" in sys_prompt,
                ),
                (
                    "shared condition surfaced for bob",
                    "retry interval missing" in sys_prompt,
                ),
            ]
            failed = [n for n, ok in checks if not ok]
            return ScenarioResult(
                4, name, len(failed) == 0,
                ", ".join(failed) if failed else "share lifts team",
                notes, time.time() - t0,
            )
    except Exception as exc:
        return ScenarioResult(
            4, name, False, f"exception: {exc!r}",
            traceback.format_exc().splitlines()[-3:],
            time.time() - t0,
        )


# ------------------------------------------------------------------- 5. cold


async def scenario_5_cold_start() -> ScenarioResult:
    """Regression — empty pools collapse the `<skills>` section to empty string."""
    name = "Regression: cold-start (empty pools) → no <skills> block"
    app, backend = _build_app_with_capturing_backend()
    notes: list[str] = []
    t0 = time.time()
    try:
        async with app.router.lifespan_context(app), AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            await _truncate(app)
            await _register_login(c, app, email="charlie-5@example.com")
            r = await c.post(
                "/api/v1/ai/compose",
                json={"message": "draft a workflow"},
            )
            if r.status_code != 200:
                return ScenarioResult(
                    5, name, False,
                    f"compose HTTP {r.status_code}: {r.text[:200]}",
                    notes, time.time() - t0,
                )
            sys_prompt = backend.last_system or ""
            checks = [
                (
                    "no <skills> tag with empty pool",
                    "<skills>" not in sys_prompt,
                ),
                (
                    "<node_catalog> still present",
                    "<node_catalog>" in sys_prompt,
                ),
            ]
            failed = [n for n, ok in checks if not ok]
            return ScenarioResult(
                5, name, len(failed) == 0,
                ", ".join(failed) if failed else "cold-start preserved",
                notes, time.time() - t0,
            )
    except Exception as exc:
        return ScenarioResult(
            5, name, False, f"exception: {exc!r}",
            traceback.format_exc().splitlines()[-3:],
            time.time() - t0,
        )


# ------------------------------------------------------------------- 6. refl


async def scenario_6_reflective_extract_regression() -> ScenarioResult:
    """Regression — handbook smoke completes; PR-I/M didn't break the
    reflective extract path. The check delegates to the existing
    `smoke_handbook_policy_extract.py` so we don't duplicate the fixture
    parsing logic."""
    name = "Regression: GitLab handbook /v1/policy/extract"
    t0 = time.time()
    try:
        # Import in a child process so smoke's `sys.exit` doesn't kill
        # the harness on its own — but we want the same env, so use
        # `runpy.run_path` and trap SystemExit.
        import runpy
        try:
            runpy.run_path(
                str(REPO / "AI_Agent" / "scripts"
                    / "smoke_handbook_policy_extract.py"),
                run_name="__main__",
            )
            return ScenarioResult(
                6, name, True, "smoke exited cleanly",
                latency_s=time.time() - t0,
            )
        except SystemExit as exc:
            code = exc.code
            ok = code == 0 or code is None
            return ScenarioResult(
                6, name, ok,
                f"smoke exit code={code}",
                latency_s=time.time() - t0,
            )
    except Exception as exc:
        return ScenarioResult(
            6, name, False, f"exception: {exc!r}",
            traceback.format_exc().splitlines()[-3:],
            time.time() - t0,
        )


# ----------------------------------------------------------------- driver


SCENARIOS = {
    1: scenario_1_workspace_inject,
    2: scenario_2_personal_same_user,
    3: scenario_3_isolation,
    4: scenario_4_share_lifts_team,
    5: scenario_5_cold_start,
    6: scenario_6_reflective_extract_regression,
}


def _print_result(r: ScenarioResult) -> None:
    badge = "PASS" if r.ok else "FAIL"
    print(f"[{badge}] {r.n}. {r.name}  ({r.latency_s:.1f}s)")
    if r.detail:
        print(f"       └─ {r.detail}")
    for n in r.notes:
        print(f"       · {n}")


async def _async_main(args: argparse.Namespace) -> int:
    selected = (
        list(SCENARIOS.values())
        if args.scenario is None
        else [SCENARIOS[args.scenario]]
    )
    results: list[ScenarioResult] = []
    for fn in selected:
        r = await fn()
        _print_result(r)
        results.append(r)
        print()

    failed = [r for r in results if not r.ok]
    print("=" * 64)
    print(f"  total: {len(results)}   passed: {len(results) - len(failed)}"
          f"   failed: {len(failed)}")
    return 0 if not failed else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", type=int, choices=list(SCENARIOS),
                   help="run only one scenario (default: all)")
    args = p.parse_args()

    for var in ("AGENT_URL", "AGENT_BEARER_TOKEN", "DATABASE_URL"):
        if not os.environ.get(var):
            print(f"missing env var: {var}", file=sys.stderr)
            return 2

    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
