"""Live smoke for the closed personalization loop (PLAN_14 §4.8).

Hits the deployed Modal endpoint to verify the round-trip:

  1. extract_from_diff (USER_A)        → propose+judge accepts a candidate
  2. memory/upsert     (USER_A)        → row written to the user's JSON file
  3. extract_reflective (USER_A)       → request lands; LangSmith trace minted
  4. extract_reflective (USER_B)       → other user's request stays isolated
  5. summary + LangSmith run_ids       → operator pastes them into the UI to
                                         confirm `search_personal_skills` ran
                                         only for USER_A

The script bypasses API_Server / DB so the smoke depends only on AI_Agent
+ Modal Volume — the closed loop the unit tests cannot exercise without
the live endpoint. PR-G's wire (workflow_id-only extract, DB-backed
candidate persistence) is locked in by `API_Server/tests/test_personalization.py`.

Env:
    AGENT_URL              — full Modal URL,
                             e.g. https://<user>--auto-workflow-agent-agentservice-fastapi.modal.run
    AGENT_BEARER_TOKEN     — bearer token for /v1/* (GCP Secret Manager
                             agent-bearer-token-staging in the runbook)

Usage (Windows PowerShell):
    $env:PYTHONUTF8="1"
    $env:AGENT_URL="https://...modal.run"
    $env:AGENT_BEARER_TOKEN="..."
    python AI_Agent/scripts/plan_14_personalization_smoke.py
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Any

import httpx

# Cold-start: container boot + Gemma 4 26B mmap + first BGE-M3 download
# can push past 180s on a fresh deploy. 600s ceiling matches the smoke
# pattern used by `smoke_handbook_policy_extract.py` for the same
# reason.
TIMEOUT = 600.0

# Two run-tagged user IDs so reruns don't collide with prior smoke
# artifacts on the Modal Volume — the writer is idempotent on (user_id,
# skill.id) but a fresh tag also makes the LangSmith UI search trivial.
RUN_TAG = f"smoke-{uuid.uuid4().hex[:8]}"
USER_A = f"alice-{RUN_TAG}"
USER_B = f"bob-{RUN_TAG}"

# Minimal workflow pair the propose+judge agent has accepted in unit
# tests (`AI_Agent/tests/test_personalization_route.py::test_returns_200_with_outcome_diff_and_signature`).
# Keeping the same shape here means a smoke failure can be triaged
# against the unit fixture instead of a custom one.
V1: dict[str, Any] = {
    "nodes": [{"id": "fetch", "type": "http_request", "config": {}}],
    "edges": [],
}
V2: dict[str, Any] = {
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

# Tangentially-related policy chunk so the reflective agent has an
# excuse to reach for `search_personal_skills` (the activated personal
# skill is "post to slack on …", the chunk is about credential
# rotation requiring channel notification).
POLICY_CHUNK = (
    "When a credential is rotated, the system MUST notify the on-call "
    "channel within 5 minutes."
)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _call(client: httpx.Client, method: str, path: str, **kw: Any) -> httpx.Response:
    """Unified call wrapper that prints HTTP error bodies inline.

    Without this an httpx.HTTPStatusError just shows the status code,
    which forces the operator to dig through Modal logs to see the
    server's detail field. We surface it on failure so the smoke output
    is self-contained.
    """
    base = os.environ["AGENT_URL"].rstrip("/")
    url = f"{base}{path}"
    r = client.request(method, url, **kw)
    if r.status_code >= 400:
        snippet = r.text[:500]
        raise SystemExit(f"{method} {path} → HTTP {r.status_code}: {snippet}")
    return r


def step_extract(client: httpx.Client, token: str) -> dict:
    """Stage 1 — propose+judge over the V1→V2 diff for USER_A."""
    r = _call(
        client,
        "POST",
        "/v1/personalization/extract_from_diff",
        headers=_headers(token),
        json={
            "v1": V1,
            "v2": V2,
            "rejected_hashes": [],
            "user_id": USER_A,
        },
    )
    body = r.json()
    if not body.get("outcome", {}).get("accepted"):
        raise SystemExit(
            f"propose+judge did not accept on a clean fixture: {body}"
        )
    return body


def step_upsert(
    client: httpx.Client, token: str, extract_body: dict
) -> dict:
    """Stage 2 — push the accepted candidate into USER_A's memory file."""
    outcome = extract_body["outcome"]
    proposal = outcome["proposal"]
    skill_id = uuid.uuid4().hex
    r = _call(
        client,
        "POST",
        "/v1/personalization/memory/upsert",
        headers=_headers(token),
        json={
            "user_id": USER_A,
            "skill": {
                "id": skill_id,
                "condition": {"text": proposal.get("hint") or ""},
                "action": {"text": "post to slack #alerts"},
                "suggestion_hash": outcome.get("suggestion_hash") or "",
                "source": "hitl_edit",
                "first_observed_at": "2026-05-14T00:00:00Z",
                "active": True,
            },
        },
    )
    body = r.json()
    if body.get("pool_size", 0) < 1:
        raise SystemExit(f"upsert did not grow the pool: {body}")
    return body


def step_reflective(
    client: httpx.Client, token: str, user_id: str
) -> dict:
    """Stage 3/4 — issue a reflective extract scoped to one user."""
    r = _call(
        client,
        "POST",
        "/v1/policy/extract_reflective",
        headers=_headers(token),
        json={
            "chunk": POLICY_CHUNK,
            "domain": "other",
            "max_iter": 2,
            "user_id": user_id,
        },
    )
    return r.json()


def main() -> int:
    url = os.environ.get("AGENT_URL", "").rstrip("/")
    token = os.environ.get("AGENT_BEARER_TOKEN", "")
    if not url or not token:
        print(
            "AGENT_URL and AGENT_BEARER_TOKEN must be set", file=sys.stderr
        )
        return 2

    print(f"== run tag: {RUN_TAG} ==")
    print(f"endpoint: {url}")
    print(f"users:    A={USER_A}")
    print(f"          B={USER_B}\n")

    failures: list[str] = []

    with httpx.Client(timeout=TIMEOUT) as client:
        print("[1/5] extract_from_diff (USER_A) — propose+judge")
        t0 = time.time()
        extract_body = step_extract(client, token)
        dt = time.time() - t0
        outcome = extract_body["outcome"]
        print(
            f"      accepted={outcome['accepted']} "
            f"hash={outcome.get('suggestion_hash')!r} "
            f"langsmith_run_id={extract_body.get('langsmith_run_id')!r} "
            f"latency={dt:.1f}s\n"
        )

        print("[2/5] memory/upsert (USER_A) — write to JSON file")
        t0 = time.time()
        upsert_body = step_upsert(client, token, extract_body)
        dt = time.time() - t0
        print(
            f"      pool_size={upsert_body['pool_size']} "
            f"embedding_source={upsert_body['embedding_source']} "
            f"latency={dt:.1f}s\n"
        )

        print(
            "[3/5] extract_reflective (USER_A) — expect search_personal_skills tool"
        )
        t0 = time.time()
        body_a = step_reflective(client, token, USER_A)
        dt = time.time() - t0
        trace_a = body_a.get("agent_trace", {})
        print(
            f"      iterations={trace_a.get('iterations') and len(trace_a['iterations'])} "
            f"reason={trace_a.get('reason')!r} "
            f"cands={len(body_a.get('candidates', []))} "
            f"langsmith_run_id={body_a.get('langsmith_run_id')!r} "
            f"latency={dt:.1f}s\n"
        )

        print(
            "[4/5] extract_reflective (USER_B) — expect personal skill NOT to surface"
        )
        t0 = time.time()
        body_b = step_reflective(client, token, USER_B)
        dt = time.time() - t0
        trace_b = body_b.get("agent_trace", {})
        print(
            f"      iterations={trace_b.get('iterations') and len(trace_b['iterations'])} "
            f"reason={trace_b.get('reason')!r} "
            f"cands={len(body_b.get('candidates', []))} "
            f"langsmith_run_id={body_b.get('langsmith_run_id')!r} "
            f"latency={dt:.1f}s\n"
        )

        # Surface checks: both routes returned a finite trace, and the
        # LangSmith run_ids differ (when tracing is on). Deep tool-call
        # inspection lives in the LangSmith UI — the unit tests already
        # lock in the file-isolation guarantee at the writer surface.
        if not trace_a.get("terminated", False):
            failures.append("USER_A reflective trace did not terminate cleanly")
        if not trace_b.get("terminated", False):
            failures.append("USER_B reflective trace did not terminate cleanly")
        run_a = body_a.get("langsmith_run_id")
        run_b = body_b.get("langsmith_run_id")
        if run_a and run_b and run_a == run_b:
            failures.append("LangSmith run_ids collided across users")

        print("[5/5] summary")
        print(
            f"      extract     run_id  : {extract_body.get('langsmith_run_id')!r}"
        )
        print(f"      USER_A refl run_id  : {run_a!r}")
        print(f"      USER_B refl run_id  : {run_b!r}")
        print(
            "      → paste the run_ids into the LangSmith UI search to "
            "confirm `search_personal_skills` ran for USER_A only."
        )

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nOK — closed-loop personalization round-trip live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
