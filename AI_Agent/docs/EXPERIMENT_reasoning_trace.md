# Gemma 4 reasoning trace — experiment notes + reproduction guide

A follow-up diagnostic to PLAN_12 W3-4 (late session, 2026-05-05). After raising max_tokens from 1024 → 4096 reached 90%, this is the result of tracking down the root causes of the remaining *2 stochastic failures* and the *30–77 s latency on dense chunks*.

> Full session narrative + memory index: auto-memory `reference_gemma4_reasoning_trace.md`, `project_session_20260505_reasoning_root_cause.md`. This document is the entry point for *continuing the experiment* from inside the codebase.

## 1. Result summary

| Metric | reasoning ON (cycles 4/5) | reasoning OFF (cycle 6) | Delta |
|---|---|---|---|
| Total wall time | ~600 s (10 min) | **~55 s** | **-91%** |
| Failure rate | 10% (cycle 4) → 0% (cycle 5 after budget 4096) | **0%** | |
| Average dense chunk | 30–77 s | 2.8–5.5 s | -90% |
| Average empty-response chunk | ~10–30 s | **0.7–1.0 s** | -97% |
| Number of candidates | 14–19 (cycle variance) | **10** | **-29% recall** |

Based on a 20-chunk fixture (`tests/fixtures/gitlab_handbook_excerpt.pdf`).

## 2. Root cause

Gemma 4 26B-A4B is a hidden-reasoning model. It emits a `<think>...</think>` trace, and llama-server's chat-template parser automatically strips that trace from the visible `choices[0].message.content`. The caller never sees it, but the GPU time and the max_tokens budget are consumed all the same.

**Decisive evidence** (one line from a Modal log):

```
eval time = 76063.81 ms / 3832 tokens (50.38 tok/s)
slot release: stop processing: n_tokens = 4484, truncated = 0
```

Body of the same call: `len(content) = 655` (~165 tokens).

3832 tokens generated / 165 tokens visible = **3667 tokens stripped by the chat template**.

## 3. Applied fix

`app/backends/llamacpp_gemma.py::_chat_payload`:

```python
"chat_template_kwargs": {"enable_thinking": False},
"reasoning_format": "none",
```

Both fields are sent at once — depending on the llama-server build, different keys are recognized. Unknown keys are silently ignored.

## 4. Outstanding trade-off

candidates dropped from **14 → 10** (-29%). Lost candidates:

| chunk | cycle 5 (reasoning ON) | cycle 6 (reasoning OFF) |
|---|---|---|
| #8 | "Allocate Compute Minutes by tier" | (missing) |
| #12 | "Request CustomersDot Admin Access" | (missing) |
| #15 | "format_zuora_access_requests" | (missing) |
| #18 | "exclude_out_of_scope_requests_from_queue" | (missing) |

All are clear policies — without reasoning, the model interprets conservatively and filters them out. The interview path lets the user fill them back in, but the standalone value of the docs path drops.

## 5. Experiment results (closed 2026-05-06)

> **Decision: a variant of option D is the winner — add "high recall over precision" bias to the system prompt**.
> The data in §5.1 below shows option D recovers docs-path-only recall from 10 → 16 while keeping latency at the default. Phase 3 burn-in PR absorbs this into the default `_system_prompt` and removes all Phase 1 instrumentation surfaces.

### 5.1. Phase 0 / Phase 2 measurement data

Three cycles run:

- **Phase 0 (baseline variance)**: 3 smoke runs under identical conditions → 10 candidates / 2 needs_clarif / 41 s warm. The per-chunk distribution and extracted text are byte-identical → **deterministic (variance = 0)**. The 4 missing items are not stochastic but systematic conservatism — cannot be recovered by retry.
- **Phase 1 (instrumentation surface, PR #154)**: `/v1/policy/extract` accepts 4 experiment request fields (`system_prompt_override`, `enable_thinking`, `temperature`, `include_raw`). The smoke script also gains `--strictness {default,aggressive,lenient}` / `--enable-thinking` / `--temperature` flags. One redeploy turns every later sweep into client-side iteration.
- **Phase 2 (sweep)**: 7 cells.

| Cell | strictness | thinking | temp | candidates | needs_clarif | wall (s) |
|---|---|---|---|---|---|---|
| S0 | default | OFF | 0 | 10 | 2 | 64 |
| **S1** | default | **ON** | 0 | **14** | 3 | **634** |
| **S2** | **aggressive** | OFF | 0 | **16** | 2 | **56** |
| S2' | aggressive | OFF | 0 | 16 | 2 | 54 |
| S3 | lenient | OFF | 0 | 16 | **9** | 77 |
| S4a/b/c | default | OFF | 0.4 | 15-16 | 2 | 50-54 |
| S5 | aggressive | OFF | 0.4 | 15 | 3 | 52 |

Key findings:

- **Aggressive prompt (S2)** produces more raw candidates (16) than ground truth (S1, 14), with latency equal to default. The count is deterministic (S2 ↔ S2' have identical per-chunk distribution).
- **Temperature alone (S4)** also reaches ~16, but with variance + misses boundary candidates such as #11. Aggressive's determinism is preferable.
- **Combined (S5)** is a regression — temperature scatters the deterministic #11 finding aggressive locks in.
- **Lenient (S3)** explodes needs_clarification (9), heavy load on the review UI. Inefficient.
- **Three items only reasoning ON recovers** (#8 Allocate Compute Minutes, #12 CustomersDot Admin, #15 Zuora format) — all need parsing dense reference tables. Cannot be solved by prompt/sampling. Acceptable for the hackathon demo because there are enough other candidates.
- **Extra items aggressive catches** (#11 Join reviewers group, #18 dense exclusion 5-way split) — even reasoning ON missed these. Qualitatively valuable too.

### 5.2. Adopted changes (Phase 3 burn-in)

- Short "## Bias" section appended to `_system_prompt` — "When in doubt, INCLUDE the candidate with needs_clarification=true rather than dropping it."
- Remove all of Phase 1: the 4 request fields, response `raw`, and smoke flags (keep hackathon main clean)
- Other infrastructure (`enable_thinking=False`, `temperature=0.0`, `reasoning_format=none`) unchanged

### 5.3. Discarded options (for reference)

### Option A: keep current fix + defer recall recovery

Demo-first for the hackathon. The interview path naturally fills in. No code change.

### Option B: `--reasoning-budget N` (server-level, balanced)

Add to the `cmd` in `scripts/modal_app.py::start_llama_server`:

```python
cmd = [
    "/usr/local/bin/llama-server",
    ...,
    "--reasoning-budget", os.environ.get("REASONING_BUDGET", "256"),
]
```

→ The model reasons up to N tokens, then starts producing visible output. Tuning N:
- 0: same as the current fix (catches 10/10 candidates but recall stays 14→10)
- 256: ~5 s/chunk, estimated recall 12–13
- 512: ~10 s/chunk, estimated recall 13–14
- -1 (unlimited): original behavior, recall 14–19 / 30–77 s/chunk

May conflict with `chat_template_kwargs` — when applied together, try first and remove `chat_template_kwargs` if needed.

**Reproduction commands** (Modal redeploy + smoke):

```powershell
$env:REASONING_BUDGET = "256"  # passed as a llama-server flag
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 modal deploy AI_Agent/scripts/modal_app.py
# Wait for "App deployed" (~4 min)
cd AI_Agent
$env:AGENT_URL = "https://dhwang0803--auto-workflow-agent-agentservice-fastapi.modal.run"
$env:AGENT_BEARER_TOKEN = $(gcloud secrets versions access latest --secret=agent-bearer-token-staging --project=autoworkflowdemo)
python scripts/smoke_handbook_policy_extract.py | Tee-Object /tmp/smoke_budget_256.txt
```

Comparison metrics: total wall time, candidates count, dense-chunks (#4, #13) latency.

### Option C: 2-pass (structural)

On the `policy_extract` caller side (API_Server):

1. Pass 1: reasoning OFF (current fix). Fast. recall 10
2. Pass 2 (optional): when the user clicks "find more," re-run with reasoning ON. recall 14-19

Higher complexity. Needs a separate endpoint or a query param to toggle reasoning. Downside: 2x GPU time (ON calls are slow).

### Option D (experiment): suppress reasoning via a stronger system prompt

Remove `enable_thinking=False` and append to the end of the system prompt:

> "Output ONLY the JSON object directly. Do not include any reasoning or explanation."

→ Reasoning trace is emitted briefly (model self-suppresses). Effect estimated 50-50. Lightest attempt.

## 6. Diagnostic tools (still present in the current codebase)

### 6-1. 502 detail dict (`{error, raw_len, raw}`)

`app/main.py::policy_extract` catches `PolicyExtractParseError` and sends the raw payload (truncated to 1500 chars) as a detail dict. When a parse failure happens live, the caller sees the raw without a Modal log hop.

```
HTTP 502
{"detail": {
    "error": "no JSON object in response: ''",
    "raw_len": 0,
    "raw": ""
}}
```

`PolicyExtractParseError.raw` is a service-side attribute. In production it could also be written to the logger (currently not).

### 6-2. Modal log grep (reproduction)

```powershell
modal app logs auto-workflow-agent --since 600s | Select-String "eval time|stop processing"
```

For each call:
- `eval time = X ms / Y tokens (... tok/s)` ← total tokens generated
- `slot release: stop processing: n_tokens = Z` ← prompt + completion sum

If `Y` (generated) >> `len(content)/4` (visible), reasoning trace is being stripped. The single channel that provided the decisive evidence for this finding.

## 7. Re-add this instrumentation (one-shot) when running more experiments

After this fix is merged, when trying options B/C/D, the *temporary* instrumentation that may be needed again:

### 7-1. Raw response exposure (`include_raw` flag)

Removed once the PR merged (keep the production surface clean). To re-add:

```python
# add to app/models/skills.py PolicyExtractRequest:
include_raw: bool = False

# add to PolicyExtractResponse:
raw: str | None = None

# change app/services/policy_extract.py extract_policies to return (drafts, raw) tuple
# branch app/main.py policy_extract handler on payload.include_raw to attach raw
```

### 7-2. Smoke script raw dump

In `scripts/smoke_handbook_policy_extract.py`, removed once the PR merged. To re-add:

```python
# add "include_raw": True to the request
# extract raw = body.get("raw") or "" from the response
# dump_path.write_text(raw, encoding="utf-8") for any chunk where elapsed > 50s
```

The exact patch can be recovered from this session's git history (commit just before the revert). Or refer to the changes in §3 + §5 of this document.

## 8. Scope of impact (other LLM services)

`LlamaCppGemmaBackend`'s `_chat_payload` is shared across the backend, so `chat_template_kwargs` applies to every service:

| Service | Input | Output | Estimated impact | Verification status |
|---|---|---|---|---|
| `compose_service` | natural language | WorkflowSchema JSON | reasoning likely beneficial | **unverified** |
| `domain_classifier` | short text | DomainCategory | short I/O, small impact | unit test only |
| `gap_analyze` | policy list | PolicyGap[] | medium I/O | unit test only |
| `answers_to_skill` | parameter answers | SkillDraft | medium | unit test only |
| `policy_extract` | document chunk | SkillDraft[] | verified in this experiment | **cycle 6 smoke OK** |

If another service essentially needs reasoning, change `_chat_payload` to a service-level toggle (e.g., `complete(*, enable_thinking: bool = False)`). Right now every service outputs JSON, so the latency loss outweighs the reasoning gain — uniform fix.

## 9. Referenced memory / ADRs

- auto-memory `reference_gemma4_reasoning_trace.md` — general pattern (diagnostic method + fix + llama.cpp option pool)
- auto-memory `project_session_20260505_reasoning_root_cause.md` — narrative of this session
- auto-memory `reference_policy_extract_smoke_findings.md` — per-cycle data up through cycle 4 smoke
- ADR-022 §8.1 (condition+action unit), §8.2 (needs_clarification)
- PLAN_12 §6 (budget assumption), §9 W3 (docs path)
