"""Live smoke for /v1/policy/extract using the handbook fixture.

Parses tests/fixtures/gitlab_handbook_excerpt.pdf into chunks via
document_parser, then POSTs each chunk to the deployed Modal endpoint and
prints per-chunk candidate counts. The intent is to confirm the W3-2/3/4
bundle survives a real network round-trip end-to-end.

The Phase 1 instrumentation (EXPERIMENT_reasoning_trace.md §5) lets the
script drive the recall-vs-latency sweep without redeploying the agent.
The four knobs below all default to the production behavior.

Env:
    AGENT_URL   — full Modal URL, e.g.
                  https://<user>--auto-workflow-agent-agentservice-fastapi.modal.run
    AGENT_BEARER_TOKEN — bearer for /v1/* (read from GCP Secret Manager
                  agent-bearer-token-staging in the runbook)

Usage:
    AGENT_URL=... AGENT_BEARER_TOKEN=... \\
        python scripts/smoke_handbook_policy_extract.py [--strictness ...] \\
        [--enable-thinking] [--temperature 0.4] [--include-raw]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

from app.services.document_parser import parse_document
from app.services.policy_extract import _system_prompt

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gitlab_handbook_excerpt.pdf"
TIMEOUT = 300.0  # generous — first call is cold-start + first BGE-M3 download


# Strictness variants used by Phase 2 sweeps. "default" sends no override
# so the server-built prompt is used; aggressive/lenient append a bias
# clause to the same default. The ladder is intentionally narrow — three
# points cover the conservative / neutral / inclusive corners we want.
def _aggressive_prompt(domain: str) -> str:
    return _system_prompt(domain) + (
        "\n\n## Bias\n"
        "When in doubt, INCLUDE the candidate with needs_clarification=true "
        "rather than dropping it. The downstream review UI can prune false "
        "positives, but cannot recover policies that were never extracted. "
        "Aim for high recall over precision."
    )


def _lenient_prompt(domain: str) -> str:
    return _system_prompt(domain) + (
        "\n\n## Bias\n"
        "Use needs_clarification=true generously — any candidate where you "
        "are not 100% certain of the action's exact form should carry that "
        "flag with a clarification_hint asking for the precise rule. Always "
        "favor surfacing a candidate with a clarification request over "
        "silently dropping it."
    )


STRICTNESS = {
    "default": None,
    "aggressive": _aggressive_prompt,
    "lenient": _lenient_prompt,
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--strictness",
        choices=sorted(STRICTNESS.keys()),
        default="default",
        help="Prompt-strictness variant; 'default' sends no system_prompt_override.",
    )
    p.add_argument(
        "--enable-thinking",
        action="store_true",
        help="Forward enable_thinking=True (re-enables Gemma 4 reasoning trace).",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Override sampling temperature (default 0.0 = greedy).",
    )
    p.add_argument(
        "--domain",
        default="other",
        help="Domain hint sent in the request body (default: other).",
    )
    p.add_argument(
        "--include-raw",
        action="store_true",
        help="Ask the server to echo the LLM's raw response per chunk.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    url = os.environ.get("AGENT_URL", "").rstrip("/")
    token = os.environ.get("AGENT_BEARER_TOKEN", "")
    if not url or not token:
        print("AGENT_URL and AGENT_BEARER_TOKEN must be set", file=sys.stderr)
        return 2

    pdf = FIXTURE.read_bytes()
    chunks = parse_document(pdf, "application/pdf")
    prompt_builder = STRICTNESS[args.strictness]
    system_prompt_override = (
        prompt_builder(args.domain) if prompt_builder is not None else None
    )

    print(f"fixture:    {FIXTURE.name} ({len(pdf):,} bytes) → {len(chunks)} chunks")
    print(f"endpoint:   {url}/v1/policy/extract")
    print(
        "knobs:      "
        f"strictness={args.strictness} enable_thinking={args.enable_thinking} "
        f"temperature={args.temperature} domain={args.domain} include_raw={args.include_raw}"
    )
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
            body_payload: dict[str, object] = {
                "chunk": c.text,
                "domain": args.domain,
            }
            if system_prompt_override is not None:
                body_payload["system_prompt_override"] = system_prompt_override
            if args.enable_thinking:
                body_payload["enable_thinking"] = True
            if args.temperature is not None:
                body_payload["temperature"] = args.temperature
            if args.include_raw:
                body_payload["include_raw"] = True

            t0 = time.time()
            try:
                r = client.post(
                    f"{url}/v1/policy/extract",
                    headers=headers,
                    json=body_payload,
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
            raw_len = len(body.get("raw") or "") if args.include_raw else None
            raw_suffix = f"  raw_len={raw_len}" if raw_len is not None else ""
            print(
                f"chunk #{c.index:>2}  {n:>2} candidate(s)  ({elapsed:>5.1f}s)  "
                f"needs_clarif={nc}{raw_suffix}"
            )
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
