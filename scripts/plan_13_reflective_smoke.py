"""PLAN_13 PR-D smoke — A/B compare /v1/policy/extract vs /v1/policy/extract_reflective.

Mirrors `phase_d_vision_smoke.py`'s shape so the two scripts feel
familiar side-by-side. Key differences from Phase D:

  - Two endpoints under test, not two modes (text/vision). Each
    selected chunk is sent to /v1/policy/extract once and to
    /v1/policy/extract_reflective once.
  - Default mode is text-only; pass --vision to send the chunk's
    image alongside the text (when document_parser produced one).
  - Reflective response carries `agent_trace.iterations` + a
    LangSmith run URL — both are surfaced in the per-row output so an
    operator can click through to the trace UI without searching.

Usage:

    AGENT_BEARER_TOKEN=$(...) python scripts/plan_13_reflective_smoke.py [opts]

Options:

    --sample N             chunk count to sample, deterministic (default 5)
    --vision               include image data URLs alongside text
    --max-iter N           reflective max_iter (default 2)
    --domain D             chunk domain hint (default "other")
    --endpoint URL         override Modal endpoint
    --out PATH             write per-chunk results as NDJSON
    --timeout SEC          per-call HTTP timeout (default 600s for
                           reflective which can run extract+judge+extract)

Output: stdout summary table + LangSmith run URLs + recall/latency
deltas. NDJSON contains the full agent_trace per reflective call so a
later analysis pass can dig into per-iteration concerns.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

import httpx

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_ENDPOINT = (
    "https://dhwang0803--auto-workflow-agent-agentservice-fastapi.modal.run"
)
FIXTURE = REPO / "AI_Agent" / "tests" / "fixtures" / "gitlab_handbook_excerpt.pdf"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample", type=int, default=5, help="chunk count to test")
    p.add_argument("--vision", action="store_true", help="send image alongside text")
    p.add_argument("--max-iter", type=int, default=2, help="reflective max_iter")
    p.add_argument("--domain", default="other", help="chunk domain hint")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--out", type=pathlib.Path, default=None)
    p.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="per-call HTTP timeout (reflective can take longer than single-shot)",
    )
    return p.parse_args()


def select_indices(total: int, sample: int) -> list[int]:
    """Same deterministic spacing as phase_d_vision_smoke so a chunk
    sampled at index i in one smoke matches index i in the other.
    """
    if sample >= total:
        return list(range(total))
    if sample <= 1:
        return [0]
    step = (total - 1) / (sample - 1)
    return [int(round(i * step)) for i in range(sample)]


def call_single_shot(
    client: httpx.Client,
    *,
    endpoint: str,
    bearer: str,
    chunk_text: str,
    domain: str,
    images: list[str] | None,
) -> dict:
    body: dict = {"chunk": chunk_text, "domain": domain}
    if images:
        body["images"] = images
    started = time.time()
    try:
        r = client.post(
            f"{endpoint}/v1/policy/extract",
            headers={"Authorization": f"Bearer {bearer}"},
            json=body,
        )
    except httpx.HTTPError as exc:
        return {
            "status": None,
            "latency_s": time.time() - started,
            "candidate_count": None,
            "error": f"http: {type(exc).__name__}: {exc}",
        }
    elapsed = time.time() - started
    info: dict = {"status": r.status_code, "latency_s": elapsed}
    try:
        body_json = r.json()
    except Exception:  # noqa: BLE001
        info["candidate_count"] = None
        info["error"] = f"non-json body: {r.text[:200]}"
        return info
    if r.status_code != 200:
        info["candidate_count"] = None
        info["error"] = body_json.get("detail", body_json)
        return info
    candidates = body_json.get("candidates") or []
    info["candidate_count"] = len(candidates)
    info["error"] = None
    return info


def call_reflective(
    client: httpx.Client,
    *,
    endpoint: str,
    bearer: str,
    chunk_text: str,
    domain: str,
    images: list[str] | None,
    max_iter: int,
) -> dict:
    body: dict = {
        "chunk": chunk_text,
        "domain": domain,
        "max_iter": max_iter,
    }
    if images:
        body["images"] = images
    started = time.time()
    try:
        r = client.post(
            f"{endpoint}/v1/policy/extract_reflective",
            headers={"Authorization": f"Bearer {bearer}"},
            json=body,
        )
    except httpx.HTTPError as exc:
        return {
            "status": None,
            "latency_s": time.time() - started,
            "candidate_count": None,
            "iterations": None,
            "reason": None,
            "langsmith_url": None,
            "agent_trace": None,
            "error": f"http: {type(exc).__name__}: {exc}",
        }
    elapsed = time.time() - started
    info: dict = {"status": r.status_code, "latency_s": elapsed}
    try:
        body_json = r.json()
    except Exception:  # noqa: BLE001
        info["candidate_count"] = None
        info["error"] = f"non-json body: {r.text[:200]}"
        return info
    if r.status_code != 200:
        info["candidate_count"] = None
        info["iterations"] = None
        info["reason"] = None
        info["langsmith_url"] = None
        info["agent_trace"] = None
        info["error"] = body_json.get("detail", body_json)
        return info
    candidates = body_json.get("candidates") or []
    trace = body_json.get("agent_trace") or {}
    info["candidate_count"] = len(candidates)
    info["iterations"] = len(trace.get("iterations") or [])
    info["reason"] = trace.get("reason")
    info["langsmith_url"] = body_json.get("langsmith_url")
    # Keep the full trace only for --out NDJSON; stdout stays scannable.
    info["agent_trace"] = trace
    info["error"] = None
    return info


def main() -> int:
    args = parse_args()

    bearer = os.environ.get("AGENT_BEARER_TOKEN", "")
    if not bearer:
        print(
            "AGENT_BEARER_TOKEN env var missing -- run via "
            "`AGENT_BEARER_TOKEN=$(gcloud secrets versions access ...) "
            "python scripts/plan_13_reflective_smoke.py`",
            file=sys.stderr,
        )
        return 2

    if not FIXTURE.exists():
        print(f"fixture missing: {FIXTURE}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(REPO / "AI_Agent"))
    from app.services.document_parser import parse_document  # noqa: E402

    chunks = parse_document(FIXTURE.read_bytes(), "application/pdf")
    if not chunks:
        print("fixture produced no chunks", file=sys.stderr)
        return 1
    indices = select_indices(len(chunks), args.sample)
    selected = [chunks[i] for i in indices]

    print(f"# PLAN_13 reflective smoke -- endpoint={args.endpoint}", file=sys.stderr)
    print(
        f"# fixture: {len(chunks)} chunks parsed, sampling {len(selected)} "
        f"at indices {indices}",
        file=sys.stderr,
    )
    print(
        f"# vision={args.vision} max_iter={args.max_iter}",
        file=sys.stderr,
    )
    print(file=sys.stderr)

    print(
        f"{'idx':>4} {'mode':>11} {'len':>5} {'status':>6} {'lat_s':>7} "
        f"{'cands':>5} {'iters':>5} {'reason':>18} note"
    )

    results: list[dict] = []

    with httpx.Client(timeout=args.timeout) as client:
        for chunk in selected:
            images = [chunk.image] if args.vision and chunk.image else None
            if args.vision and not chunk.image:
                print(
                    f"{chunk.index:>4} {'-':>11} {len(chunk.text):>5} "
                    f"{'skip':>6} {'-':>7} {'-':>5} {'-':>5} {'-':>18} "
                    f"chunk has no image"
                )
                continue

            single = call_single_shot(
                client,
                endpoint=args.endpoint,
                bearer=bearer,
                chunk_text=chunk.text,
                domain=args.domain,
                images=images,
            )
            row_single = {
                "chunk_index": chunk.index,
                "mode": "single-shot",
                "chunk_len": len(chunk.text),
                **single,
            }
            results.append(row_single)
            print(
                f"{chunk.index:>4} {'single-shot':>11} {len(chunk.text):>5} "
                f"{single['status'] if single['status'] else 'ERR':>6} "
                f"{single['latency_s']:>7.1f} "
                f"{single.get('candidate_count') if single.get('candidate_count') is not None else '-':>5} "
                f"{'-':>5} {'-':>18} "
                f"{(single.get('error') or 'ok')[:60]}"
            )

            refl = call_reflective(
                client,
                endpoint=args.endpoint,
                bearer=bearer,
                chunk_text=chunk.text,
                domain=args.domain,
                images=images,
                max_iter=args.max_iter,
            )
            row_refl = {
                "chunk_index": chunk.index,
                "mode": "reflective",
                "chunk_len": len(chunk.text),
                **refl,
            }
            results.append(row_refl)
            print(
                f"{chunk.index:>4} {'reflective':>11} {len(chunk.text):>5} "
                f"{refl['status'] if refl['status'] else 'ERR':>6} "
                f"{refl['latency_s']:>7.1f} "
                f"{refl.get('candidate_count') if refl.get('candidate_count') is not None else '-':>5} "
                f"{refl.get('iterations') if refl.get('iterations') is not None else '-':>5} "
                f"{(refl.get('reason') or '-'):>18} "
                f"{(refl.get('error') or refl.get('langsmith_url') or 'ok')[:60]}"
            )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            for row in results:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"# wrote {len(results)} rows to {args.out}", file=sys.stderr)

    # A/B summary
    single_rows = [
        r for r in results if r["mode"] == "single-shot" and r.get("status") == 200
    ]
    refl_rows = [
        r for r in results if r["mode"] == "reflective" and r.get("status") == 200
    ]
    if single_rows and refl_rows:
        s_lat = sum(r["latency_s"] for r in single_rows) / len(single_rows)
        r_lat = sum(r["latency_s"] for r in refl_rows) / len(refl_rows)
        s_cands = sum(r["candidate_count"] for r in single_rows)
        r_cands = sum(r["candidate_count"] for r in refl_rows)
        print(file=sys.stderr)
        print(
            f"# summary: single-shot avg_lat={s_lat:5.1f}s  total_cands={s_cands}",
            file=sys.stderr,
        )
        print(
            f"# summary: reflective  avg_lat={r_lat:5.1f}s  total_cands={r_cands}",
            file=sys.stderr,
        )
        if s_lat > 0:
            print(
                f"# summary: reflective/single-shot lat_ratio={r_lat / s_lat:.2f}x  "
                f"recall_ratio={r_cands / s_cands if s_cands else 0:.2f}x  "
                f"recall_delta={r_cands - s_cands:+d}",
                file=sys.stderr,
            )

    # LangSmith roll-up — handy for the demo + post-mortem
    urls = [
        r.get("langsmith_url")
        for r in refl_rows
        if r.get("langsmith_url")
    ]
    if urls:
        print(file=sys.stderr)
        print(f"# LangSmith trace URLs ({len(urls)}):", file=sys.stderr)
        for u in urls:
            print(f"#   {u}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
