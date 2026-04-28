"""StubLLMBackend coverage for the skill-bootstrap call types (PLAN_12 W2-8a).

The live LLM is mocked end-to-end on `LLM_BACKEND=stub`, so these tests
make sure the stub returns shapes the live parsers (services/skill_bootstrap
+ services/domain_classifier) accept without fallback. Without this
coverage the integration walkthrough only catches missing fields at the
wizard UI, where the failure mode is opaque.
"""
from __future__ import annotations

import json

import pytest

from app.backends.stub import StubLLMBackend
from app.services.domain_classifier import (
    _classifier_system_prompt,
    classify_domain,
)
from app.services.skill_bootstrap import (
    _answer_to_skill_system_prompt,
    _gap_analyze_system_prompt,
    _seed_policies,
    analyze_gaps,
    answer_to_skill,
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


# --- gap_analyze ----------------------------------------------------------


@pytest.mark.asyncio
async def test_gap_analyze_returns_seed_backed_missing_list(
    stub: StubLLMBackend,
) -> None:
    result = await analyze_gaps(stub, "ecommerce", extracted_skills=[])
    assert len(result.missing) > 0
    seed_ids = {p["id"] for p in _seed_policies("ecommerce")}
    for gap in result.missing:
        assert gap.policy_id in seed_ids
        assert len(gap.questions) >= 1
        assert gap.questions[0].text  # non-empty


@pytest.mark.asyncio
async def test_gap_analyze_caps_at_five_for_demo_predictability(
    stub: StubLLMBackend,
) -> None:
    """Persona A walkthrough length is bounded — pick the cap that the
    stub commits to so dem scripts stay deterministic. Live LLM is free
    to return more or fewer; this is a stub-specific contract."""
    result = await analyze_gaps(stub, "ecommerce", extracted_skills=[])
    assert len(result.missing) <= 5


@pytest.mark.asyncio
async def test_gap_analyze_other_domain_short_circuits(
    stub: StubLLMBackend,
) -> None:
    """`other` has no seed YAML — services short-circuits before the LLM
    so the stub is never even called. We assert the return path here so
    a future regression that drops the short-circuit surfaces."""
    result = await analyze_gaps(stub, "other", extracted_skills=[])
    assert result.missing == []


# --- answer_to_skill ------------------------------------------------------


@pytest.mark.asyncio
async def test_answer_to_skill_clean_answer(stub: StubLLMBackend) -> None:
    seed = _seed_policies("ecommerce")[0]
    draft = await answer_to_skill(
        stub,
        domain="ecommerce",
        policy_id=seed["id"],
        question="What is your refund auto-approve limit?",
        answer="$200",
    )
    assert draft.needs_clarification is False
    assert "$200" in draft.action
    assert seed["id"] in draft.condition or seed["id"] in draft.name


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
async def test_answer_to_skill_flags_vague_answers(
    stub: StubLLMBackend, vague_answer: str
) -> None:
    seed = _seed_policies("ecommerce")[0]
    draft = await answer_to_skill(
        stub,
        domain="ecommerce",
        policy_id=seed["id"],
        question="What is your refund auto-approve limit?",
        answer=vague_answer,
    )
    assert draft.needs_clarification is True
    # Live parser rejects empty hint when needs_clarification=true.
    assert draft.clarification_hint


@pytest.mark.asyncio
async def test_answer_to_skill_unknown_policy_id_surfaces(
    stub: StubLLMBackend,
) -> None:
    """answer_to_skill (the service, not the stub) raises ValueError when
    policy_id is not in the seed. The stub itself never sees that path —
    the service guards before calling the backend."""
    with pytest.raises(ValueError):
        await answer_to_skill(
            stub,
            domain="ecommerce",
            policy_id="not.a.real.policy",
            question="Q",
            answer="A",
        )


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
    assert "```json" in raw  # legacy fenced wrapper preserved
    # Find the JSON body and confirm shape.
    payload_str = raw.split("```json", 1)[1].split("```", 1)[0]
    payload = json.loads(payload_str)
    assert payload["intent"] == "draft"
    assert payload["proposed_dag"] is not None
