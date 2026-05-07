"""HTTP route tests for /v1/policy/extract_reflective (PLAN_13 PR-C).

Drives the FastAPI app via ASGITransport (no real network), with a
sequenced stub backend so each test exercises a specific termination
branch end-to-end. Mirrors the conventions from
`test_policy_extract.py` for the single-shot endpoint so a reader can
A/B the two contracts side by side.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


def _candidate(
    name: str = "approve-large-refunds",
    *,
    condition: str = "Refunds over $500",
    action: str = "Escalate to manager",
    needs_clarification: bool = False,
    clarification_hint: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} description",
        "condition": condition,
        "action": action,
        "rationale": "Drawn from the chunk",
        "needs_clarification": needs_clarification,
        "clarification_hint": clarification_hint,
    }


def _payload(*candidates: dict[str, Any]) -> str:
    return json.dumps({"candidates": list(candidates)})


class _SequencedBackend:
    """Returns the next response from `responses` per `complete` call.

    Same shape as the graph-test backend so behavior is consistent
    across both test files. Captures last `images` so the route test
    can assert images forwarded.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.last_images: list[str] | None = None

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        if self.calls >= len(self._responses):
            raise AssertionError(
                f"backend ran out of responses at call #{self.calls + 1}"
            )
        out = self._responses[self.calls]
        self.last_images = images
        self.calls += 1
        return out

    async def stream(self, **_):  # noqa: ANN001, ANN003
        if False:
            yield ""

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


# --- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_200_with_final_candidates_and_full_trace() -> None:
    backend = _SequencedBackend([_payload(_candidate())])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={
                "chunk": "Refunds over $500 must be approved by a manager.",
                "domain": "ecommerce",
            },
        )

    assert resp.status_code == 200
    body = resp.json()

    # Final candidates surface the iter-1 drafts (single converge case).
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["name"] == "approve-large-refunds"

    # Trace is fully populated and terminated.
    trace = body["agent_trace"]
    assert trace["terminated"] is True
    assert trace["reason"] == "converge"
    assert len(trace["iterations"]) == 1

    iter1 = trace["iterations"][0]
    assert iter1["eval"] is not None
    assert iter1["eval"]["decision"] == "converge"
    assert iter1["prompt_hint"] == ""

    # PR-C leaves langsmith_url null (PR-D wires it).
    assert body["langsmith_url"] is None


# --- multi-iter trace surfaces ------------------------------------------


@pytest.mark.asyncio
async def test_retry_then_converge_records_both_iterations_in_trace() -> None:
    backend = _SequencedBackend(
        [
            _payload(),  # iter 1: empty + policy keywords → rule 1 retry
            _payload(_candidate()),  # iter 2: real candidate → converge
        ]
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={
                "chunk": "All purchase orders over $1000 shall require approval.",
                "max_iter": 2,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    trace = body["agent_trace"]

    assert trace["reason"] == "converge"
    assert len(trace["iterations"]) == 2

    # Iter 1: retry decision, empty drafts, no prompt_hint applied.
    assert trace["iterations"][0]["eval"]["decision"] == "retry"
    assert trace["iterations"][0]["drafts"] == []
    assert trace["iterations"][0]["prompt_hint"] == ""

    # Iter 2: converge, prompt_hint carries reflect's bullet list.
    assert trace["iterations"][1]["eval"]["decision"] == "converge"
    assert trace["iterations"][1]["prompt_hint"].startswith("- ")
    assert "policy-imperative" in trace["iterations"][1]["prompt_hint"]

    # Final candidates = iter 2's drafts (latest-iter superset policy).
    assert len(body["candidates"]) == 1


# --- request validation ---------------------------------------------------


@pytest.mark.asyncio
async def test_returns_422_on_invalid_max_iter() -> None:
    app = create_app(backend_override=_SequencedBackend([_payload()]))
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # max_iter=0 violates ge=1
        resp_low = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "x", "max_iter": 0},
        )
        # max_iter=10 violates le=5
        resp_high = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "x", "max_iter": 10},
        )

    assert resp_low.status_code == 422
    assert resp_high.status_code == 422


@pytest.mark.asyncio
async def test_returns_422_on_empty_chunk() -> None:
    app = create_app(backend_override=_SequencedBackend([_payload()]))
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": ""},
        )
    assert resp.status_code == 422


# --- LLM parse error → 502 ------------------------------------------------


@pytest.mark.asyncio
async def test_returns_502_on_parse_error() -> None:
    """Same envelope as /v1/policy/extract — parse failure on iter 1
    propagates out of the agent as 502 with `error/raw_len/raw` body.
    """
    backend = _SequencedBackend(["this is not json at all"])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "anything", "domain": "other"},
        )

    assert resp.status_code == 502
    body = resp.json()
    detail = body["detail"]
    assert "error" in detail
    assert "raw_len" in detail
    assert "raw" in detail


# --- bearer auth ---------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_gated_by_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-x")
    backend = _SequencedBackend([_payload(_candidate())])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # No Authorization header → 401
        resp_missing = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "Refunds must be approved."},
        )
        # Wrong token → 403
        resp_wrong = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "Refunds must be approved."},
            headers={"Authorization": "Bearer wrong"},
        )
        # Correct token → 200
        resp_ok = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "Refunds must be approved."},
            headers={"Authorization": "Bearer secret-x"},
        )

    assert resp_missing.status_code == 401
    assert resp_wrong.status_code == 403
    assert resp_ok.status_code == 200


# --- images forwarded through agent ---------------------------------------


@pytest.mark.asyncio
async def test_images_field_forwarded_to_backend() -> None:
    """`images` must reach `backend.complete` — Phase D's smoke depends
    on this, and the reflective endpoint must preserve the contract.
    """
    backend = _SequencedBackend([_payload(_candidate())])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    image_url = "data:image/png;base64,iVBORw0KGgoAAAA="

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={
                "chunk": "Refunds must be approved.",
                "images": [image_url],
            },
        )

    assert resp.status_code == 200
    assert backend.last_images == [image_url]


# --- max_iter=1 single-pass equivalence ----------------------------------


@pytest.mark.asyncio
async def test_max_iter_1_runs_a_single_pass_via_route() -> None:
    """The reflective endpoint with max_iter=1 should produce the same
    candidate set as /v1/policy/extract (one extract call, no retry).
    """
    backend = _SequencedBackend([_payload(_candidate())])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={
                "chunk": "Refunds over $500 must be approved by a manager.",
                "max_iter": 1,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert backend.calls == 1
    assert len(body["agent_trace"]["iterations"]) == 1
    assert len(body["candidates"]) == 1
