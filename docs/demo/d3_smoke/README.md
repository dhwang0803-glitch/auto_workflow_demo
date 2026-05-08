# D3 — live smoke + wizard demo evidence

This directory holds the end-to-end evidence we captured the day the
wizard's reflective pre-extract path went green. It complements the PRs
that landed the wiring (PR #166 API_Server proxy, PR #167 Frontend
wizard pre-extract) by showing the path running against live Modal,
end-to-end, with the agent_trace and LangSmith run IDs surfaced.

The PRs themselves prove correctness; the artifacts here are what we
hand a Kaggle judge or a teammate who wants to see the system *do* the
thing without booting it themselves.

## Layout

```
docs/demo/d3_smoke/
├── README.md                          ← this file
├── fixtures/
│   └── ecommerce_policy.txt           ← synthetic merchant policy (~2.4 KB)
├── screenshots/                       ← wizard phase captures (PNG)
│   ├── 01_domain_picker.png
│   ├── 02_doc_choice_empty.png
│   ├── 03_doc_choice_pasted.png
│   ├── 04_extract_review.png
│   └── 05_agent_trace_expanded.png
├── gitlab_handbook.ndjson             ← live Modal capture, GitLab fixture
└── ecommerce_policy.ndjson            ← live Modal capture, ecommerce fixture
```

The two `.ndjson` files are committed snapshots of one live run against
Modal. Latency and exact candidate text move with the model and prompt;
the *shape* (candidates → agent_trace.iterations → langsmith_run_id) is
the wire contract under test.

## What the artifacts demonstrate

The demo's core claim is that the reflective agent recovers coverage
that single-shot extraction misses **on the chunks where it matters,
without inflating recall on chunks where single-shot is already
saturated**. The two NDJSON captures together show both ends:

- `gitlab_handbook.ndjson` — the GitLab handbook subscription/license
  page (MIT-licensed; see `AI_Agent/tests/fixtures/NOTICE.md`) parsed
  into 23 chunks, sampled deterministically at 5 evenly spaced indices
  (0, 6, 11, 16, 22), and sent to Modal twice per chunk:
  `/v1/policy/extract` (single-shot) and `/v1/policy/extract_reflective`.

  Recall: **single-shot=1 cand, reflective=4 cands, delta=+3** across
  the 5-chunk sample (matches PLAN_13 §10 baseline). The recovery is
  concentrated on chunks 11 and 16, where single-shot returned 0 cands
  and reflective recovered 1 each via a judge-injected `prompt_hint`
  in iter 2 ("read-only state until additional storage", "L&R tickets
  must occur in #support_lic"). Chunk 6 also benefited, going from 1 to
  2 cands. The intro/footer chunks (0, 22) returned 0 cands in both
  modes — there are simply no rules to pull from boilerplate.

- `ecommerce_policy.ndjson` — a synthetic 7-section merchant
  return/refund policy written for this demo. Single-chunk plain text,
  ~2.4 KB.

  Recall: **single-shot=10 cands, reflective=10 cands, delta=0**.
  Single-shot saturates on this dense, well-structured prose; the
  reflect loop's self-eval correctly judges "all coverage concerns
  addressed" and converges in iter 1. This is the "no-op when
  unnecessary" half of the contract: the agent doesn't burn iterations
  on chunks that don't need them. (Latency is still ~24 s for the
  reflective call because iter 1 still runs the extract+judge legs.)

Together the two captures show the reflective agent does the right
thing on both extremes — recovers under-extracted PDF chunks and
defers when single-shot already covers everything.

### Screenshots

The screenshots walk a reviewer through the wizard UI: domain picker →
doc-choice → paste → review → agent_trace expanded. They use mocked
`/skills/*` responses tuned to a four-candidate
two-iter-with-recovery scenario so the agent_trace toggle has
something interesting to show. That is **not** the same scenario as
either committed NDJSON file — it is the visual demonstration of the
UI's capability under a representative reflective-recovery case. The
NDJSON files are the live-data evidence; the screenshots are the
UI evidence.

## Reproduction — screenshots

The screenshots come from `Frontend/tests/d3-screenshots.spec.ts`. The
spec uses Playwright route mocking, so it has no Modal dependency. Any
contributor can regenerate the PNGs:

```bash
cd Frontend
npx playwright test tests/d3-screenshots.spec.ts
```

The spec's mock candidates and `agent_trace` mirror the live Modal
response shape on purpose; the screenshot narrative therefore stays
honest if the spec is rerun on a machine with no Modal access.

## Reproduction — live NDJSON

The live capture hits Modal directly (not through API_Server) — same
endpoint the API_Server proxy calls in production. Modal's bearer token
lives in GCP Secret Manager (`agent-bearer-token-staging`), so the
runner has to inject it. From the repo root in PowerShell:

```powershell
# 1. Pipe the bearer into env. The block-secret-leak hook permits the
#    pipe form; do not assign the gcloud output to anything that prints.
$env:AGENT_BEARER_TOKEN = $(gcloud secrets versions access latest `
  --secret=agent-bearer-token-staging --project=autoworkflowdemo)
$env:PYTHONUTF8 = "1"   # avoids cp949 em-dash crashes on Windows

# 2. GitLab handbook fixture — 5-chunk deterministic sample, A/B against
#    /v1/policy/extract for the recall-delta line. Cold start is the
#    dominant cost on the first call (~150s); warm calls run 5–25s.
python scripts/plan_13_reflective_smoke.py `
  --sample 5 --max-iter 2 `
  --out docs/demo/d3_smoke/gitlab_handbook.ndjson

# 3. Ecommerce fixture — single chunk, plain text, same A/B.
python scripts/plan_13_reflective_smoke.py `
  --text-file docs/demo/d3_smoke/fixtures/ecommerce_policy.txt `
  --domain ecommerce --max-iter 2 `
  --out docs/demo/d3_smoke/ecommerce_policy.ndjson
```

Each run writes one NDJSON line per call (so 10 lines for the GitLab
sample of 5 × {single-shot, reflective}, 2 lines for the ecommerce
fixture). The full `agent_trace` lives in the `reflective` rows under
the `agent_trace` key. LangSmith run IDs are also printed to stderr in
a roll-up at the bottom of the run — paste them into the LangSmith UI's
search to navigate to each trace tree.

## Why text-mode only

Modal's vision path produces ~10 candidates per chunk (Phase D smoke,
2026-05-07) versus ~1 for text-mode single-shot. That makes the recall
recovery narrative — the entire point of the reflective agent — invisible
because vision already saturates. We deliberately stay text-mode for the
demo so iter 1 → iter 2 progression has somewhere to go.

The `--vision` flag still exists for benchmark runs against the
multimodal path; it just isn't what we capture here.

## When to refresh

- **Wizard UI changes** that affect doc-choice, extract-review, or the
  agent_trace toggle — rerun the screenshot spec.
- **Reflective response shape changes** — rerun the live smoke and
  commit the new NDJSON. The wire contract is also enforced by
  `API_Server/tests/test_skills.py` and `API_Server/tests/test_ai_agent_client.py`,
  so a wire break should fail there first.
- **Model or prompt changes** that move recall numbers — rerun the
  live smoke; the recall delta in the stderr summary is the headline
  figure to check.
