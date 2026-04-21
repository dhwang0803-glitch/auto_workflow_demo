"""POST /api/v1/ai/compose — JSON-once + SSE streaming (PLAN_02 PR A & B).

The Anthropic SDK is mocked via a `FakeBackend` injected through
`create_app(ai_composer_backend=...)`. No network I/O.

Streaming tests feed canned chunks through `FakeBackend.stream()` and parse
the SSE frames the router emits.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.services.ai_composer_service import LLMBackend
from app.services.email_sender import NoopEmailSender


# Reuse the fixtures' DATABASE_URL skip — composer still needs the auth
# stack which sits on Postgres.
from tests.conftest import DATABASE_URL  # noqa: E402

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set — composer test needs Postgres"
)


# --------------------------------------------------------------- fake LLM


class FakeBackend:
    """Records the most recent prompt and replays a canned response. Each
    test instantiates one and asserts on `last_system` / `last_user` to
    catch prompt regressions.

    For streaming tests, pass `stream_chunks=[...]` to replay a preset chunk
    sequence through `stream()`.
    """

    def __init__(
        self,
        response: str = "",
        *,
        stream_chunks: list[str] | None = None,
    ) -> None:
        self._response = response
        self._stream_chunks = stream_chunks or []
        self.last_system: str | None = None
        self.last_user: str | None = None
        self.calls = 0
        self.stream_calls = 0

    async def complete(self, *, system: str, user_message: str, max_tokens: int) -> str:
        self.last_system = system
        self.last_user = user_message
        self.calls += 1
        return self._response

    async def stream(
        self, *, system: str, user_message: str, max_tokens: int
    ) -> AsyncIterator[str]:
        self.last_system = system
        self.last_user = user_message
        self.stream_calls += 1
        for chunk in self._stream_chunks:
            yield chunk


def _wrap_json(payload: dict) -> str:
    """Match the prompt's required ```json fenced block."""
    return f"```json\n{json.dumps(payload)}\n```"


# --------------------------------------------------------------- fixtures


def _make_settings(**overrides) -> Settings:
    base = dict(
        database_url=DATABASE_URL or "",
        jwt_secret="test-secret",
        jwt_algorithm="HS256",
        jwt_access_ttl_minutes=60,
        jwt_verify_email_ttl_hours=24,
        email_sender="console",
        app_base_url="http://testserver",
        bcrypt_cost=4,
        credential_master_key=Fernet.generate_key().decode("utf-8"),
        # FakeBackend is injected so the key value is irrelevant — but it
        # must be non-empty if we wanted the AnthropicBackend path; here we
        # bypass that by passing the backend explicitly.
        anthropic_api_key="",
        ai_compose_rate_per_minute=10,
    )
    base.update(overrides)
    return Settings(**base)


@pytest_asyncio.fixture
async def composer_client_factory():
    """Returns (backend, async_client_cm) — caller picks the canned response.

    A factory (not a fixture returning a tuple) keeps each test in control
    of the backend's response, while still sharing the auth boilerplate.
    """

    async def _build(
        response_text: str = "",
        *,
        stream_chunks: list[str] | None = None,
        **settings_overrides,
    ):
        backend = FakeBackend(response_text, stream_chunks=stream_chunks)
        settings = _make_settings(**settings_overrides)
        app = create_app(
            settings,
            email_sender=NoopEmailSender(),
            ai_composer_backend=backend,
        )

        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://testserver")
        return backend, app, client

    return _build


async def _truncate(app) -> None:
    sm = app.state.sessionmaker
    async with sm() as s, s.begin():
        await s.execute(text("TRUNCATE users CASCADE"))


async def _register_and_login(client: AsyncClient, app) -> None:
    from urllib.parse import parse_qs, urlparse

    email = "composer@example.com"
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


# --------------------------------------------------------------- tests


async def test_clarify_intent_passthrough(composer_client_factory):
    canned = _wrap_json(
        {
            "intent": "clarify",
            "clarify_questions": ["Where is the data source?", "Who are the recipients?"],
            "proposed_dag": None,
            "diff": None,
            "rationale": "Need more info before drafting.",
        }
    )
    backend, app, client = await composer_client_factory(canned)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/ai/compose",
            json={"message": "Send a report to the team."},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["result"]["intent"] == "clarify"
        assert body["result"]["clarify_questions"] == [
            "Where is the data source?",
            "Who are the recipients?",
        ]
        assert body["result"]["proposed_dag"] is None
        assert body["session_id"]  # server allocates when client omits

        # Catalog must be present in the system prompt (catches accidental
        # regressions where the catalog provider stops being called).
        assert "<node_catalog>" in (backend.last_system or "")

        await _truncate(app)


async def test_draft_intent_round_trip(composer_client_factory):
    proposed = {
        "nodes": [
            {"id": "fetch", "type": "http_request", "config": {"url": "https://x"}},
        ],
        "edges": [],
    }
    canned = _wrap_json(
        {
            "intent": "draft",
            "clarify_questions": None,
            "proposed_dag": proposed,
            "diff": None,
            "rationale": "Single HTTP fetch matches the request.",
        }
    )
    backend, app, client = await composer_client_factory(canned)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/ai/compose",
            json={"message": "Fetch the URL https://x and stop."},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["result"]["intent"] == "draft"
        assert body["result"]["proposed_dag"]["nodes"][0]["id"] == "fetch"

        await _truncate(app)


async def test_refine_intent_with_current_dag(composer_client_factory):
    current = {
        "nodes": [{"id": "a", "type": "http_request", "config": {}}],
        "edges": [],
    }
    proposed = {
        "nodes": [
            {"id": "a", "type": "http_request", "config": {"url": "https://new"}},
        ],
        "edges": [],
    }
    canned = _wrap_json(
        {
            "intent": "refine",
            "clarify_questions": None,
            "proposed_dag": proposed,
            "diff": {
                "added_nodes": [],
                "removed_node_ids": [],
                "modified_nodes": [{"id": "a", "config": {"url": "https://new"}}],
            },
            "rationale": "Updated the URL.",
        }
    )
    backend, app, client = await composer_client_factory(canned)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/ai/compose",
            json={
                "message": "Change the URL to https://new",
                "current_dag": current,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["result"]["intent"] == "refine"
        assert body["result"]["diff"]["modified_nodes"][0]["id"] == "a"
        # The user prompt must contain the current_dag the client sent —
        # otherwise the LLM has no basis to compute a diff.
        assert '"id": "a"' in (backend.last_user or "") or '"id":"a"' in (
            backend.last_user or ""
        )

        await _truncate(app)


async def test_invalid_json_from_llm_returns_502(composer_client_factory):
    backend, app, client = await composer_client_factory("not json at all")
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/ai/compose", json={"message": "anything"}
        )
        assert r.status_code == 502, r.text

        await _truncate(app)


async def test_schema_mismatch_returns_502(composer_client_factory):
    # `intent` outside the literal triggers ValidationError.
    bogus = _wrap_json({"intent": "unknown", "rationale": "oops"})
    backend, app, client = await composer_client_factory(bogus)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/ai/compose", json={"message": "anything"}
        )
        assert r.status_code == 502, r.text

        await _truncate(app)


async def test_rate_limit_429(composer_client_factory):
    canned = _wrap_json({"intent": "clarify", "rationale": "x"})
    backend, app, client = await composer_client_factory(
        canned, ai_compose_rate_per_minute=2
    )
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        # First two succeed, third trips the limiter.
        for _ in range(2):
            ok = await client.post(
                "/api/v1/ai/compose", json={"message": "go"}
            )
            assert ok.status_code == 200, ok.text

        blocked = await client.post(
            "/api/v1/ai/compose", json={"message": "go"}
        )
        assert blocked.status_code == 429
        assert blocked.headers.get("Retry-After") == "60"

        await _truncate(app)


async def test_disabled_when_no_api_key(composer_client_factory):
    """The container builds AIComposerService with backend=None when the
    Anthropic key is empty AND no test backend is injected. The router
    surfaces this as 503 instead of crashing in the SDK."""
    settings = _make_settings(anthropic_api_key="")
    app = create_app(
        settings, email_sender=NoopEmailSender(), ai_composer_backend=None
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/ai/compose", json={"message": "anything"}
        )
        assert r.status_code == 503, r.text

        await _truncate(app)


async def test_unauthenticated_returns_401(composer_client_factory):
    canned = _wrap_json({"intent": "clarify", "rationale": "x"})
    backend, app, client = await composer_client_factory(canned)
    async with client, app.router.lifespan_context(app):
        await _truncate(app)

        r = await client.post(
            "/api/v1/ai/compose", json={"message": "go"}
        )
        assert r.status_code == 401

        await _truncate(app)


# --------------------------------------------------------- streaming (PR B)


def _parse_sse(body: str) -> list[dict]:
    """Parse a text/event-stream body into a list of {event, data} dicts."""
    out: list[dict] = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        event = None
        data_lines: list[str] = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if event is not None:
            out.append(
                {"event": event, "data": json.loads("\n".join(data_lines))}
            )
    return out


async def test_stream_emits_rationale_deltas_then_result(composer_client_factory):
    payload = {
        "intent": "draft",
        "clarify_questions": None,
        "proposed_dag": {
            "nodes": [
                {"id": "fetch", "type": "http_request", "config": {"url": "https://x"}}
            ],
            "edges": [],
        },
        "diff": None,
        "rationale": "Fetch then done.",
    }
    # Simulate the model narrating inside <rationale>...</rationale>, then
    # emitting the JSON fence. Chunks split across tag boundaries to prove
    # the parser holds back partial-tag tails.
    chunks = [
        "<ration",
        "ale>Fetching ",
        "the URL.",
        "</rational",
        "e>\n```json\n",
        json.dumps(payload),
        "\n```",
    ]
    backend, app, client = await composer_client_factory(
        "", stream_chunks=chunks
    )
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/ai/compose?stream=true", json={"message": "fetch it"}
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(r.text)
        kinds = [e["event"] for e in events]
        assert kinds[0] == "session"  # correlation frame first
        assert "rationale_delta" in kinds
        assert kinds[-1] == "result"

        # Rationale tokens concatenated reconstruct the full rationale text
        # — no tag fragments leaked.
        narrated = "".join(
            e["data"]["token"] for e in events if e["event"] == "rationale_delta"
        )
        assert "Fetching the URL." in narrated
        assert "<rationale" not in narrated
        assert "</rationale" not in narrated

        final = next(e["data"] for e in events if e["event"] == "result")
        assert final["result"]["intent"] == "draft"
        assert final["result"]["proposed_dag"]["nodes"][0]["id"] == "fetch"
        assert final["session_id"]

        await _truncate(app)


async def test_stream_invalid_json_emits_error_event(composer_client_factory):
    chunks = [
        "<rationale>oops</rationale>\n",
        "not a json fence at all",
    ]
    backend, app, client = await composer_client_factory(
        "", stream_chunks=chunks
    )
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/ai/compose?stream=true", json={"message": "go"}
        )
        # Stream opens 200 because headers flush before any failure. The
        # error is reported in-band.
        assert r.status_code == 200

        events = _parse_sse(r.text)
        errs = [e for e in events if e["event"] == "error"]
        assert len(errs) == 1
        assert errs[0]["data"]["code"] == "invalid_response"
        # No `result` frame should follow an `error`.
        assert not any(e["event"] == "result" for e in events)

        await _truncate(app)


async def test_stream_rate_limit_emits_error_event(composer_client_factory):
    payload = {"intent": "clarify", "rationale": "x"}
    chunks = [
        "<rationale>x</rationale>\n```json\n",
        json.dumps(payload),
        "\n```",
    ]
    backend, app, client = await composer_client_factory(
        "", stream_chunks=chunks, ai_compose_rate_per_minute=1
    )
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        # First request consumes the budget.
        first = await client.post(
            "/api/v1/ai/compose?stream=true", json={"message": "go"}
        )
        assert first.status_code == 200
        assert any(
            e["event"] == "result" for e in _parse_sse(first.text)
        )

        # Second trips the limiter — in-band error, still HTTP 200.
        blocked = await client.post(
            "/api/v1/ai/compose?stream=true", json={"message": "go"}
        )
        assert blocked.status_code == 200
        events = _parse_sse(blocked.text)
        errs = [e for e in events if e["event"] == "error"]
        assert len(errs) == 1
        assert errs[0]["data"]["code"] == "rate_limit"

        await _truncate(app)


async def test_stream_uses_streaming_prompt_variant(composer_client_factory):
    """The streaming path must instruct the model to narrate inside
    <rationale>...</rationale>. Regression guard: if someone refactors
    `_build_system_prompt`, this catches dropping the streaming branch."""
    payload = {"intent": "clarify", "rationale": "x"}
    chunks = [
        "<rationale>x</rationale>\n```json\n",
        json.dumps(payload),
        "\n```",
    ]
    backend, app, client = await composer_client_factory(
        "", stream_chunks=chunks
    )
    async with client, app.router.lifespan_context(app):
        await _truncate(app)
        await _register_and_login(client, app)

        r = await client.post(
            "/api/v1/ai/compose?stream=true", json={"message": "go"}
        )
        assert r.status_code == 200
        assert "<rationale>" in (backend.last_system or "")
        assert "streaming mode" in (backend.last_system or "")

        await _truncate(app)
