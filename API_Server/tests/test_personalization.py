"""HITL personalization router tests — PLAN_14 PR-G.

Real Postgres for repositories (skill / personal_skill_review / workflow
/ workflow_revision) + a fake AIAgentHTTPBackend whose
`extract_personalization_from_diff` returns canned payloads. The pattern
mirrors `test_skills.py`: dependency-override the service factory with
one that injects the fake AI but keeps the real repos.

What we want to catch here:
- Drop reasons map to the right DB write (judge_reject → review row;
  other drops → no row).
- Suppression hash list to AI_Agent merges existing personal skills +
  prior reject rows.
- Cross-user isolation — alice's candidates never appear for bob.
- 422 when the workflow has no diff-eligible revision pair.
- 502 when AI_Agent errors out.
- Workspace skills (scope='workspace') are NOT exposed via the
  personalization GET/activate/reject endpoints.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import create_app
from app.routers.personalization import get_personalization_service
from app.services.ai_agent_client import AIAgentHTTPBackend
from app.services.email_sender import NoopEmailSender
from app.services.personalization_service import PersonalizationService
from tests.conftest import DATABASE_URL, _make_settings

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — personalization tests need Postgres",
)


# --- fake AI ----------------------------------------------------------


class FakePersonalizationAI:
    """Duck-types AIAgentHTTPBackend.extract_personalization_from_diff.

    Each test sets `response` to the canned payload the agent would
    return; `raise_status` triggers an httpx.HTTPStatusError to exercise
    the upstream-failure 502 path. `last_call` captures the arg dict so
    tests can assert on the hash-suppression list.
    """

    def __init__(
        self,
        *,
        response: dict | None = None,
        raise_status: int | None = None,
        upsert_raise_status: int | None = None,
    ) -> None:
        self.response = response
        self.raise_status = raise_status
        # PR-I: upsert is best-effort. Tests that need to exercise the
        # failure path set `upsert_raise_status`; default is success so
        # existing PR-G activate tests keep their semantics.
        self.upsert_raise_status = upsert_raise_status
        self.last_call: dict | None = None
        self.upsert_calls: list[dict] = []

    @staticmethod
    def _err(status: int) -> httpx.HTTPStatusError:
        req = httpx.Request("POST", "http://upstream/")
        resp = httpx.Response(status, request=req)
        return httpx.HTTPStatusError("upstream", request=req, response=resp)

    async def extract_personalization_from_diff(
        self,
        *,
        v1: dict,
        v2: dict,
        rejected_hashes: list[str],
        user_id: str | None,
    ) -> dict:
        self.last_call = {
            "v1": v1,
            "v2": v2,
            "rejected_hashes": list(rejected_hashes),
            "user_id": user_id,
        }
        if self.raise_status is not None:
            raise self._err(self.raise_status)
        assert self.response is not None, "fake AI response not configured"
        return self.response

    async def upsert_personal_memory(
        self,
        *,
        user_id: str,
        skill: dict,
    ) -> dict:
        self.upsert_calls.append({"user_id": user_id, "skill": dict(skill)})
        if self.upsert_raise_status is not None:
            # The real client raises httpx.HTTPError subclasses; we mirror
            # the broad surface so the service's `except httpx.HTTPError`
            # catches every variant a transient Modal blip would produce.
            raise httpx.ConnectError(
                f"upstream unreachable ({self.upsert_raise_status})"
            )
        return {"ok": True, "pool_size": 1, "embedding_source": "server"}


# --- helpers ----------------------------------------------------------


def _accept_outcome(*, hint: str, suggestion_hash: str) -> dict:
    return {
        "outcome": {
            "accepted": True,
            "drop_reason": "",
            "suggestion_hash": suggestion_hash,
            "proposal": {"hint": hint, "is_noise": False, "raw": ""},
            "judgment": {
                "decision": "accept",
                "reason": "",
                "raw": "",
            },
        },
        "diff": {},
        "diff_signature": "sig:" + suggestion_hash,
        "langsmith_run_id": "ls-run-1",
    }


def _judge_reject_outcome(*, suggestion_hash: str, reason: str) -> dict:
    return {
        "outcome": {
            "accepted": False,
            "drop_reason": "judge_reject",
            "suggestion_hash": suggestion_hash,
            "proposal": {"hint": "hint", "is_noise": False, "raw": ""},
            "judgment": {
                "decision": "reject",
                "reason": reason,
                "raw": "",
            },
        },
        "diff": {},
        "diff_signature": "sig:" + suggestion_hash,
        "langsmith_run_id": None,
    }


def _empty_drop_outcome(reason: str) -> dict:
    return {
        "outcome": {
            "accepted": False,
            "drop_reason": reason,
            "suggestion_hash": None,
            "proposal": {"hint": "", "is_noise": True, "raw": ""},
            "judgment": None,
        },
        "diff": {},
        "diff_signature": "",
        "langsmith_run_id": None,
    }


def _graph(node_id: str = "a") -> dict:
    return {
        "nodes": [{"id": node_id, "type": "noop", "config": {}}],
        "edges": [],
    }


async def _truncate(app) -> None:
    sm = app.state.sessionmaker
    async with sm() as s, s.begin():
        await s.execute(text("TRUNCATE users CASCADE"))


async def _register_and_login(
    client: AsyncClient, app, *, email: str
) -> None:
    password = "correct-horse-8"
    r = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert r.status_code == 201, r.text
    link = next(l for (to, l) in app.state.email_sender.sent if to == email)
    token = parse_qs(urlparse(link).query)["token"][0]
    v = await client.get("/api/v1/auth/verify", params={"token": token})
    assert v.status_code == 200
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert login.status_code == 200
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"


async def _seed_workflow_with_diff_pair(
    client: AsyncClient,
) -> UUID:
    """Create a workflow with an ai_draft seed + a user_edit on top.

    Returns the workflow_id. PR-B's save hook wires parent_revision_id
    automatically, so the personalization service can resolve the pair.
    """
    create = await client.post(
        "/api/v1/workflows",
        json={
            "name": "wf-diffable",
            "settings": {},
            "graph": _graph("a"),
            "revision_source": "ai_draft",
        },
    )
    assert create.status_code == 201, create.text
    wf_id = create.json()["id"]

    # Edit the graph so the diff isn't empty (a real agent would no-op
    # on identical payloads — we want to make sure our test doesn't
    # accidentally rely on that path).
    edit = await client.put(
        f"/api/v1/workflows/{wf_id}",
        json={
            "name": "wf-diffable",
            "settings": {},
            "graph": _graph("b"),
            "revision_source": "user_edit",
        },
    )
    assert edit.status_code == 200, edit.text
    return UUID(wf_id)


@pytest_asyncio.fixture
async def pz_client_factory():
    """Build (app, client) and inject a fake AI into the service."""

    async def _build(*, fake_ai: FakePersonalizationAI):
        settings = _make_settings()
        app = create_app(settings, email_sender=NoopEmailSender())
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://testserver")

        async def _override():
            return PersonalizationService(
                ai_agent=fake_ai,  # type: ignore[arg-type]
                workflow_repo=app.state.workflow_repo,
                revision_repo=app.state.workflow_revision_repo,
                skill_repo=app.state.skill_repo,
                review_repo=app.state.personal_skill_review_repo,
            )

        app.dependency_overrides[get_personalization_service] = _override
        return app, client

    return _build


# --- extract_from_diff ------------------------------------------------


async def test_extract_accept_persists_candidate(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_accept_outcome(
            hint="Always add Slack notify after credentials",
            suggestion_hash="hash-accept-1",
        ),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="acc@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)

        r = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["candidate_id"] is not None
        assert body["drop_reason"] == ""
        assert body["suggestion_hash"] == "hash-accept-1"
        assert body["langsmith_run_id"] == "ls-run-1"

        # Candidate visible in the list with the right shape.
        listing = await client.get("/api/v1/personalization/candidates")
        assert listing.status_code == 200
        cands = listing.json()["candidates"]
        assert len(cands) == 1
        assert cands[0]["id"] == body["candidate_id"]
        assert cands[0]["hint"] == "Always add Slack notify after credentials"
        assert cands[0]["status"] == "pending_review"
        assert cands[0]["suggestion_hash"] == "hash-accept-1"

        await _truncate(app)


async def test_extract_judge_reject_records_review_row(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_judge_reject_outcome(
            suggestion_hash="hash-reject-1", reason="too specific"
        ),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="rej@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)

        r = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["candidate_id"] is None
        assert body["drop_reason"] == "judge_reject"
        # No skill row.
        listing = await client.get("/api/v1/personalization/candidates")
        assert listing.json()["candidates"] == []

        # A second call with the same hash should see the reject record
        # listed in `rejected_hashes` (suppression contract — see service
        # docstring). We don't assert what the agent does with it; just
        # that API_Server forwarded it.
        r2 = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r2.status_code == 200
        assert "hash-reject-1" in fake.last_call["rejected_hashes"]

        await _truncate(app)


async def test_extract_empty_drop_writes_nothing(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_empty_drop_outcome("empty_proposal")
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="emp@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)

        r = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r.status_code == 200
        assert r.json()["drop_reason"] == "empty_proposal"
        # No candidate, no review row — confirm the list is empty AND
        # the hash isn't fed back on a follow-up extract (because no
        # hash was assigned).
        listing = await client.get("/api/v1/personalization/candidates")
        assert listing.json()["candidates"] == []

        # Second call — rejected_hashes should still be empty.
        r2 = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r2.status_code == 200
        assert fake.last_call["rejected_hashes"] == []

        await _truncate(app)


async def test_extract_suppression_merges_active_and_rejected_hashes(
    pz_client_factory,
):
    # Two calls in sequence — first one accepts (lands a skill row with
    # hash X), second one is independent (hash Y). The second call's
    # rejected_hashes should include X because the active skill row is
    # part of the dedup signal.
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="first", suggestion_hash="hash-X"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="sup@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)

        # First call accepts; hash-X lands as a personal skill row.
        r1 = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r1.status_code == 200
        assert r1.json()["candidate_id"] is not None

        # Second call — agent returns judge_reject for a different hash.
        # API_Server still has to forward hash-X in the suppression list.
        fake.response = _judge_reject_outcome(
            suggestion_hash="hash-Y", reason="nope"
        )
        r2 = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r2.status_code == 200
        forwarded = fake.last_call["rejected_hashes"]
        assert "hash-X" in forwarded

        # Third call — both hash-X (from skills) and hash-Y (from
        # reviews) should be in the suppression list.
        fake.response = _empty_drop_outcome("empty_proposal")
        r3 = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r3.status_code == 200
        forwarded = fake.last_call["rejected_hashes"]
        assert {"hash-X", "hash-Y"} <= set(forwarded)

        await _truncate(app)


async def test_extract_422_when_no_user_edit_revision(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="h"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="nodiff@example.com")
        # Workflow with only an ai_draft seed — no user_edit, no diff.
        create = await client.post(
            "/api/v1/workflows",
            json={
                "name": "seed-only",
                "settings": {},
                "graph": _graph("a"),
                "revision_source": "ai_draft",
            },
        )
        wf_id = create.json()["id"]

        r = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": wf_id},
        )
        assert r.status_code == 422
        # The fake AI should never have been called — short-circuit
        # before the agent.
        assert fake.last_call is None

        await _truncate(app)


async def test_extract_404_when_workflow_owned_by_someone_else(
    pz_client_factory,
):
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="h"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="alice@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)
        # Log out alice, log in bob.
        client.headers.pop("Authorization", None)
        await _register_and_login(client, app, email="bob@example.com")

        r = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r.status_code == 404
        assert fake.last_call is None

        await _truncate(app)


async def test_extract_502_on_upstream_failure(pz_client_factory):
    fake = FakePersonalizationAI(raise_status=503)
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="up@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)

        r = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert r.status_code == 502
        await _truncate(app)


async def test_extract_requires_auth(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="h"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        r = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert r.status_code == 401
        await _truncate(app)


# --- activate / reject -----------------------------------------------


async def test_activate_pending_candidate(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="hash-1"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="act@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)

        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]

        act = await client.post(
            f"/api/v1/personalization/candidates/{cid}/activate"
        )
        assert act.status_code == 200, act.text
        assert act.json()["status"] == "active"

        # No longer in the pending list.
        assert (
            await client.get("/api/v1/personalization/candidates")
        ).json()["candidates"] == []

        await _truncate(app)


async def test_activate_propagates_to_ai_agent_memory(pz_client_factory):
    """PR-I — activate must call AI_Agent's upsert_personal_memory so
    the next reflective extract for the same user finds the row in the
    in-memory pool. We assert the wire shape (user_id is a string,
    skill carries condition/action/hash/source) without leaking ORM
    types across the boundary."""
    fake = FakePersonalizationAI(
        response=_accept_outcome(
            hint="Always add Slack notify after credentials",
            suggestion_hash="hash-sync-1",
        ),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="sync@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)

        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]

        act = await client.post(
            f"/api/v1/personalization/candidates/{cid}/activate"
        )
        assert act.status_code == 200, act.text

        assert len(fake.upsert_calls) == 1
        call = fake.upsert_calls[0]
        # Stable string for the wire (UUID 객체 그대로 직렬화하면 JSON 호환성 깨짐).
        UUID(call["user_id"])  # validates the shape rather than the value
        skill = call["skill"]
        assert skill["id"] == cid
        assert skill["suggestion_hash"] == "hash-sync-1"
        assert skill["source"] == "hitl_edit"
        assert skill["active"] is True
        assert skill["condition"]["text"] == (
            "Always add Slack notify after credentials"
        )

        await _truncate(app)


async def test_activate_succeeds_when_memory_sync_fails(pz_client_factory):
    """The DB transition is the source of truth — a transient Modal
    failure on the upsert must NOT roll back the activate or surface a
    5xx. Operators see the failure in the warning log; the next
    activate / extract retries the sync."""
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="hash-sync-fail"),
        upsert_raise_status=503,
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="syncfail@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)

        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]

        act = await client.post(
            f"/api/v1/personalization/candidates/{cid}/activate"
        )
        assert act.status_code == 200, act.text
        assert act.json()["status"] == "active"
        # Sync was attempted exactly once — no retry storm.
        assert len(fake.upsert_calls) == 1

        await _truncate(app)


async def test_activate_404_for_other_user(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="hash-1"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="alice@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)
        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]

        client.headers.pop("Authorization", None)
        await _register_and_login(client, app, email="bob@example.com")

        act = await client.post(
            f"/api/v1/personalization/candidates/{cid}/activate"
        )
        assert act.status_code == 404
        await _truncate(app)


async def test_activate_conflict_when_not_pending(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="hash-1"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="conf@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)
        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]

        # First activate → ok.
        await client.post(
            f"/api/v1/personalization/candidates/{cid}/activate"
        )
        # Second activate → 409 (already active, not pending).
        again = await client.post(
            f"/api/v1/personalization/candidates/{cid}/activate"
        )
        assert again.status_code == 409
        await _truncate(app)


async def test_reject_archives_and_records_hash(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="hash-rej-flow"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="rejflow@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)
        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]

        rej = await client.post(
            f"/api/v1/personalization/candidates/{cid}/reject",
            json={"reason": "not generalizable"},
        )
        assert rej.status_code == 200
        assert rej.json()["status"] == "archived"

        # The hash should now flow into the suppression list on the next
        # extract — both via the (archived) skill row AND the new
        # review row. Either path is enough for suppression; we just
        # confirm the hash is present.
        fake.response = _empty_drop_outcome("empty_proposal")
        await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        assert "hash-rej-flow" in fake.last_call["rejected_hashes"]

        await _truncate(app)


# --- isolation ---------------------------------------------------------


async def test_candidates_isolated_per_user(pz_client_factory):
    """Alice's pending candidates are invisible to bob (route-level
    guard — complements PR-F's AI_Agent-side cross-user memory guard).
    """

    fake = FakePersonalizationAI(
        response=_accept_outcome(
            hint="alice-only", suggestion_hash="hash-alice"
        ),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="alice@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)
        await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        alice_list = await client.get("/api/v1/personalization/candidates")
        assert len(alice_list.json()["candidates"]) == 1

        # Switch to bob — empty list, regardless of alice's row.
        client.headers.pop("Authorization", None)
        await _register_and_login(client, app, email="bob@example.com")
        bob_list = await client.get("/api/v1/personalization/candidates")
        assert bob_list.json()["candidates"] == []

        await _truncate(app)


async def test_workspace_skills_not_listed_as_candidates(pz_client_factory):
    """`GET /candidates` must filter to scope='user'. A workspace skill
    created via the wizard path must not surface here.
    """
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="h"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="ws@example.com")

        # Inject a workspace skill directly via the repo.
        sm = app.state.sessionmaker
        from auto_workflow_database.models.core import User as UserORM
        from sqlalchemy import select
        async with sm() as s:
            user_row = (
                await s.execute(select(UserORM).where(UserORM.email == "ws@example.com"))
            ).scalar_one()
        await app.state.skill_repo.create(
            owner_user_id=user_row.id,
            name="workspace-only",
            condition={"text": "c"},
            action={"text": "a"},
            scope="workspace",
            status="pending_review",
        )

        listing = await client.get("/api/v1/personalization/candidates")
        assert listing.status_code == 200
        assert listing.json()["candidates"] == []

        await _truncate(app)


# --- share (PR-J) -----------------------------------------------------


async def test_share_promotes_active_personal_skill_to_workspace(
    pz_client_factory,
):
    """PR-J — POST /candidates/{id}/share flips an active personal
    skill into the workspace pool. The DB scope changes; the AI_Agent
    memory file is best-effort deactivated. Response has status=active
    (the share doesn't change status, only scope) and the row no
    longer appears in the active personal listing."""
    fake = FakePersonalizationAI(
        response=_accept_outcome(
            hint="post slack on invoice",
            suggestion_hash="h-share-1",
        ),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="share@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)

        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]
        await client.post(
            f"/api/v1/personalization/candidates/{cid}/activate"
        )

        active_pre = await client.get(
            "/api/v1/personalization/candidates?status=active"
        )
        assert active_pre.status_code == 200
        assert len(active_pre.json()["candidates"]) == 1

        share = await client.post(
            f"/api/v1/personalization/candidates/{cid}/share"
        )
        assert share.status_code == 200, share.text
        assert share.json()["status"] == "active"

        active_post = await client.get(
            "/api/v1/personalization/candidates?status=active"
        )
        assert active_post.status_code == 200
        assert active_post.json()["candidates"] == []

        deactivate_calls = [
            c
            for c in fake.upsert_calls
            if c["skill"]["id"] == cid and c["skill"]["active"] is False
        ]
        assert len(deactivate_calls) == 1

        await _truncate(app)


async def test_share_409_when_candidate_not_active(pz_client_factory):
    """Pending or archived candidates can't be shared — only active."""
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="h-pending"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="share-409@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)
        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]

        share = await client.post(
            f"/api/v1/personalization/candidates/{cid}/share"
        )
        assert share.status_code == 409, share.text

        await _truncate(app)


async def test_share_404_for_other_user(pz_client_factory):
    """alice's candidate can't be shared by bob — auth check at the
    repo layer surfaces as 404 (privacy: don't even confirm the row
    exists for unrelated users)."""
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="h-share-404"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="alice-share@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)
        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]
        await client.post(
            f"/api/v1/personalization/candidates/{cid}/activate"
        )

        client.headers.pop("Authorization", None)
        await _register_and_login(client, app, email="bob-share@example.com")

        share = await client.post(
            f"/api/v1/personalization/candidates/{cid}/share"
        )
        assert share.status_code == 404
        await _truncate(app)


async def test_list_candidates_status_active_filter(pz_client_factory):
    """The new ?status=active query parameter returns active personal
    skills (used by the Frontend share lane)."""
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="h-active-list"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="active-list@example.com")
        wf_id = await _seed_workflow_with_diff_pair(client)
        ex = await client.post(
            "/api/v1/personalization/extract_from_diff",
            json={"workflow_id": str(wf_id)},
        )
        cid = ex.json()["candidate_id"]

        pending = await client.get(
            "/api/v1/personalization/candidates?status=pending_review"
        )
        active_empty = await client.get(
            "/api/v1/personalization/candidates?status=active"
        )
        assert len(pending.json()["candidates"]) == 1
        assert active_empty.json()["candidates"] == []

        await client.post(
            f"/api/v1/personalization/candidates/{cid}/activate"
        )

        pending_post = await client.get(
            "/api/v1/personalization/candidates?status=pending_review"
        )
        active_post = await client.get(
            "/api/v1/personalization/candidates?status=active"
        )
        assert pending_post.json()["candidates"] == []
        assert len(active_post.json()["candidates"]) == 1

        await _truncate(app)


async def test_list_candidates_rejects_unsupported_status(pz_client_factory):
    fake = FakePersonalizationAI(
        response=_accept_outcome(hint="x", suggestion_hash="h"),
    )
    app, client = await pz_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="bad-status@example.com")

        r = await client.get(
            "/api/v1/personalization/candidates?status=archived"
        )
        assert r.status_code == 400

        await _truncate(app)
