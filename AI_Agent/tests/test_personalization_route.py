"""HTTP route tests for /v1/personalization/extract_from_diff.

PR-E scope. The route is a thin wrapper around PR-C's diff and PR-D's
agent — the unit tests in `test_workflow_diff.py` and
`test_personalization_agent.py` already cover the deterministic and
agent contracts. Here we lock in the wire shape: validation, envelope
fields, drop-reason surfacing, `rejected_hashes` plumbing, and the
`langsmith_run_id` env gate.
"""
from __future__ import annotations

import json
import os
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


# Common fixture payloads — the user adds a Slack notify after an
# existing HTTP step. Mirrors the unit-test fixture so the wire-shape
# tests rest on the same edit pattern as the agent unit tests.
_V1 = {
    "nodes": [{"id": "fetch", "type": "http_request", "config": {}}],
    "edges": [],
}
_V2 = {
    "nodes": [
        {"id": "fetch", "type": "http_request", "config": {}},
        {
            "id": "notify",
            "type": "slack_notify",
            "config": {"channel": "#alerts"},
        },
    ],
    "edges": [{"source": "fetch", "target": "notify"}],
}


class _ScriptedBackend:
    """Returns canned responses in order. Records each call so tests
    can assert prompts the route handed to the agent."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        del images
        self.calls.append(
            {
                "system": system,
                "user_message": user_message,
                "max_tokens": max_tokens,
            }
        )
        if not self.responses:
            raise AssertionError(
                "_ScriptedBackend exhausted — route made one more call than expected"
            )
        return self.responses.pop(0)

    async def stream(self, **_: Any):  # noqa: ANN003
        if False:  # pragma: no cover
            yield ""

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _propose(hint: str, *, is_noise: bool = False) -> str:
    return json.dumps({"hint": hint, "is_noise": is_noise})


def _judge(decision: str, reason: str = "") -> str:
    return json.dumps({"decision": decision, "reason": reason})


# --- happy path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_200_with_outcome_diff_and_signature() -> None:
    backend = _ScriptedBackend(
        [
            _propose("adds Slack notify after fetch"),
            _judge("accept", "generalizable preference"),
        ]
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V2},
        )

    assert resp.status_code == 200
    body = resp.json()

    assert body["outcome"]["accepted"] is True
    assert body["outcome"]["drop_reason"] == ""
    assert body["outcome"]["proposal"]["hint"] == "adds Slack notify after fetch"
    assert body["outcome"]["judgment"]["decision"] == "accept"
    assert body["outcome"]["suggestion_hash"] is not None
    assert len(body["outcome"]["suggestion_hash"]) == 16

    assert body["diff"]["nodes_added"][0]["type"] == "slack_notify"
    assert body["diff_signature"] == "added=slack_notify;removed="

    assert body["langsmith_run_id"] is None  # tracing not configured in test


# --- drop branches surface as drop_reason ---------------------------------


@pytest.mark.asyncio
async def test_empty_diff_returns_empty_diff_drop_reason_without_llm_call() -> None:
    backend = _ScriptedBackend([])  # any call would raise
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V1},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"]["accepted"] is False
    assert body["outcome"]["drop_reason"] == "empty_diff"
    assert backend.calls == []


@pytest.mark.asyncio
async def test_empty_proposal_drops_with_reason() -> None:
    backend = _ScriptedBackend([_propose("", is_noise=True)])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V2},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"]["accepted"] is False
    assert body["outcome"]["drop_reason"] == "empty_proposal"
    assert body["outcome"]["judgment"] is None
    assert len(backend.calls) == 1  # propose only


@pytest.mark.asyncio
async def test_judge_reject_surfaces_as_drop_reason() -> None:
    backend = _ScriptedBackend(
        [_propose("renamed step label"), _judge("reject", "label rename")]
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V2},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"]["accepted"] is False
    assert body["outcome"]["drop_reason"] == "judge_reject"
    assert body["outcome"]["judgment"]["decision"] == "reject"
    # Hash still surfaced — PR-G dedupes on it even on reject.
    assert body["outcome"]["suggestion_hash"] is not None


# --- rejected_hashes plumbing ---------------------------------------------


@pytest.mark.asyncio
async def test_rejected_hashes_short_circuit_skips_judge() -> None:
    """When the propose hint hashes to one of the rejected_hashes, the
    route must not burn a judge LLM call."""
    # First call the route with no rejected_hashes to capture the
    # suggestion_hash for "adds Slack notify after fetch".
    backend1 = _ScriptedBackend(
        [_propose("adds Slack notify after fetch"), _judge("accept")]
    )
    app1 = create_app(backend_override=backend1)
    async with AsyncClient(
        transport=ASGITransport(app=app1), base_url="http://test"
    ) as c:
        first = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V2},
        )
    captured_hash = first.json()["outcome"]["suggestion_hash"]

    # Now replay with that hash in rejected_hashes — judge should be skipped.
    backend2 = _ScriptedBackend([_propose("adds Slack notify after fetch")])
    app2 = create_app(backend_override=backend2)
    async with AsyncClient(
        transport=ASGITransport(app=app2), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={
                "v1": _V1,
                "v2": _V2,
                "rejected_hashes": [captured_hash],
            },
        )

    body = resp.json()
    assert body["outcome"]["drop_reason"] == "hash_previously_rejected"
    assert body["outcome"]["suggestion_hash"] == captured_hash
    assert len(backend2.calls) == 1  # only propose, no judge


@pytest.mark.asyncio
async def test_rejected_hashes_reach_judge_prompt_on_non_match() -> None:
    """On a non-short-circuit path, judge still sees the rejected list
    in its system prompt (soft signal)."""
    backend = _ScriptedBackend(
        [_propose("adds metrics step"), _judge("accept")]
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post(
            "/v1/personalization/extract_from_diff",
            json={
                "v1": _V1,
                "v2": _V2,
                "rejected_hashes": ["abc1234567890abc"],
            },
        )

    judge_system = backend.calls[1]["system"]
    assert "abc1234567890abc" in judge_system


# --- request validation ----------------------------------------------------


@pytest.mark.asyncio
async def test_missing_v1_returns_422() -> None:
    backend = _ScriptedBackend([])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v2": _V2},
        )

    assert resp.status_code == 422
    assert backend.calls == []


@pytest.mark.asyncio
async def test_v1_nodes_must_be_a_list() -> None:
    backend = _ScriptedBackend([])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": {"nodes": None}, "v2": _V2},
        )

    assert resp.status_code == 422
    assert backend.calls == []


@pytest.mark.asyncio
async def test_empty_rejected_hashes_default_works() -> None:
    """rejected_hashes is optional; omitting it should not 422."""
    backend = _ScriptedBackend(
        [_propose("adds Slack notify"), _judge("accept")]
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V2},
        )
    assert resp.status_code == 200


# --- langsmith_run_id env gate --------------------------------------------


@pytest.mark.asyncio
async def test_langsmith_run_id_set_when_tracing_env_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "fake-key-for-tests")
    backend = _ScriptedBackend(
        [_propose("adds Slack notify"), _judge("accept")]
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V2},
        )

    run_id = resp.json()["langsmith_run_id"]
    assert run_id is not None
    assert len(run_id) == 36  # UUID string form


@pytest.mark.asyncio
async def test_langsmith_run_id_omitted_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tracing flag on but no key — same shape as a first-deploy when
    # the langsmith Modal Secret hasn't synced yet.
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    backend = _ScriptedBackend(
        [_propose("adds Slack notify"), _judge("accept")]
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V2},
        )

    assert resp.json()["langsmith_run_id"] is None


# --- user_id plumbing ------------------------------------------------------


@pytest.mark.asyncio
async def test_user_id_accepted_and_does_not_affect_agent_behavior() -> None:
    """user_id is a logging-only field — same agent output regardless."""
    backend = _ScriptedBackend(
        [_propose("adds Slack notify"), _judge("accept")]
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V2, "user_id": "user-abc"},
        )

    assert resp.status_code == 200
    assert resp.json()["outcome"]["accepted"] is True


@pytest.mark.asyncio
async def test_user_id_length_capped() -> None:
    backend = _ScriptedBackend([])
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/extract_from_diff",
            json={"v1": _V1, "v2": _V2, "user_id": "x" * 129},
        )

    assert resp.status_code == 422
