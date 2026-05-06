"""Tests for policy_extract service + endpoint (PLAN_12 W3-4).

The service contract:
- empty / whitespace chunk → empty list (no LLM round-trip)
- chunk with no policy signal → empty list
- chunk with a policy → list of SkillDraft, each with non-empty
  name/condition/action
- vague chunk → at least one candidate with needs_clarification=true
- malformed LLM response → PolicyExtractParseError
- needs_clarification=true without hint → PolicyExtractParseError

The endpoint contract is exercised via ASGITransport in the same suite to
keep the wire-shape regression checks close to the service tests.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.backends.stub import StubLLMBackend
from app.main import create_app
from app.models.skills import SkillDraft
from app.services.policy_extract import (
    PolicyExtractParseError,
    extract_policies,
)


class _StaticBackend:
    """Returns a fixed JSON string regardless of input. Used to test the
    parser in isolation from any LLM behavior.
    """

    def __init__(self, response: str) -> None:
        self._response = response

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        return self._response

    async def stream(self, **_):  # noqa: ANN001, ANN003
        if False:
            yield ""

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


# --- service-level: empty + heuristic stub ---------------------------------


async def test_empty_chunk_short_circuits_without_llm_call() -> None:
    # Use a backend that would crash if called — proves the service skipped it.
    class _Boom:
        async def complete(self, **_):
            raise AssertionError("must not be called for empty chunk")

        async def stream(self, **_):
            if False:
                yield ""

        async def ready(self) -> bool:
            return True

        async def aclose(self) -> None:
            return None

    assert await extract_policies(_Boom(), "") == []
    assert await extract_policies(_Boom(), "   \n  ") == []


async def test_stub_policy_extract_emits_threshold_candidate() -> None:
    backend = StubLLMBackend()
    drafts = await extract_policies(
        backend,
        "Refunds over $500 require manager approval before processing.",
        domain="ecommerce",
    )
    assert len(drafts) >= 1
    # The stub's threshold path includes the dollar amount in the candidate.
    joined = " ".join(d.condition + " " + d.action for d in drafts)
    assert "$500" in joined


async def test_stub_returns_empty_for_irrelevant_chunk() -> None:
    backend = StubLLMBackend()
    drafts = await extract_policies(
        backend, "The team meets every Tuesday at 10am.", domain="other"
    )
    assert drafts == []


async def test_stub_marks_vague_chunk_needs_clarification() -> None:
    backend = StubLLMBackend()
    drafts = await extract_policies(
        backend,
        "Be careful with PII when handling customer requests.",
        domain="services",
    )
    assert len(drafts) == 1
    assert drafts[0].needs_clarification is True
    assert drafts[0].clarification_hint != ""


# --- service-level: parser robustness on hand-crafted backend responses ----


async def test_parser_accepts_well_formed_response() -> None:
    backend = _StaticBackend(
        '{"candidates": [{"name": "n", "condition": "c", "action": "a", '
        '"rationale": "r", "needs_clarification": false, "clarification_hint": ""}]}'
    )
    drafts = await extract_policies(backend, "any chunk")
    assert drafts == [
        SkillDraft(
            name="n",
            description="",
            condition="c",
            action="a",
            rationale="r",
            needs_clarification=False,
            clarification_hint="",
        )
    ]


async def test_parser_accepts_empty_candidates_list() -> None:
    backend = _StaticBackend('{"candidates": []}')
    drafts = await extract_policies(backend, "any chunk")
    assert drafts == []


async def test_parser_rejects_missing_candidates_key() -> None:
    backend = _StaticBackend('{"oops": []}')
    with pytest.raises(PolicyExtractParseError, match="candidates"):
        await extract_policies(backend, "any")


async def test_parser_rejects_non_list_candidates() -> None:
    backend = _StaticBackend('{"candidates": "not a list"}')
    with pytest.raises(PolicyExtractParseError, match="must be a list"):
        await extract_policies(backend, "any")


async def test_parser_rejects_candidate_missing_required_field() -> None:
    backend = _StaticBackend(
        '{"candidates": [{"name": "n", "condition": "c"}]}'  # action missing
    )
    with pytest.raises(PolicyExtractParseError, match="action"):
        await extract_policies(backend, "any")


async def test_parser_rejects_clarification_flag_without_hint() -> None:
    backend = _StaticBackend(
        '{"candidates": [{"name": "n", "condition": "c", "action": "a", '
        '"needs_clarification": true, "clarification_hint": ""}]}'
    )
    with pytest.raises(PolicyExtractParseError, match="hint"):
        await extract_policies(backend, "any")


async def test_parser_tolerates_json_fence() -> None:
    backend = _StaticBackend(
        '```json\n{"candidates": [{"name": "n", "condition": "c", '
        '"action": "a"}]}\n```'
    )
    drafts = await extract_policies(backend, "any")
    assert len(drafts) == 1
    assert drafts[0].name == "n"


# --- HTTP endpoint --------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_returns_candidates_from_stub() -> None:
    app = create_app(backend_override=StubLLMBackend())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract",
            json={
                "chunk": "Refunds over $500 require manager approval.",
                "domain": "ecommerce",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "candidates" in body
    assert len(body["candidates"]) >= 1
    first = body["candidates"][0]
    for required in ("name", "condition", "action"):
        assert first[required], f"missing/empty {required}"


@pytest.mark.asyncio
async def test_endpoint_validates_chunk_required() -> None:
    app = create_app(backend_override=StubLLMBackend())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Empty chunk fails Pydantic validation (min_length=1) before the
        # service ever runs.
        resp = await c.post(
            "/v1/policy/extract",
            json={"chunk": "", "domain": "other"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_endpoint_returns_502_on_parse_error() -> None:
    app = create_app(backend_override=_StaticBackend("not json at all"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract",
            json={"chunk": "anything", "domain": "other"},
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_endpoint_gated_by_bearer(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-x")
    app = create_app(backend_override=StubLLMBackend())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract",
            json={"chunk": "Refunds over $500 require approval.", "domain": "ecommerce"},
        )
    # No auth header → 401 (matches the convention from other /v1/* tests).
    assert resp.status_code == 401
