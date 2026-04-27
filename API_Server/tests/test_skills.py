"""Skill-bootstrap endpoint tests (PLAN_12 W2-7).

The skills router orchestrates AI_Agent (LLM) + SkillRepository (DB). We
mock the AI_Agent client at the dependency-override boundary so each test
controls exactly what AI_Agent appears to return. The SkillRepository
hits the real Postgres via the same conftest as the rest of API_Server's
suite — we want to catch ORM/JSONB/server-default regressions, which are
the same class of bugs the live-validation step on PR #134 caught.
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from urllib.parse import parse_qs, urlparse

from app.main import create_app
from app.models.skills import (
    DomainClassificationResponse,
    PolicyGapBody,
    SkillDraftBody,
    WizardQuestionBody,
)
from app.routers.skills import get_skill_bootstrap_service
from app.services.email_sender import NoopEmailSender
from app.services.skill_bootstrap_service import SkillBootstrapService
from tests.conftest import DATABASE_URL, _make_settings

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set — skills tests need Postgres"
)


# --- fake AI_Agent client -------------------------------------------------


class FakeAIAgent:
    """Duck-types AIAgentHTTPBackend.{classify_domain, analyze_gaps, answer_to_skill}.

    Each test instantiates one with the canned responses it expects to see.
    Setting any of `*_error` triggers an httpx.HTTPStatusError on that
    method to exercise the upstream-failure 502 path.
    """

    def __init__(
        self,
        *,
        classify: DomainClassificationResponse | None = None,
        gaps: list[PolicyGapBody] | None = None,
        draft: SkillDraftBody | None = None,
        classify_error: int | None = None,
        gaps_error: int | None = None,
        answer_error: int | None = None,
    ) -> None:
        self._classify = classify
        self._gaps = gaps or []
        self._draft = draft
        self._classify_error = classify_error
        self._gaps_error = gaps_error
        self._answer_error = answer_error
        # Capture last call args for assertion.
        self.last_classify_text: str | None = None
        self.last_gap_args: tuple[str, list] | None = None
        self.last_answer_args: dict | None = None

    @staticmethod
    def _err(status: int) -> httpx.HTTPStatusError:
        req = httpx.Request("POST", "http://upstream/")
        resp = httpx.Response(status, request=req)
        return httpx.HTTPStatusError("upstream", request=req, response=resp)

    async def classify_domain(self, text: str) -> DomainClassificationResponse:
        self.last_classify_text = text
        if self._classify_error is not None:
            raise self._err(self._classify_error)
        assert self._classify is not None
        return self._classify

    async def analyze_gaps(self, domain, extracted_skills):
        self.last_gap_args = (domain, list(extracted_skills))
        if self._gaps_error is not None:
            raise self._err(self._gaps_error)
        return self._gaps

    async def answer_to_skill(self, *, domain, policy_id, question, answer):
        self.last_answer_args = {
            "domain": domain,
            "policy_id": policy_id,
            "question": question,
            "answer": answer,
        }
        if self._answer_error is not None:
            raise self._err(self._answer_error)
        assert self._draft is not None
        return self._draft


# --- fixtures -------------------------------------------------------------


async def _truncate(app) -> None:
    sm = app.state.sessionmaker
    async with sm() as s, s.begin():
        # CASCADE wipes skills + skill_sources via FK to users.
        await s.execute(text("TRUNCATE users CASCADE"))


async def _register_and_login(client: AsyncClient, app, *, email: str = "skills@example.com") -> None:
    password = "correct-horse-8"
    r = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert r.status_code == 201, r.text
    sender = app.state.email_sender
    link = next(l for (to, l) in sender.sent if to == email)
    token = parse_qs(urlparse(link).query)["token"][0]
    v = await client.get("/api/v1/auth/verify", params={"token": token})
    assert v.status_code == 200
    login = await client.post(
        "/api/v1/auth/login", data={"username": email, "password": password}
    )
    assert login.status_code == 200
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"


@pytest_asyncio.fixture
async def skills_client_factory():
    async def _build(*, fake_ai: FakeAIAgent):
        settings = _make_settings()
        app = create_app(settings, email_sender=NoopEmailSender())
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://testserver")

        # Wait until lifespan starts so app.state.skill_repo exists, then
        # override the service with one that uses the fake AI client + the
        # real Postgres skill_repo.
        async def _override():
            return SkillBootstrapService(
                ai_agent=fake_ai,  # type: ignore[arg-type]
                skill_repo=app.state.skill_repo,
            )
        app.dependency_overrides[get_skill_bootstrap_service] = _override
        return app, client

    return _build


# --- /classify_domain -----------------------------------------------------


async def test_classify_domain_returns_ai_agent_response(skills_client_factory):
    fake = FakeAIAgent(
        classify=DomainClassificationResponse(
            domain="ecommerce", confidence=0.92, rationale="Online store."
        ),
    )
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/skills/classify_domain",
            json={"text": "We run a Shopify store selling skincare products."},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["domain"] == "ecommerce"
        assert body["confidence"] == 0.92
        assert fake.last_classify_text == "We run a Shopify store selling skincare products."

        await _truncate(app)


async def test_classify_domain_502_on_upstream_failure(skills_client_factory):
    fake = FakeAIAgent(classify_error=502)
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/skills/classify_domain", json={"text": "anything"}
        )
        assert r.status_code == 502
        await _truncate(app)


async def test_classify_domain_requires_auth(skills_client_factory):
    fake = FakeAIAgent(
        classify=DomainClassificationResponse(domain="other", confidence=0.5, rationale=""),
    )
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        # No login → 401
        r = await client.post(
            "/api/v1/skills/classify_domain", json={"text": "hi"}
        )
        assert r.status_code == 401
        await _truncate(app)


# --- /bootstrap -----------------------------------------------------------


async def test_bootstrap_round_trips_session_id(skills_client_factory):
    fake = FakeAIAgent(
        gaps=[
            PolicyGapBody(
                policy_id="ecommerce.refund_threshold",
                policy_name="Refund threshold escalation",
                questions=[
                    WizardQuestionBody(
                        text="What dollar amount triggers manager approval?",
                        parameter="REFUND_AUTO_APPROVE_LIMIT",
                    )
                ],
            )
        ],
    )
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        my_session = "11111111-1111-1111-1111-111111111111"
        r = await client.post(
            "/api/v1/skills/bootstrap",
            json={"domain": "ecommerce", "session_id": my_session, "extracted_skills": []},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_id"] == my_session
        assert body["domain"] == "ecommerce"
        assert body["missing"][0]["policy_id"] == "ecommerce.refund_threshold"
        assert body["missing"][0]["policy_name"] == "Refund threshold escalation"

        await _truncate(app)


async def test_bootstrap_mints_session_id_when_omitted(skills_client_factory):
    fake = FakeAIAgent(gaps=[])
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/skills/bootstrap",
            json={"domain": "other", "extracted_skills": []},
        )
        assert r.status_code == 200, r.text
        # Server-allocated UUID4
        assert len(r.json()["session_id"]) == 36

        await _truncate(app)


# --- /answer + DB write ---------------------------------------------------


async def test_answer_persists_pending_review_skill(skills_client_factory):
    fake = FakeAIAgent(
        draft=SkillDraftBody(
            name="Refund threshold escalation",
            description="Refunds above $500 require manager approval.",
            condition="Customer requests refund AND amount > $500",
            action="Forward to manager via #refunds Slack channel",
            rationale="Large refunds need human judgment.",
            needs_clarification=False,
        ),
    )
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        sid = "22222222-2222-2222-2222-222222222222"
        r = await client.post(
            "/api/v1/skills/answer",
            json={
                "session_id": sid,
                "domain": "ecommerce",
                "policy_id": "ecommerce.refund_threshold",
                "question": "What dollar amount triggers manager approval?",
                "answer": "$500. Goes to my co-founder Sarah on Slack.",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_id"] == sid
        assert body["draft"]["name"] == "Refund threshold escalation"
        skill_id = body["skill_id"]

        # Verify the skill landed in DB as pending_review with the right shape.
        list_resp = await client.get(
            "/api/v1/skills", params={"status": "pending_review"}
        )
        assert list_resp.status_code == 200
        skills = list_resp.json()["skills"]
        assert len(skills) == 1
        assert skills[0]["id"] == skill_id
        assert skills[0]["status"] == "pending_review"
        # Prose answers wrapped as {"text": ...} per W2-7 design.
        assert skills[0]["condition"] == {"text": "Customer requests refund AND amount > $500"}
        assert skills[0]["action"] == {"text": "Forward to manager via #refunds Slack channel"}

        # Fake recorded the right inputs.
        assert fake.last_answer_args["policy_id"] == "ecommerce.refund_threshold"

        await _truncate(app)


# --- approve / reject -----------------------------------------------------


async def _create_pending_skill(client, fake) -> str:
    sid = "33333333-3333-3333-3333-333333333333"
    r = await client.post(
        "/api/v1/skills/answer",
        json={
            "session_id": sid,
            "domain": "ecommerce",
            "policy_id": "ecommerce.refund_threshold",
            "question": "Q?",
            "answer": "A.",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["skill_id"]


async def test_approve_transitions_pending_to_active(skills_client_factory):
    fake = FakeAIAgent(
        draft=SkillDraftBody(name="X", condition="C", action="A", needs_clarification=False),
    )
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)
        sid = await _create_pending_skill(client, fake)

        r = await client.post(f"/api/v1/skills/{sid}/approve")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == sid
        assert body["status"] == "active"

        await _truncate(app)


async def test_reject_transitions_pending_to_rejected(skills_client_factory):
    fake = FakeAIAgent(
        draft=SkillDraftBody(name="X", condition="C", action="A", needs_clarification=False),
    )
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)
        sid = await _create_pending_skill(client, fake)

        r = await client.post(f"/api/v1/skills/{sid}/reject")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"

        await _truncate(app)


async def test_approve_rejects_already_active(skills_client_factory):
    fake = FakeAIAgent(
        draft=SkillDraftBody(name="X", condition="C", action="A", needs_clarification=False),
    )
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)
        sid = await _create_pending_skill(client, fake)

        # First approve transitions pending → active
        r1 = await client.post(f"/api/v1/skills/{sid}/approve")
        assert r1.status_code == 200
        # Second approve must 409 — already active
        r2 = await client.post(f"/api/v1/skills/{sid}/approve")
        assert r2.status_code == 409

        await _truncate(app)


async def test_approve_unknown_skill_returns_404(skills_client_factory):
    fake = FakeAIAgent()  # no calls expected
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/skills/99999999-9999-9999-9999-999999999999/approve"
        )
        assert r.status_code == 404

        await _truncate(app)


# --- list / get + owner isolation ----------------------------------------


async def test_list_filters_by_status(skills_client_factory):
    fake = FakeAIAgent(
        draft=SkillDraftBody(name="X", condition="C", action="A", needs_clarification=False),
    )
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        s1 = await _create_pending_skill(client, fake)
        s2 = await _create_pending_skill(client, fake)
        await client.post(f"/api/v1/skills/{s1}/approve")
        # s1 = active, s2 = pending_review

        pending = await client.get("/api/v1/skills", params={"status": "pending_review"})
        assert pending.status_code == 200
        assert {s["id"] for s in pending.json()["skills"]} == {s2}

        active = await client.get("/api/v1/skills", params={"status": "active"})
        assert active.status_code == 200
        assert {s["id"] for s in active.json()["skills"]} == {s1}

        all_skills = await client.get("/api/v1/skills")
        assert {s["id"] for s in all_skills.json()["skills"]} == {s1, s2}

        await _truncate(app)


async def test_endpoint_503_when_ai_agent_unconfigured():
    """Container leaves skill_bootstrap_service=None when ai_agent_base_url
    is unset. The router maps that to 503 instead of crashing on a None
    deref or hanging on an httpx connect."""
    settings = _make_settings()  # default leaves ai_agent_base_url=""
    app = create_app(settings, email_sender=NoopEmailSender())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/skills/classify_domain", json={"text": "anything"}
        )
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]

        await _truncate(app)


async def test_get_returns_404_for_other_users_skill(skills_client_factory):
    fake = FakeAIAgent(
        draft=SkillDraftBody(name="X", condition="C", action="A", needs_clarification=False),
    )
    app, client = await skills_client_factory(fake_ai=fake)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app, email="alice@example.com")
        alice_skill = await _create_pending_skill(client, fake)

        # Switch to a second user — Bob shouldn't see Alice's skill.
        del client.headers["Authorization"]
        await _register_and_login(client, app, email="bob@example.com")

        bob_get = await client.get(f"/api/v1/skills/{alice_skill}")
        assert bob_get.status_code == 404
        bob_list = await client.get("/api/v1/skills")
        assert bob_list.json()["skills"] == []

        await _truncate(app)
