"""Live smoke for /v1/policy/extract using the handbook fixture.

Parses tests/fixtures/gitlab_handbook_excerpt.pdf into chunks via
document_parser, then POSTs each chunk to the deployed Modal endpoint and
prints per-chunk candidate counts. The intent is to confirm the W3-2/3/4
bundle survives a real network round-trip end-to-end.

Env:
    AGENT_URL   — full Modal URL, e.g.
                  https://<user>--auto-workflow-agent-agentservice-fastapi.modal.run
    AGENT_BEARER_TOKEN — bearer for /v1/* (read from GCP Secret Manager
                  agent-bearer-token-staging in the runbook)

Usage:
    AGENT_URL=... AGENT_BEARER_TOKEN=... python scripts/smoke_handbook_policy_extract.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

from app.services.document_parser import parse_document

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gitlab_handbook_excerpt.pdf"
TIMEOUT = 300.0  # generous — first call is cold-start + first BGE-M3 download


def main() -> int:
    url = os.environ.get("AGENT_URL", "").rstrip("/")
    token = os.environ.get("AGENT_BEARER_TOKEN", "")
    if not url or not token:
        print("AGENT_URL and AGENT_BEARER_TOKEN must be set", file=sys.stderr)
        return 2

    pdf = FIXTURE.read_bytes()
    chunks = parse_document(pdf, "application/pdf")
    print(f"fixture: {FIXTURE.name} ({len(pdf):,} bytes) → {len(chunks)} chunks")
    print(f"endpoint: {url}/v1/policy/extract")
    print()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    total_candidates = 0
    needs_clarification = 0
    failures: list[tuple[int, str]] = []

    with httpx.Client(timeout=TIMEOUT) as client:
        for c in chunks:
            t0 = time.time()
            try:
                r = client.post(
                    f"{url}/v1/policy/extract",
                    headers=headers,
                    json={"chunk": c.text, "domain": "other"},
                )
            except httpx.HTTPError as exc:
                failures.append((c.index, repr(exc)))
                print(f"chunk #{c.index:>2}  ERROR  {exc!r}")
                continue
            elapsed = time.time() - t0

            if r.status_code != 200:
                # 502 detail is a dict carrying error/raw_len/raw — see
                # main.py policy_extract handler. Print error + raw_len so
                # the smoke surfaces parse failures without a Modal log hop.
                failures.append((c.index, f"HTTP {r.status_code}: {r.text[:200]}"))
                print(f"chunk #{c.index:>2}  HTTP {r.status_code}  ({elapsed:.1f}s)")
                try:
                    body = r.json().get("detail", {})
                    print(f"    error:   {body.get('error', '')!r}  raw_len={body.get('raw_len', 0)}")
                except Exception:
                    print(f"    text:    {r.text[:600]!r}")
                continue

            body = r.json()
            cands = body.get("candidates", [])
            n = len(cands)
            total_candidates += n
            nc = sum(1 for x in cands if x.get("needs_clarification"))
            needs_clarification += nc
            print(f"chunk #{c.index:>2}  {n:>2} candidate(s)  ({elapsed:>5.1f}s)  needs_clarif={nc}")
            for x in cands[:2]:
                name = x.get("name", "")
                cond = (x.get("condition") or "")[:60]
                act = (x.get("action") or "")[:60]
                print(f"    - {name!r}  if: {cond!r}  then: {act!r}")

    print()
    print(f"summary: {total_candidates} total candidates "
          f"({needs_clarification} needs_clarification) "
          f"across {len(chunks)} chunks, {len(failures)} failures")
    if failures:
        for idx, msg in failures:
            print(f"  chunk #{idx}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
