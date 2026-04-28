"""Tests for gap_analyze + answer_to_skill (PLAN_12 W2-4)."""
from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.models.skills import ExtractedSkill
from app.services.skill_bootstrap import (
    SkillBootstrapParseError,
    _seed_policies,
    analyze_gaps,
    answer_to_skill,
)


class _ScriptedBackend:
    """LLMBackend duck-type returning a fixed string."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.last_system: str | None = None
        self.last_user: str | None = None
        self.last_max_tokens: int | None = None

    async def complete(self, *, system: str, user_message: str, max_tokens: int) -> str:
        self.last_system = system
        self.last_user = user_message
        self.last_max_tokens = max_tokens
        return self._response

    async def stream(
        self, *, system: str, user_message: str, max_tokens: int
    ) -> AsyncIterator[str]:
        yield self._response  # not used here

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _ecommerce_ids() -> list[str]:
    return [p["id"] for p in _seed_policies("ecommerce")]


# --- analyze_gaps service -------------------------------------------------


@pytest.mark.asyncio
async def test_gap_analyze_other_domain_short_circuits() -> None:
    # No LLM call should fire — the backend response is unreachable.
    backend = _ScriptedBackend("UNREACHABLE")
    result = await analyze_gaps(backend, "other", [])
    assert result.missing == []
    assert backend.last_system is None  # confirmed not called


@pytest.mark.asyncio
async def test_gap_analyze_enriches_with_seed_policy_name() -> None:
    target = _ecommerce_ids()[0]
    response = json.dumps(
        {
            "missing": [
                {
                    "policy_id": target,
                    "questions": [
                        {"text": "What dollar amount?", "parameter": None}
                    ],
                }
            ]
        }
    )
    backend = _ScriptedBackend(response)
    result = await analyze_gaps(backend, "ecommerce", [])
    assert len(result.missing) == 1
    assert result.missing[0].policy_id == target
    # Service looked up the seed and filled in the human-readable name.
    seed = next(p for p in _seed_policies("ecommerce") if p["id"] == target)
    assert result.missing[0].policy_name == seed["name"]


@pytest.mark.asyncio
async def test_gap_analyze_unknown_policy_id_rejected() -> None:
    backend = _ScriptedBackend(
        json.dumps(
            {
                "missing": [
                    {"policy_id": "ecommerce.fabricated_id", "questions": []}
                ]
            }
        )
    )
    with pytest.raises(SkillBootstrapParseError, match="fabricated_id"):
        await analyze_gaps(backend, "ecommerce", [])


@pytest.mark.asyncio
async def test_gap_analyze_drops_phantom_parameter() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps(
            {
                "missing": [
                    {
                        "policy_id": target,
                        "questions": [
                            {
                                "text": "What threshold?",
                                "parameter": "TOTALLY_FAKE_PARAM",
                            }
                        ],
                    }
                ]
            }
        )
    )
    result = await analyze_gaps(backend, "ecommerce", [])
    # Phantom parameter null'd out so consumers don't act on it.
    assert result.missing[0].questions[0].parameter is None


@pytest.mark.asyncio
async def test_gap_analyze_passes_extracted_skills_in_user_message() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps({"missing": [{"policy_id": target, "questions": []}]})
    )
    extracted = [
        ExtractedSkill(
            name="Refund cap",
            condition="Customer asks for refund > $1000",
            action="Forward to founder via email",
        )
    ]
    await analyze_gaps(backend, "ecommerce", extracted)
    assert backend.last_user is not None
    user_payload = json.loads(backend.last_user)
    assert user_payload[0]["name"] == "Refund cap"


@pytest.mark.asyncio
async def test_gap_analyze_invalid_json_raises() -> None:
    backend = _ScriptedBackend("I think there are some gaps but...")
    with pytest.raises(SkillBootstrapParseError):
        await analyze_gaps(backend, "ecommerce", [])


@pytest.mark.asyncio
async def test_gap_analyze_missing_field_raises() -> None:
    backend = _ScriptedBackend(json.dumps({"not_missing": []}))
    with pytest.raises(SkillBootstrapParseError, match="missing"):
        await analyze_gaps(backend, "ecommerce", [])


# --- answer_to_skill service ---------------------------------------------


@pytest.mark.asyncio
async def test_answer_to_skill_happy_path() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps(
            {
                "name": "Refund threshold escalation",
                "description": "Refunds above $500 require manager approval.",
                "condition": "Customer requests refund AND amount > $500",
                "action": "Forward to manager via #refunds Slack channel",
                "rationale": "Large refunds need human judgment.",
                "needs_clarification": False,
                "clarification_hint": "",
            }
        )
    )
    draft = await answer_to_skill(
        backend,
        "ecommerce",
        target,
        "What dollar amount triggers manager approval for refunds?",
        "$500. Goes to my co-founder Sarah on Slack.",
    )
    assert draft.name == "Refund threshold escalation"
    assert "$500" in draft.condition
    assert draft.needs_clarification is False


@pytest.mark.asyncio
async def test_answer_to_skill_unwraps_markdown_fence() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        '```json\n'
        '{"name": "X", "condition": "C", "action": "A",'
        ' "needs_clarification": false}\n```'
    )
    draft = await answer_to_skill(
        backend, "ecommerce", target, "Q?", "A."
    )
    assert draft.name == "X"


@pytest.mark.asyncio
async def test_answer_to_skill_unknown_policy_id_raises_value_error() -> None:
    backend = _ScriptedBackend("{}")  # would-be valid if reachable
    with pytest.raises(ValueError, match="unknown policy_id"):
        await answer_to_skill(
            backend, "ecommerce", "ecommerce.does_not_exist", "Q?", "A."
        )


@pytest.mark.asyncio
async def test_answer_to_skill_missing_required_field_raises() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps({"name": "X", "condition": "C"})  # action missing
    )
    with pytest.raises(SkillBootstrapParseError, match="action"):
        await answer_to_skill(
            backend, "ecommerce", target, "Q?", "A."
        )


@pytest.mark.asyncio
async def test_answer_to_skill_needs_clarification_requires_hint() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps(
            {
                "name": "X",
                "condition": "C",
                "action": "A",
                "needs_clarification": True,
                "clarification_hint": "",
            }
        )
    )
    with pytest.raises(SkillBootstrapParseError, match="clarification_hint"):
        await answer_to_skill(
            backend, "ecommerce", target, "Q?", "I dunno."
        )


@pytest.mark.asyncio
async def test_answer_to_skill_user_message_carries_question_and_answer() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps(
            {"name": "X", "condition": "C", "action": "A", "needs_clarification": False}
        )
    )
    await answer_to_skill(
        backend,
        "ecommerce",
        target,
        "What dollar amount?",
        "$500.",
    )
    assert backend.last_user is not None
    assert "What dollar amount?" in backend.last_user
    assert "$500." in backend.last_user


# --- endpoints ------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap_analyze_endpoint_happy_path() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps({"missing": [{"policy_id": target, "questions": []}]})
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/gap_analyze",
            json={"domain": "ecommerce", "extracted_skills": []},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["missing"]) == 1
    assert body["missing"][0]["policy_id"] == target


@pytest.mark.asyncio
async def test_gap_analyze_endpoint_502_on_parse_error() -> None:
    backend = _ScriptedBackend("not json")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/gap_analyze",
            json={"domain": "ecommerce", "extracted_skills": []},
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_gap_analyze_endpoint_other_domain_returns_empty() -> None:
    backend = _ScriptedBackend("UNREACHABLE")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/gap_analyze",
            json={"domain": "other", "extracted_skills": []},
        )
    assert resp.status_code == 200
    assert resp.json() == {"missing": []}


@pytest.mark.asyncio
async def test_answer_to_skill_endpoint_happy_path() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps(
            {
                "name": "X",
                "condition": "C with $500",
                "action": "A",
                "rationale": "R",
                "needs_clarification": False,
            }
        )
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/answer_to_skill",
            json={
                "domain": "ecommerce",
                "policy_id": target,
                "question": "Q?",
                "answer": "$500.",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["condition"] == "C with $500"


@pytest.mark.asyncio
async def test_answer_to_skill_endpoint_422_on_unknown_policy_id() -> None:
    backend = _ScriptedBackend("{}")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/answer_to_skill",
            json={
                "domain": "ecommerce",
                "policy_id": "ecommerce.fabricated",
                "question": "Q?",
                "answer": "A.",
            },
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_answer_to_skill_endpoint_502_on_parse_error() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend("not json")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/answer_to_skill",
            json={
                "domain": "ecommerce",
                "policy_id": target,
                "question": "Q?",
                "answer": "A.",
            },
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_skills_endpoints_respect_bearer_auth(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-x")
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps({"missing": [{"policy_id": target, "questions": []}]})
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        unauth = await c.post(
            "/v1/skills/gap_analyze",
            json={"domain": "ecommerce", "extracted_skills": []},
        )
        ok = await c.post(
            "/v1/skills/gap_analyze",
            headers={"Authorization": "Bearer secret-x"},
            json={"domain": "ecommerce", "extracted_skills": []},
        )
    assert unauth.status_code == 401
    assert ok.status_code == 200
