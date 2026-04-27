"""Tests for the domain classifier service + endpoint (PLAN_12 W2-2)."""
from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services.domain_classifier import (
    ClassifierParseError,
    _classifier_system_prompt,
    classify_domain,
)


class _ScriptedBackend:
    """LLMBackend duck-type returning a fixed response string."""

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
        yield self._response  # not used by classifier

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


# --- prompt construction --------------------------------------------------


def test_prompt_lists_all_seed_domains() -> None:
    prompt = _classifier_system_prompt()
    for domain in ["ecommerce", "services", "consulting", "content", "nonprofit"]:
        assert f"- {domain} (" in prompt, f"{domain} missing from prompt"
    assert "- other (None of the above)" in prompt


def test_prompt_specifies_json_only_output() -> None:
    prompt = _classifier_system_prompt()
    assert "Output ONLY a single JSON object" in prompt
    assert "domain" in prompt and "confidence" in prompt and "rationale" in prompt


# --- parser ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_parses_clean_json() -> None:
    backend = _ScriptedBackend(
        json.dumps(
            {"domain": "ecommerce", "confidence": 0.9, "rationale": "Online store."}
        )
    )
    result = await classify_domain(backend, "We sell handmade candles online.")
    assert result.domain == "ecommerce"
    assert result.confidence == 0.9
    assert result.rationale == "Online store."


@pytest.mark.asyncio
async def test_unwraps_markdown_fence() -> None:
    backend = _ScriptedBackend(
        '```json\n{"domain": "services", "confidence": 0.8, "rationale": "Hair salon."}\n```'
    )
    result = await classify_domain(backend, "I run a hair salon.")
    assert result.domain == "services"


@pytest.mark.asyncio
async def test_tolerates_preamble_before_json() -> None:
    backend = _ScriptedBackend(
        'Sure! {"domain": "consulting", "confidence": 0.75, "rationale": "B2B advisory."}'
    )
    result = await classify_domain(backend, "I advise companies on M&A.")
    assert result.domain == "consulting"


@pytest.mark.asyncio
async def test_clamps_out_of_range_confidence() -> None:
    backend = _ScriptedBackend(
        json.dumps({"domain": "content", "confidence": 1.7, "rationale": "Podcast."})
    )
    result = await classify_domain(backend, "I host a weekly podcast.")
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_rejects_unknown_domain() -> None:
    backend = _ScriptedBackend(
        json.dumps({"domain": "manufacturing", "confidence": 0.9, "rationale": ""})
    )
    with pytest.raises(ClassifierParseError, match="manufacturing"):
        await classify_domain(backend, "We make widgets.")


@pytest.mark.asyncio
async def test_rejects_missing_confidence() -> None:
    backend = _ScriptedBackend(
        json.dumps({"domain": "nonprofit", "rationale": "Charity."})
    )
    with pytest.raises(ClassifierParseError, match="confidence"):
        await classify_domain(backend, "We run a food bank.")


@pytest.mark.asyncio
async def test_rejects_non_json_response() -> None:
    backend = _ScriptedBackend("I'm not sure how to classify that.")
    with pytest.raises(ClassifierParseError, match="no JSON object"):
        await classify_domain(backend, "...")


@pytest.mark.asyncio
async def test_max_tokens_passed_to_backend() -> None:
    backend = _ScriptedBackend(
        json.dumps({"domain": "other", "confidence": 0.4, "rationale": "Unclear."})
    )
    await classify_domain(backend, "Hello.")
    # Classifier responses are tiny — sending the full multi-turn budget
    # would just slow cold-start and waste cache for nothing.
    assert backend.last_max_tokens is not None
    assert backend.last_max_tokens <= 512


# --- endpoint -------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_returns_classification() -> None:
    backend = _ScriptedBackend(
        json.dumps(
            {"domain": "ecommerce", "confidence": 0.92, "rationale": "Storefront."}
        )
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/domain/classify",
            json={"text": "We run a Shopify store selling skincare."},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "domain": "ecommerce",
        "confidence": 0.92,
        "rationale": "Storefront.",
    }


@pytest.mark.asyncio
async def test_endpoint_502_on_parse_error() -> None:
    backend = _ScriptedBackend("not json at all")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/domain/classify",
            json={"text": "Anything."},
        )
    assert resp.status_code == 502
    assert "no JSON object" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_endpoint_422_on_empty_text() -> None:
    backend = _ScriptedBackend("{}")
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/v1/domain/classify", json={"text": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_endpoint_respects_bearer_auth(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-x")
    backend = _ScriptedBackend(
        json.dumps({"domain": "services", "confidence": 0.8, "rationale": "Salon."})
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        unauth = await c.post(
            "/v1/domain/classify",
            json={"text": "Hair salon."},
        )
        ok = await c.post(
            "/v1/domain/classify",
            headers={"Authorization": "Bearer secret-x"},
            json={"text": "Hair salon."},
        )
    assert unauth.status_code == 401
    assert ok.status_code == 200
