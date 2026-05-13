"""HTTP route tests for /v1/policy/extract_reflective.

PR-β rebuilt the underlying agent on the ReAct loop (ADR-024). The
route's wire shape is unchanged, so the assertions here still target
status codes, envelope, auth, validation, langsmith_run_id surfacing,
and that `images` reach `backend.complete`. The mock backend is the
same 3-bucket pattern used by `test_policy_extract_agent_loop.py`:
agent / extract / judge prompts route into separate response queues.

PR-γ adds personalization wiring — the route loads a
`PersonalMemoryPool` once per request and threads it into the agent.
The tests below cover the four `user_id` paths (none / set with file /
set without file / file disabled by config), and verify the
`search_personal_skills` tool only appears in the system prompt when
there's something to find.

PLAN_14 PR-F closes the privacy guard at the route boundary: with two
populated files in the same memory dir, a request scoped to one user
must never expose the other's skill text in the search obs. Unit tests
on `PersonalMemoryPool` already prove the loader picks the right file
by path; the cross-user route test below proves the wiring all the way
through the agent loop preserves that isolation, so a future regression
that (say) caches a pool across requests would surface here.
"""
from __future__ import annotations

import json
from pathlib import Path
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
    return json.dumps(
        {"action": "tool_call", "name": name, "args": args or {}}
    )


def _agent_finish(drafts: list[dict[str, Any]]) -> str:
    return json.dumps({"action": "finish", "result": {"drafts": drafts}})


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


# --- personalization (PR-γ memory) --------------------------------------


def _populated_skill_record() -> dict[str, Any]:
    return {
        "id": "past-edit-1",
        "condition": {"text": "Refunds over $500"},
        "action": {"text": "Manager approval"},
        "suggestion_hash": "hash-1",
        # 1024-dim to match `StubEmbeddingBackend.dimension` — the
        # search path drops dim mismatches, so a real-shape vector is
        # required for the entry to be findable.
        "embedding": [1.0] + [0.0] * 1023,
        "source": "hitl_edit",
        "first_observed_at": "2026-05-01T00:00:00Z",
        "active": True,
    }


def _write_user_memory(base_dir: Path, user_id: str, skills: list[dict]) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / f"{user_id}.json").write_text(
        json.dumps({"user_id": user_id, "skills": skills}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_user_id_with_populated_memory_routes_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end through the route: memory dir set, user_id passed,
    file present with one entry. The agent must run search → extract
    → evaluate → finish, the trace shows the hint that flowed from
    the memory match, and the response stays a 200."""
    monkeypatch.setenv("PERSONAL_MEMORY_DIR", str(tmp_path))
    _write_user_memory(tmp_path, "alice", [_populated_skill_record()])

    cand = _candidate()
    backend = _SequencedBackend(
        agent=[
            _agent_call(
                "search_personal_skills",
                {"query": "refund approvals", "k": 3},
            ),
            _agent_call(
                "extract_policies",
                {"hint": "from prior approval pattern"},
            ),
            _agent_call("evaluate_coverage"),
            _agent_finish([cand]),
        ],
        extract=[_extract_payload(cand)],
    )
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={
                "chunk": "Refunds over $500 must be approved by a manager.",
                "user_id": "alice",
                "max_iter": 2,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["candidates"]) == 1
    # The hint surfaced through the trace — this is the operator-visible
    # signal that memory shaped iteration 1.
    assert body["agent_trace"]["iterations"][0]["prompt_hint"] == (
        "from prior approval pattern"
    )


@pytest.mark.asyncio
async def test_user_id_with_no_file_yields_cold_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Memory dir is configured but the user has not edited anything
    yet — file missing → empty pool → search tool not registered →
    agent runs the baseline extract path. This is the GitLab-smoke
    +3-cand regression guard expressed at the route boundary."""
    monkeypatch.setenv("PERSONAL_MEMORY_DIR", str(tmp_path))

    cand = _candidate()
    # Script the baseline 3-turn shape — no search call. If memory had
    # leaked in, the agent script would diverge and the assertion below
    # on `extract_calls == 1` would still pass but `agent_calls == 3`
    # would fail (4 expected).
    backend = _converge_in_one(cand)
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={
                "chunk": "Refunds must be approved by a manager.",
                "user_id": "fresh-user",
            },
        )

    assert resp.status_code == 200
    assert backend.agent_calls == 3
    assert backend.extract_calls == 1


@pytest.mark.asyncio
async def test_user_id_none_yields_cold_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Anonymous request — memory dir is configured (could be in prod)
    but the wire payload omits `user_id`. The pool is empty by design,
    no search tool registered."""
    monkeypatch.setenv("PERSONAL_MEMORY_DIR", str(tmp_path))
    # Even a populated file for some OTHER user must not leak into the
    # anonymous request. Write one to prove isolation.
    _write_user_memory(tmp_path, "alice", [_populated_skill_record()])

    backend = _converge_in_one(_candidate())
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={"chunk": "Refunds must be approved.", "user_id": None},
        )

    assert resp.status_code == 200
    assert backend.agent_calls == 3
    assert backend.extract_calls == 1


@pytest.mark.asyncio
async def test_personal_memory_dir_empty_disables_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The feature flag — empty `PERSONAL_MEMORY_DIR` short-circuits
    the loader before it touches the filesystem. Even with a real
    `user_id` on the wire, the pool is empty and the agent runs the
    baseline shape."""
    monkeypatch.setenv("PERSONAL_MEMORY_DIR", "")

    backend = _converge_in_one(_candidate())
    app = create_app(backend_override=backend)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/policy/extract_reflective",
            json={
                "chunk": "Refunds must be approved.",
                "user_id": "alice",
            },
        )

    assert resp.status_code == 200
    assert backend.agent_calls == 3
    assert backend.extract_calls == 1


# --- PLAN_14 PR-F: cross-user isolation guard at the route boundary ----


class _ObsCapturingBackend(_SequencedBackend):
    """Sequenced backend that snapshots the user_message of every agent
    turn after the first one.

    The agent loop appends each tool obs back into the running transcript
    that becomes the next call's `user_message`. So everything the model
    "saw" from `search_personal_skills` is observable here — which is
    exactly the surface the cross-user leak guard cares about.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.agent_user_messages: list[str] = []

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        if system.startswith(_AGENT_PROMPT_PREFIX):
            self.agent_user_messages.append(user_message)
        return await super().complete(
            system=system,
            user_message=user_message,
            max_tokens=max_tokens,
            images=images,
        )


@pytest.mark.asyncio
async def test_cross_user_memory_files_do_not_leak_via_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Alice and Bob both have populated memory files in the same dir.
    The route is called twice — once as Alice, once as Bob — and each
    request's `search_personal_skills` obs must only surface the
    invoking user's pattern text.

    Distinctive markers (`ALICE-MARKER-...` / `BOB-MARKER-...`) live in
    `condition.text` so the assertion can be a simple substring check
    on the transcript that flows into the agent's second turn.
    """
    monkeypatch.setenv("PERSONAL_MEMORY_DIR", str(tmp_path))

    alice_marker = "ALICE-MARKER-refund-escalation"
    bob_marker = "BOB-MARKER-shipping-cutoff"

    alice_skill = {
        "id": "alice-skill",
        "condition": {"text": alice_marker},
        "action": {"text": "Manager approval"},
        "suggestion_hash": "alice-hash",
        # Orthogonal unit vectors so each user's stored embedding is the
        # nearest neighbour to itself and no other entry can outscore it.
        "embedding": [1.0] + [0.0] * 1023,
        "source": "hitl_edit",
        "first_observed_at": "2026-05-01T00:00:00Z",
        "active": True,
    }
    bob_skill = {
        "id": "bob-skill",
        "condition": {"text": bob_marker},
        "action": {"text": "Hold shipment"},
        "suggestion_hash": "bob-hash",
        "embedding": [0.0, 1.0] + [0.0] * 1022,
        "source": "hitl_edit",
        "first_observed_at": "2026-05-02T00:00:00Z",
        "active": True,
    }

    _write_user_memory(tmp_path, "alice", [alice_skill])
    _write_user_memory(tmp_path, "bob", [bob_skill])

    cand = _candidate()
    # Same scripted shape works for both users — the script doesn't
    # know whose memory it's searching, only that exactly one search
    # call happens before extract → evaluate → finish.
    def _build_backend() -> _ObsCapturingBackend:
        return _ObsCapturingBackend(
            agent=[
                _agent_call(
                    "search_personal_skills",
                    {"query": "refund approvals", "k": 3},
                ),
                _agent_call(
                    "extract_policies",
                    {"hint": "from prior approval pattern"},
                ),
                _agent_call("evaluate_coverage"),
                _agent_finish([cand]),
            ],
            extract=[_extract_payload(cand)],
        )

    # Fresh app per request — the route-level isolation guard would be
    # uninteresting if a shared FastAPI app cached the pool between
    # calls. Each app instance hits `PersonalMemoryPool.load()` exactly
    # once, with the user_id from the request body.
    alice_backend = _build_backend()
    alice_app = create_app(backend_override=alice_backend)
    async with AsyncClient(
        transport=ASGITransport(app=alice_app), base_url="http://test"
    ) as c:
        alice_resp = await c.post(
            "/v1/policy/extract_reflective",
            json={
                "chunk": "Refunds over $500 must be approved.",
                "user_id": "alice",
                "max_iter": 2,
            },
        )

    bob_backend = _build_backend()
    bob_app = create_app(backend_override=bob_backend)
    async with AsyncClient(
        transport=ASGITransport(app=bob_app), base_url="http://test"
    ) as c:
        bob_resp = await c.post(
            "/v1/policy/extract_reflective",
            json={
                "chunk": "Refunds over $500 must be approved.",
                "user_id": "bob",
                "max_iter": 2,
            },
        )

    assert alice_resp.status_code == 200
    assert bob_resp.status_code == 200

    # Each agent turn after the first contains the cumulative
    # transcript, including every prior tool obs. Join them so the
    # assertion is independent of which turn surfaces the search obs.
    alice_transcript = "\n".join(alice_backend.agent_user_messages[1:])
    bob_transcript = "\n".join(bob_backend.agent_user_messages[1:])

    # Alice saw her own marker, never Bob's.
    assert alice_marker in alice_transcript
    assert bob_marker not in alice_transcript

    # Bob saw his own marker, never Alice's.
    assert bob_marker in bob_transcript
    assert alice_marker not in bob_transcript


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
