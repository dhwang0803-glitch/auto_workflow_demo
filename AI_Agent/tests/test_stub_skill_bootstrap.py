"""StubLLMBackend coverage for the skill-bootstrap call types (PLAN_12 W2-8a + W2-4a/b/c).

The live LLM is mocked end-to-end on `LLM_BACKEND=stub`, so these tests
make sure the stub returns shapes the live parsers (services/skill_bootstrap
+ services/domain_classifier) accept without fallback. Without this
coverage the integration walkthrough only catches missing fields at the
wizard UI, where the failure mode is opaque.

2026-04-28 polish: gap_analyze service now has a deterministic
short-circuit (extracted_skills empty), so the stub's gap_analyze branch
only fires when extracted_skills is non-empty (Persona B-style). The
short-circuit case is exercised here through the service entry point.
"""
from __future__ import annotations

import json

import pytest

from app.backends.stub import StubLLMBackend
from app.models.skills import ExtractedSkill, ParameterAnswer
from app.services.domain_classifier import (
    _classifier_system_prompt,
    classify_domain,
)
from app.services.skill_bootstrap import (
    _gap_analyze_system_prompt,
    _seed_policies,
    analyze_gaps,
    answer_to_skill,
    answers_to_skill,
)


@pytest.fixture
def stub() -> StubLLMBackend:
    return StubLLMBackend()


# --- classify_domain ------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_domain",
    [
        ("I run an online store selling skincare products", "ecommerce"),
        ("We're a charity foundation accepting donor contributions", "nonprofit"),
        ("Boutique consulting advisory practice", "consulting"),
        ("I publish a weekly newsletter and a podcast", "content"),
        ("Hair salon with appointment booking", "services"),
        ("Internal tooling team for a manufacturing line", "other"),
    ],
)
@pytest.mark.asyncio
async def test_classify_domain_keyword_routing(
    stub: StubLLMBackend, text: str, expected_domain: str
) -> None:
    result = await classify_domain(stub, text)
    assert result.domain == expected_domain
    assert 0 <= result.confidence <= 1
    assert result.rationale  # non-empty


@pytest.mark.asyncio
async def test_classify_domain_returns_parseable_json(
    stub: StubLLMBackend,
) -> None:
    """Stub output must be a single JSON object — no markdown fences."""
    raw = await stub.complete(
        system=_classifier_system_prompt(),
        user_message="online store",
        max_tokens=256,
    )
    body = json.loads(raw)
    assert set(body.keys()) >= {"domain", "confidence", "rationale"}


# --- gap_analyze (service entry — service short-circuits when empty) ------


@pytest.mark.asyncio
async def test_gap_analyze_short_circuit_returns_all_seeds_with_baselines(
    stub: StubLLMBackend,
) -> None:
    """Persona A path through the service: no LLM call, every seed comes
    through with new wizard fields populated from the YAML."""
    result = await analyze_gaps(stub, "ecommerce", extracted_skills=[])
    seeds = _seed_policies("ecommerce")
    assert len(result.missing) == len(seeds)
    first = result.missing[0]
    first_seed = seeds[0]
    assert first.policy_id == first_seed["id"]
    assert first.source_kind == first_seed["source_kind"]
    assert len(first.parameters) == len(first_seed["parameters"])
    assert first.parameters[0].default_baseline == first_seed["parameters"][0]["default_baseline"]


@pytest.mark.asyncio
async def test_gap_analyze_stub_branch_returns_coverage_only_shape(
    stub: StubLLMBackend,
) -> None:
    """When extracted_skills is non-empty the service hits the stub. The
    stub returns the new coverage-only schema (`missing_policy_ids`)."""
    raw = await stub.complete(
        system=_gap_analyze_system_prompt("ecommerce"),
        user_message=json.dumps([{"name": "X", "condition": "C", "action": "A"}]),
        max_tokens=1024,
    )
    body = json.loads(raw)
    assert "missing_policy_ids" in body
    seed_ids = {p["id"] for p in _seed_policies("ecommerce")}
    for pid in body["missing_policy_ids"]:
        assert pid in seed_ids


@pytest.mark.asyncio
async def test_gap_analyze_stub_caps_at_five_for_demo_predictability(
    stub: StubLLMBackend,
) -> None:
    """Stub-specific cap so a Persona B walkthrough on the stub stays
    deterministic in scripted demos. Live LLM is free to return any size."""
    extracted = [ExtractedSkill(name="X", condition="C", action="A")]
    result = await analyze_gaps(stub, "ecommerce", extracted_skills=extracted)
    assert len(result.missing) <= 5


@pytest.mark.asyncio
async def test_gap_analyze_other_domain_short_circuits(
    stub: StubLLMBackend,
) -> None:
    result = await analyze_gaps(stub, "other", extracted_skills=[])
    assert result.missing == []


# --- answers_to_skill (batch) ---------------------------------------------


@pytest.mark.asyncio
async def test_answers_to_skill_clean_answer_batch(stub: StubLLMBackend) -> None:
    seed = _seed_policies("ecommerce")[0]
    params = seed["parameters"]
    draft = await answers_to_skill(
        stub,
        domain="ecommerce",
        policy_id=seed["id"],
        answers=[
            ParameterAnswer(parameter=params[0]["name"], answer="$200"),
            ParameterAnswer(parameter=params[1]["name"], answer="Sarah (co-founder)"),
        ],
    )
    assert draft.needs_clarification is False
    assert "$200" in draft.condition  # value substituted
    assert "Sarah" in draft.condition or "Sarah" in draft.action


@pytest.mark.parametrize(
    "vague_answer",
    [
        "I don't know",
        "it depends",
        "not sure",
        "?",
        "uh",
    ],
)
@pytest.mark.asyncio
async def test_answers_to_skill_flags_vague_answers(
    stub: StubLLMBackend, vague_answer: str
) -> None:
    seed = _seed_policies("ecommerce")[0]
    params = seed["parameters"]
    draft = await answers_to_skill(
        stub,
        domain="ecommerce",
        policy_id=seed["id"],
        answers=[
            ParameterAnswer(parameter=params[0]["name"], answer=vague_answer)
        ],
    )
    assert draft.needs_clarification is True
    assert draft.clarification_hint  # live parser rejects empty hint


@pytest.mark.asyncio
async def test_answers_to_skill_unknown_policy_id_surfaces(
    stub: StubLLMBackend,
) -> None:
    """Service guards before calling the backend."""
    with pytest.raises(ValueError):
        await answers_to_skill(
            stub,
            domain="ecommerce",
            policy_id="not.a.real.policy",
            answers=[ParameterAnswer(parameter="X", answer="Y")],
        )


# --- legacy answer_to_skill wrapper ---------------------------------------


@pytest.mark.asyncio
async def test_legacy_answer_to_skill_clean_answer(stub: StubLLMBackend) -> None:
    """Single-shot wrapper still works on the stub via the batch path."""
    seed = _seed_policies("ecommerce")[0]
    draft = await answer_to_skill(
        stub,
        domain="ecommerce",
        policy_id=seed["id"],
        question="What is your refund auto-approve limit?",
        answer="$200",
    )
    assert draft.needs_clarification is False
    assert "$200" in draft.condition or "$200" in draft.action


# --- compose path unchanged -----------------------------------------------


@pytest.mark.asyncio
async def test_compose_path_still_returns_draft(stub: StubLLMBackend) -> None:
    """PLAN_02 AI Composer flow must keep working unchanged on the stub —
    the new branching is system-prompt-keyed and falls through to the
    legacy compose path when none of the skill-bootstrap markers match."""
    raw = await stub.complete(
        system="You are an AI workflow composer.",
        user_message="Send a daily report by email",
        max_tokens=4096,
    )
    assert "```json" in raw
    payload_str = raw.split("```json", 1)[1].split("```", 1)[0]
    payload = json.loads(payload_str)
    assert payload["intent"] == "draft"
    assert payload["proposed_dag"] is not None
