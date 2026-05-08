"""AIAgentHTTPBackend bearer-header smoke.

The full request/response shape is exercised by test_ai_composer.py via the
service layer. These tests pin the bearer-header wiring so the Modal endpoint
auth contract doesn't silently regress.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services.ai_agent_client import AIAgentHTTPBackend


def _capture_handler(captured: list[dict[str, Any]]):
    """Build a MockTransport handler that records each request + returns
    a canned `/v1/complete` body. Stream tests use a separate fixture."""

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append({
            "url": str(request.url),
            "auth": request.headers.get("authorization"),
        })
        return httpx.Response(200, json={"text": "ok"})

    return _handler


@pytest.mark.asyncio
async def test_complete_attaches_bearer_when_token_set(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    monkeypatch.setattr(httpx, "AsyncClient", _patch_client(transport))

    backend = AIAgentHTTPBackend(
        base_url="https://agent.example",
        bearer_token="secret-x",
    )
    text = await backend.complete(system="s", user_message="u", max_tokens=32)

    assert text == "ok"
    assert captured[0]["auth"] == "Bearer secret-x"


@pytest.mark.asyncio
async def test_complete_omits_header_when_token_empty(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    monkeypatch.setattr(httpx, "AsyncClient", _patch_client(transport))

    backend = AIAgentHTTPBackend(base_url="https://agent.example")
    await backend.complete(system="s", user_message="u", max_tokens=32)

    assert captured[0]["auth"] is None


def _patch_client(transport: httpx.MockTransport):
    """Wrap httpx.AsyncClient so every instantiation in the backend rides on
    the same MockTransport. Backend uses `async with httpx.AsyncClient(...)`
    per call, so we can't pass transport directly — patch the constructor."""
    real_cls = httpx.AsyncClient

    class _Client(real_cls):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    return _Client


# --- extract_reflective wire round-trip -----------------------------------


@pytest.mark.asyncio
async def test_extract_reflective_round_trips_request_and_parses_response(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        import json
        captured.append({
            "url": str(request.url),
            "body": json.loads(request.content.decode()),
        })
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "name": "Refund threshold escalation",
                        "condition": "Refund > $500",
                        "action": "Forward to manager",
                    }
                ],
                "agent_trace": {
                    "iterations": [
                        {
                            "drafts": [],
                            "eval": {
                                "decision": "converge",
                                "coverage_concerns": [],
                                "schema_issues": [],
                                "rationale": "Single candidate covers the chunk",
                            },
                            "prompt_hint": "",
                        }
                    ],
                    "terminated": True,
                    "reason": "converge",
                },
                "langsmith_run_id": "abc-123",
            },
        )

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(httpx, "AsyncClient", _patch_client(transport))

    backend = AIAgentHTTPBackend(base_url="https://agent.example", bearer_token="t")
    resp = await backend.extract_reflective(
        chunk="Refunds over $500 need manager approval.",
        domain="ecommerce",
        max_iter=3,
    )

    # Wire-level assertions on the outbound request.
    assert captured[0]["url"] == "https://agent.example/v1/policy/extract_reflective"
    assert captured[0]["body"] == {
        "chunk": "Refunds over $500 need manager approval.",
        "domain": "ecommerce",
        "max_iter": 3,
    }

    # Pydantic re-validation succeeded — narrowly check the trace shape
    # so a future Modal-side rename surfaces here as a parse error.
    assert len(resp.candidates) == 1
    assert resp.candidates[0].name == "Refund threshold escalation"
    assert resp.agent_trace.terminated is True
    assert resp.agent_trace.reason == "converge"
    assert resp.agent_trace.iterations[0].eval.decision == "converge"
    assert resp.langsmith_run_id == "abc-123"


@pytest.mark.asyncio
async def test_extract_reflective_propagates_upstream_error(monkeypatch) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "upstream"})

    transport = httpx.MockTransport(_handler)
    monkeypatch.setattr(httpx, "AsyncClient", _patch_client(transport))

    backend = AIAgentHTTPBackend(base_url="https://agent.example")
    with pytest.raises(httpx.HTTPStatusError):
        await backend.extract_reflective(chunk="x", domain="other", max_iter=1)
