"""HTTP route tests for /v1/policy/extract_reflective.

PR-β rebuilt the underlying agent on the ReAct loop (ADR-024). The
route's wire shape is unchanged, so the assertions here still target
status codes, envelope, auth, validation, langsmith_run_id surfacing,
and that `images` reach `backend.complete`. The mock backend is the
same 3-bucket pattern used by `test_policy_extract_agent_loop.py`:
agent / extract / judge prompts route into separate response queues.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


_AGENT_PROMPT_PREFIX = "You are an extraction agent"
_EXTRACT_PROMPT_PREFIX = "You are the policy extractor"
_JUDGE_PROMPT_PREFIX = "You are a critic for a policy-extraction step"


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


def _extract_payload(*candidates: dict[str, Any]) -> str:
    return json.dumps({"candidates": list(candidates)})


def _agent_call(name: str, args: dict | None = None) -> str:
    body = json.dumps(args or {})
    return f"<tool_call name=\"{name}\">\n{body}\n</tool_call>"


def _agent_finish(drafts: list[dict[str, Any]]) -> str:
    return f"<finish>\n{json.dumps({'drafts': drafts})}\n</finish>"


class _SequencedBackend:
    """3-bucket sequenced backend.

    `agent` queue feeds the ReAct outer loop, `extract` feeds
    `services.policy_extract`, `judge` feeds `agents.judge`. Calls are
    dispatched by system-prompt prefix; an unrecognized prefix raises.
    Bucket counters (`agent_calls` / `extract_calls` / `judge_calls`)
    let tests assert "the extractor was called once" etc. without
    bookkeeping.

    `last_images` reports the images keyword the most recent EXTRACT
    call received — agent-loop and judge calls don't pass images, so
    this isolates the contract under test in `test_images_field_*`.
    """

    def __init__(
        self,
        *,
        agent: list[str] | None = None,
        extract: list[str] | None = None,
        judge: list[str] | None = None,
    ) -> None:
        self._agent = list(agent or [])
        self._extract = list(extract or [])
        self._judge = list(judge or [])
        self.agent_calls = 0
        self.extract_calls = 0
        self.judge_calls = 0
        self.last_images: list[str] | None = None

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        del user_message, max_tokens

        if system.startswith(_AGENT_PROMPT_PREFIX):
            if self.agent_calls >= len(self._agent):
                raise AssertionError(
                    f"agent backend exhausted at agent_calls={self.agent_calls}"
                )
            out = self._agent[self.agent_calls]
            self.agent_calls += 1
            return out

        if system.startswith(_EXTRACT_PROMPT_PREFIX):
            if self.extract_calls >= len(self._extract):
                raise AssertionError(
                    f"extract backend exhausted at extract_calls={self.extract_calls}"
                )
            out = self._extract[self.extract_calls]
            self.last_images = images
            self.extract_calls += 1
            return out

        if system.startswith(_JUDGE_PROMPT_PREFIX):
            if self.judge_calls < len(self._judge):
                out = self._judge[self.judge_calls]
            else:
                out = '{"missed": []}'
            self.judge_calls += 1
            return out

        raise AssertionError(
            f"unrecognized system prompt prefix: {system[:80]!r}"
        )

    async def stream(self, **_):  # noqa: ANN001, ANN003
        if False:  # pragma: no cover
            yield ""

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _converge_in_one(cand: dict[str, Any]) -> _SequencedBackend:
    """Common shape: agent does extract → evaluate → finish, no retry."""
    return _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_finish([cand]),
        ],
        extract=[_extract_payload(cand)],
    )


# --- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_200_with_final_candidates_and_full_trace() -> None:
    backend = _converge_in_one(_candidate())
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

    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["name"] == "approve-large-refunds"

    trace = body["agent_trace"]
    assert trace["terminated"] is True
    assert trace["reason"] == "converge"
    assert len(trace["iterations"]) == 1
    iter1 = trace["iterations"][0]
    assert iter1["eval"] is not None
    assert iter1["eval"]["decision"] == "converge"
    assert iter1["prompt_hint"] == ""

    assert body["langsmith_run_id"] is None


# --- multi-iter trace surfaces ------------------------------------------


@pytest.mark.asyncio
async def test_retry_then_converge_records_both_iterations_in_trace() -> None:
    """Iter 1 returns empty drafts on a chunk full of policy keywords —
    deterministic rule 1 returns retry → agent re-extracts with hint →
    iter 2 succeeds → converge.
    """
    cand = _candidate()
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            _agent_call("evaluate_coverage"),
            _agent_call(
                "extract_policies",
                {"hint": "policy-imperative chunk had no candidates"},
            ),
            _agent_call("evaluate_coverage"),
            _agent_finish([cand]),
        ],
        extract=[
            _extract_payload(),       # iter 1 empty (rule 1 fires)
            _extract_payload(cand),   # iter 2 recovers
        ],
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
    assert trace["iterations"][0]["eval"]["decision"] == "retry"
    assert trace["iterations"][0]["drafts"] == []
    assert trace["iterations"][0]["prompt_hint"] == ""
    assert trace["iterations"][1]["eval"]["decision"] == "converge"
    assert trace["iterations"][1]["prompt_hint"] != ""

    assert len(body["candidates"]) == 1


# --- request validation ---------------------------------------------------


@pytest.mark.asyncio
async def test_returns_422_on_invalid_max_iter() -> None:
    backend = _converge_in_one(_candidate())
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp_low = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "x", "max_iter": 0},
        )
        resp_high = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "x", "max_iter": 10},
        )

    assert resp_low.status_code == 422
    assert resp_high.status_code == 422


@pytest.mark.asyncio
async def test_returns_422_on_empty_chunk() -> None:
    backend = _converge_in_one(_candidate())
    app = create_app(backend_override=backend)
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
    """Same envelope as `/v1/policy/extract`. The first extraction's
    LLM body is unparseable → `PolicyExtractParseError` propagates from
    the agent driver → route returns 502 with the standard
    `error/raw_len/raw` body.
    """
    backend = _SequencedBackend(
        agent=[
            _agent_call("extract_policies"),
            # The agent would receive an obs error and might try to
            # finish; provide one finish so the agent loop terminates
            # cleanly. The driver re-raises the captured parse error
            # afterwards regardless.
            _agent_finish([]),
        ],
        extract=["this is not json at all"],
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "anything", "domain": "other"},
        )

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "error" in detail
    assert "raw_len" in detail
    assert "raw" in detail


# --- bearer auth ---------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_gated_by_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-x")
    backend = _converge_in_one(_candidate())
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp_missing = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "Refunds must be approved."},
        )
        resp_wrong = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "Refunds must be approved."},
            headers={"Authorization": "Bearer wrong"},
        )
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
    """`images` must reach `backend.complete` on the EXTRACT call —
    Phase D's smoke depends on this and the reflective endpoint must
    preserve the contract through the agent loop refactor.
    """
    backend = _converge_in_one(_candidate())
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


# --- LangSmith run_id surfacing -----------------------------------------


@pytest.mark.asyncio
async def test_langsmith_run_id_populated_when_tracing_envs_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BOTH LANGCHAIN_TRACING_V2=true AND LANGCHAIN_API_KEY are
    set, the response carries the UUID the route minted. The client
    pastes it into LangSmith's UI search to navigate to the trace tree.
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "fake-key-for-test")

    backend = _converge_in_one(_candidate())
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "Refunds must be approved.", "max_iter": 1},
        )

    assert resp.status_code == 200
    run_id = resp.json()["langsmith_run_id"]
    assert run_id is not None

    import uuid as _uuid

    _uuid.UUID(run_id)


@pytest.mark.asyncio
async def test_langsmith_run_id_null_when_only_master_switch_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If LANGCHAIN_TRACING_V2 is on but the API key is empty (e.g.,
    Modal Secret not yet synced), run_id stays null. We don't surface
    an id that points to a run that wasn't actually ingested.
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "")

    backend = _converge_in_one(_candidate())
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "Refunds must be approved.", "max_iter": 1},
        )

    assert resp.status_code == 200
    assert resp.json()["langsmith_run_id"] is None


# --- max_iter=1 single-pass equivalence ----------------------------------


@pytest.mark.asyncio
async def test_max_iter_1_runs_a_single_pass_via_route() -> None:
    backend = _converge_in_one(_candidate())
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
    assert backend.extract_calls == 1
    assert len(body["agent_trace"]["iterations"]) == 1
    assert len(body["candidates"]) == 1
