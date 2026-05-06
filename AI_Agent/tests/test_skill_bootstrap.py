"""Tests for gap_analyze + answers_to_skill (PLAN_12 W2-4).

Covers both the deterministic short-circuit (extracted_skills empty,
Persona A) and the LLM coverage path (extracted_skills non-empty,
Persona B). The LLM no longer generates wizard question text — that
comes from the seed YAML's `parameters[].prompt` — so the gap_analyze
LLM contract is now coverage-only (`{missing_policy_ids: [...]}`).
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.models.skills import ExtractedSkill, ParameterAnswer
from app.services.skill_bootstrap import (
    SkillBootstrapParseError,
    _seed_policies,
    analyze_gaps,
    answer_to_skill,
    answers_to_skill,
)


class _ScriptedBackend:
    """LLMBackend duck-type returning a fixed string."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.last_system: str | None = None
        self.last_user: str | None = None
        self.last_max_tokens: int | None = None
        self.call_count = 0

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        self.last_system = system
        self.last_user = user_message
        self.last_max_tokens = max_tokens
        self.call_count += 1
        return self._response

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> AsyncIterator[str]:
        yield self._response  # not used here

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _ecommerce_ids() -> list[str]:
    return [p["id"] for p in _seed_policies("ecommerce")]


def _first_param(policy_id: str) -> str:
    seed = next(p for p in _seed_policies("ecommerce") if p["id"] == policy_id)
    return seed["parameters"][0]["name"]


# --- analyze_gaps service -------------------------------------------------


@pytest.mark.asyncio
async def test_gap_analyze_other_domain_short_circuits() -> None:
    backend = _ScriptedBackend("UNREACHABLE")
    result = await analyze_gaps(backend, "other", [])
    assert result.missing == []
    assert backend.call_count == 0


@pytest.mark.asyncio
async def test_gap_analyze_empty_extracted_short_circuits_emits_all_seeds() -> None:
    """Persona A path: no LLM call, every seed comes through with the
    seed-defined wizard fields (prompt, baseline, baseline_source)."""
    backend = _ScriptedBackend("UNREACHABLE")
    result = await analyze_gaps(backend, "ecommerce", [])
    seeds = _seed_policies("ecommerce")
    assert backend.call_count == 0
    assert len(result.missing) == len(seeds)
    # First gap matches first seed and carries every wizard field.
    first = result.missing[0]
    first_seed = seeds[0]
    assert first.policy_id == first_seed["id"]
    assert first.policy_name == first_seed["name"]
    assert first.source_kind == first_seed["source_kind"]
    assert len(first.parameters) == len(first_seed["parameters"])
    p0 = first.parameters[0]
    p0_seed = first_seed["parameters"][0]
    assert p0.text == p0_seed["prompt"]
    assert p0.parameter == p0_seed["name"]
    assert p0.default_baseline == p0_seed["default_baseline"]
    assert p0.baseline_source == p0_seed["baseline_source"]
    # Backward-compat alias is populated identically.
    assert first.questions == first.parameters


@pytest.mark.asyncio
async def test_gap_analyze_non_empty_extracted_invokes_llm_coverage_check() -> None:
    """Persona B path: extracted_skills non-empty → LLM is called, returns
    a coverage-only `missing_policy_ids` shape, service enriches from seed."""
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps({"missing_policy_ids": [target]})
    )
    extracted = [
        ExtractedSkill(
            name="Refund cap",
            condition="Customer asks for refund > $1000",
            action="Forward to founder via email",
        )
    ]
    result = await analyze_gaps(backend, "ecommerce", extracted)
    assert backend.call_count == 1
    assert len(result.missing) == 1
    gap = result.missing[0]
    seed = next(p for p in _seed_policies("ecommerce") if p["id"] == target)
    # Service still fills in all wizard fields from seed even though the
    # LLM returned only the id.
    assert gap.policy_name == seed["name"]
    assert len(gap.parameters) == len(seed["parameters"])
    assert gap.parameters[0].text == seed["parameters"][0]["prompt"]


@pytest.mark.asyncio
async def test_gap_analyze_accepts_legacy_missing_shape() -> None:
    """Tolerates the older `missing: [{policy_id}]` LLM output while the
    prompt rolls out — keeps a transient mid-roll deploy from breaking."""
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps({"missing": [{"policy_id": target, "questions": []}]})
    )
    extracted = [ExtractedSkill(name="X", condition="C", action="A")]
    result = await analyze_gaps(backend, "ecommerce", extracted)
    assert len(result.missing) == 1
    assert result.missing[0].policy_id == target


@pytest.mark.asyncio
async def test_gap_analyze_unknown_policy_id_rejected() -> None:
    backend = _ScriptedBackend(
        json.dumps({"missing_policy_ids": ["ecommerce.fabricated_id"]})
    )
    extracted = [ExtractedSkill(name="X", condition="C", action="A")]
    with pytest.raises(SkillBootstrapParseError, match="fabricated_id"):
        await analyze_gaps(backend, "ecommerce", extracted)


@pytest.mark.asyncio
async def test_gap_analyze_invalid_json_raises() -> None:
    backend = _ScriptedBackend("I think there are some gaps but...")
    extracted = [ExtractedSkill(name="X", condition="C", action="A")]
    with pytest.raises(SkillBootstrapParseError):
        await analyze_gaps(backend, "ecommerce", extracted)


@pytest.mark.asyncio
async def test_gap_analyze_missing_field_raises() -> None:
    backend = _ScriptedBackend(json.dumps({"not_missing": []}))
    extracted = [ExtractedSkill(name="X", condition="C", action="A")]
    with pytest.raises(SkillBootstrapParseError):
        await analyze_gaps(backend, "ecommerce", extracted)


@pytest.mark.asyncio
async def test_gap_analyze_passes_extracted_skills_in_user_message() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps({"missing_policy_ids": [target]})
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


# --- answers_to_skill service (batch) -------------------------------------


@pytest.mark.asyncio
async def test_answers_to_skill_happy_path() -> None:
    target = _ecommerce_ids()[0]
    seed = next(p for p in _seed_policies("ecommerce") if p["id"] == target)
    params = seed["parameters"]
    backend = _ScriptedBackend(
        json.dumps(
            {
                "name": "Refund threshold escalation",
                "description": "Refunds above $500 require manager approval.",
                "condition": "Customer requests refund AND amount > $500",
                "action": "Forward to manager Sarah on #refunds Slack channel",
                "rationale": "Large refunds need human judgment.",
                "needs_clarification": False,
                "clarification_hint": "",
            }
        )
    )
    draft = await answers_to_skill(
        backend,
        "ecommerce",
        target,
        [
            ParameterAnswer(parameter=params[0]["name"], answer="$500"),
            ParameterAnswer(parameter=params[1]["name"], answer="Sarah"),
        ],
    )
    assert draft.name == "Refund threshold escalation"
    assert "$500" in draft.condition
    assert draft.needs_clarification is False
    # User message should carry per-parameter answer pairs (not raw QA).
    assert backend.last_user is not None
    assert "$500" in backend.last_user
    assert "Sarah" in backend.last_user
    assert params[0]["name"] in backend.last_user


@pytest.mark.asyncio
async def test_answers_to_skill_unknown_policy_id_raises_value_error() -> None:
    backend = _ScriptedBackend("{}")
    with pytest.raises(ValueError, match="unknown policy_id"):
        await answers_to_skill(
            backend,
            "ecommerce",
            "ecommerce.does_not_exist",
            [ParameterAnswer(parameter="X", answer="Y")],
        )


@pytest.mark.asyncio
async def test_answers_to_skill_unknown_parameter_raises_value_error() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend("{}")
    with pytest.raises(ValueError, match="unknown parameter"):
        await answers_to_skill(
            backend,
            "ecommerce",
            target,
            [ParameterAnswer(parameter="TOTALLY_FAKE_PARAM", answer="Y")],
        )


@pytest.mark.asyncio
async def test_answers_to_skill_missing_required_field_raises() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend(
        json.dumps({"name": "X", "condition": "C"})  # action missing
    )
    with pytest.raises(SkillBootstrapParseError, match="action"):
        await answers_to_skill(
            backend,
            "ecommerce",
            target,
            [ParameterAnswer(parameter=_first_param(target), answer="$50")],
        )


@pytest.mark.asyncio
async def test_answers_to_skill_needs_clarification_requires_hint() -> None:
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
        await answers_to_skill(
            backend,
            "ecommerce",
            target,
            [ParameterAnswer(parameter=_first_param(target), answer="dunno")],
        )


# --- legacy answer_to_skill wrapper --------------------------------------


@pytest.mark.asyncio
async def test_legacy_answer_to_skill_routes_through_batch() -> None:
    """Single-shot wrapper still works — it picks the first parameter as
    the target and passes through to the batch path."""
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
    draft = await answer_to_skill(
        backend, "ecommerce", target, "What dollar amount?", "$500"
    )
    assert draft.name == "X"
    # Legacy path discards `question` and uses seed prompt instead — but
    # the user's answer must reach the LLM.
    assert backend.last_user is not None
    assert "$500" in backend.last_user


@pytest.mark.asyncio
async def test_legacy_answer_to_skill_unknown_policy_raises() -> None:
    backend = _ScriptedBackend("{}")
    with pytest.raises(ValueError, match="unknown policy_id"):
        await answer_to_skill(
            backend, "ecommerce", "ecommerce.does_not_exist", "Q?", "A."
        )


# --- endpoints ------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap_analyze_endpoint_persona_a_path() -> None:
    """Empty extracted_skills → service short-circuits, no LLM call."""
    backend = _ScriptedBackend("UNREACHABLE")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/gap_analyze",
            json={"domain": "ecommerce", "extracted_skills": []},
        )
    assert resp.status_code == 200
    body = resp.json()
    seeds = _seed_policies("ecommerce")
    assert len(body["missing"]) == len(seeds)
    assert backend.call_count == 0
    # First gap carries new fields end-to-end.
    first = body["missing"][0]
    assert first["policy_id"] == seeds[0]["id"]
    assert first["source_kind"] == seeds[0]["source_kind"]
    assert len(first["parameters"]) == len(seeds[0]["parameters"])
    assert first["parameters"][0]["default_baseline"] == seeds[0]["parameters"][0]["default_baseline"]
    # Legacy `questions` alias still present for API_Server pre-#143.
    assert first["questions"] == first["parameters"]


@pytest.mark.asyncio
async def test_gap_analyze_endpoint_502_on_parse_error() -> None:
    backend = _ScriptedBackend("not json")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/gap_analyze",
            json={
                "domain": "ecommerce",
                "extracted_skills": [
                    {"name": "X", "condition": "C", "action": "A"}
                ],
            },
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
async def test_answers_to_skill_endpoint_happy_path() -> None:
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
            "/v1/skills/answers_to_skill",
            json={
                "domain": "ecommerce",
                "policy_id": target,
                "answers": [
                    {"parameter": _first_param(target), "answer": "$500"}
                ],
            },
        )
    assert resp.status_code == 200
    assert resp.json()["condition"] == "C with $500"


@pytest.mark.asyncio
async def test_answers_to_skill_endpoint_422_on_unknown_policy_id() -> None:
    backend = _ScriptedBackend("{}")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/answers_to_skill",
            json={
                "domain": "ecommerce",
                "policy_id": "ecommerce.fabricated",
                "answers": [{"parameter": "X", "answer": "Y"}],
            },
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_answers_to_skill_endpoint_422_on_unknown_parameter() -> None:
    target = _ecommerce_ids()[0]
    backend = _ScriptedBackend("{}")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/skills/answers_to_skill",
            json={
                "domain": "ecommerce",
                "policy_id": target,
                "answers": [{"parameter": "FAKE_PARAM", "answer": "Y"}],
            },
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_legacy_answer_to_skill_endpoint_still_works() -> None:
    """Legacy `/v1/skills/answer_to_skill` stays functional until PR #143."""
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
                "question": "What dollar amount?",
                "answer": "$500.",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["condition"] == "C with $500"


@pytest.mark.asyncio
async def test_skills_endpoints_respect_bearer_auth(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-x")
    backend = _ScriptedBackend("UNREACHABLE")
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
