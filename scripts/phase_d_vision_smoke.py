"""Phase D vision smoke for /v1/policy/extract.

Sets the multimodal pivot's stress test against the live Modal endpoint:
parse the gitlab handbook fixture into per-page chunks (with images, via
Phase C document_parser), then exercise text-only and vision modes on the
same chunks back-to-back so we can compare wall latency, candidate count,
and parse outcomes side by side.

Usage:

    AGENT_BEARER_TOKEN=$(...) python scripts/phase_d_vision_smoke.py [opts]

Options:
    --sample N              chunk indices to sample, deterministic (default 5)
    --mode {text,vision,both}  which path(s) to run (default both)
    --max-tokens N          model max_tokens override (default leaves the
                            service default at 4096)
    --domain D              chunk domain hint (default "other")
    --endpoint URL          override Modal endpoint
    --out PATH              write per-chunk results as NDJSON

Output: stdout summary table + optional NDJSON for downstream analysis.
The HTTP body's `candidates` list is summarized by count + first name —
full JSON only goes to --out to keep stdout readable across N x 2 calls.
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
    p.add_argument(
        "--mode",
        choices=["text", "vision", "both"],
        default="both",
        help="which path(s) to exercise per chunk",
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="override service max_tokens (default uses POLICY_EXTRACT_MAX_TOKENS=4096)",
    )
    p.add_argument("--domain", default="other", help="chunk domain hint")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--out", type=pathlib.Path, default=None)
    p.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="per-call HTTP timeout (vision @ 4096 max_tokens can take 60s+)",
    )
    return p.parse_args()


def select_indices(total: int, sample: int) -> list[int]:
    """Evenly spaced chunk indices across the document.

    Deterministic: re-running with the same `--sample` always picks the
    same chunks, so latency comparisons across sessions are apples-to-
    apples even though the model is non-deterministic itself.
    """
    if sample >= total:
        return list(range(total))
    if sample <= 1:
        return [0]
    step = (total - 1) / (sample - 1)
    return [int(round(i * step)) for i in range(sample)]


def call_extract(
    client: httpx.Client,
    *,
    endpoint: str,
    bearer: str,
    chunk_text: str,
    domain: str,
    images: list[str] | None,
) -> dict:
    """Single /v1/policy/extract call.

    Returns a dict with status, latency_s, candidate_count, first_name,
    error, raw_truncated. Never raises -- exceptions become {error: ...}
    so the loop in main() can keep going.
    """
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
            "first_name": None,
            "error": f"http: {type(exc).__name__}: {exc}",
        }
    elapsed = time.time() - started

    info: dict = {"status": r.status_code, "latency_s": elapsed}
    try:
        body_json = r.json()
    except Exception:  # noqa: BLE001
        info["candidate_count"] = None
        info["first_name"] = None
        info["error"] = f"non-json body: {r.text[:200]}"
        return info

    if r.status_code != 200:
        # Surface 502 detail (parser failure on the LLM's raw output) so
        # we can see what the model emitted before the parse broke.
        info["candidate_count"] = None
        info["first_name"] = None
        info["error"] = body_json.get("detail", body_json)
        return info

    candidates = body_json.get("candidates") or []
    info["candidate_count"] = len(candidates)
    info["first_name"] = candidates[0]["name"] if candidates else None
    info["needs_clar_count"] = sum(
        1 for c in candidates if c.get("needs_clarification")
    )
    info["error"] = None
    return info


def main() -> int:
    args = parse_args()

    bearer = os.environ.get("AGENT_BEARER_TOKEN", "")
    if not bearer:
        print(
            "AGENT_BEARER_TOKEN env var missing -- run via "
            "`AGENT_BEARER_TOKEN=$(gcloud secrets versions access ... ) "
            "python scripts/phase_d_vision_smoke.py`",
            file=sys.stderr,
        )
        return 2

    if not FIXTURE.exists():
        print(f"fixture missing: {FIXTURE}", file=sys.stderr)
        return 2

    # Lazy import so the script's --help works without AI_Agent installed.
    sys.path.insert(0, str(REPO / "AI_Agent"))
    from app.services.document_parser import parse_document  # noqa: E402

    chunks = parse_document(FIXTURE.read_bytes(), "application/pdf")
    if not chunks:
        print("fixture produced no chunks", file=sys.stderr)
        return 1
    indices = select_indices(len(chunks), args.sample)
    selected = [chunks[i] for i in indices]

    print(
        f"# Phase D smoke -- endpoint={args.endpoint}",
        file=sys.stderr,
    )
    print(
        f"# fixture: {len(chunks)} chunks parsed, sampling {len(selected)} "
        f"at indices {indices}",
        file=sys.stderr,
    )
    print(f"# mode={args.mode} max_tokens={args.max_tokens or 'service-default'}", file=sys.stderr)
    print(file=sys.stderr)

    print(
        f"{'idx':>4} {'mode':>6} {'len':>5} {'img_kb':>7} "
        f"{'status':>6} {'lat_s':>7} {'cands':>5} {'clar':>4} note"
    )

    results: list[dict] = []

    with httpx.Client(timeout=args.timeout) as client:
        for chunk in selected:
            img_kb = (len(chunk.image) // 1024) if chunk.image else 0
            modes_to_run = (
                ["text", "vision"]
                if args.mode == "both"
                else [args.mode]
            )
            for m in modes_to_run:
                images = [chunk.image] if m == "vision" and chunk.image else None
                if m == "vision" and not chunk.image:
                    print(
                        f"{chunk.index:>4} {m:>6} {len(chunk.text):>5} "
                        f"{0:>7} {'skip':>6} {'-':>7} {'-':>5} {'-':>4} "
                        f"chunk has no image"
                    )
                    continue
                info = call_extract(
                    client,
                    endpoint=args.endpoint,
                    bearer=bearer,
                    chunk_text=chunk.text,
                    domain=args.domain,
                    images=images,
                )
                row = {
                    "chunk_index": chunk.index,
                    "mode": m,
                    "chunk_len": len(chunk.text),
                    "image_kb": img_kb if m == "vision" else 0,
                    **info,
                }
                results.append(row)
                note = info.get("error") or (
                    info.get("first_name") or "(no candidates)"
                )
                note_short = (str(note)[:60]).replace("\n", " ")
                cands_field = info["candidate_count"] if info["candidate_count"] is not None else "-"
                clar_field = info.get("needs_clar_count") if info.get("needs_clar_count") is not None else "-"
                print(
                    f"{chunk.index:>4} {m:>6} {len(chunk.text):>5} "
                    f"{img_kb if m=='vision' else 0:>7} "
                    f"{info['status'] if info['status'] else 'ERR':>6} "
                    f"{info['latency_s']:>7.1f} "
                    f"{cands_field:>5} {clar_field:>4} {note_short}"
                )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            for row in results:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"# wrote {len(results)} rows to {args.out}", file=sys.stderr)

    # Comparison summary when --mode=both
    if args.mode == "both":
        text_rows = [r for r in results if r["mode"] == "text" and r.get("status") == 200]
        vision_rows = [r for r in results if r["mode"] == "vision" and r.get("status") == 200]
        if text_rows and vision_rows:
            t_lat = sum(r["latency_s"] for r in text_rows) / len(text_rows)
            v_lat = sum(r["latency_s"] for r in vision_rows) / len(vision_rows)
            t_cands = sum(r["candidate_count"] for r in text_rows)
            v_cands = sum(r["candidate_count"] for r in vision_rows)
            print(file=sys.stderr)
            print(
                f"# summary: text  avg_lat={t_lat:5.1f}s  total_cands={t_cands}",
                file=sys.stderr,
            )
            print(
                f"# summary: vision avg_lat={v_lat:5.1f}s  total_cands={v_cands}",
                file=sys.stderr,
            )
            if t_lat > 0:
                print(
                    f"# summary: vision/text  lat_ratio={v_lat/t_lat:.2f}x  "
                    f"recall_ratio={v_cands/t_cands if t_cands else 0:.2f}x",
                    file=sys.stderr,
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
