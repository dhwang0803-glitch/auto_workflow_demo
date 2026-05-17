# PLAN_11 — Kaggle Gemma 4 Good Hackathon submission

> **Branch**: `AI_Agent` lead · `API_Server`/`infra`/`Frontend` supporting
> **Written**: 2026-04-22 · **Deadline**: 2026-05-18 23:59 UTC (D-26)
> **Status**: Active — top-priority project goal
> **Predecessor spec**: [`../docs/SPLIT.md`](../docs/SPLIT.md) — API_Server ↔ AI_Agent split boundary

## 1. Goal

Turn the teamlift project into a **Kaggle Gemma 4 Good
Hackathon** submission. Total prizes: $200K. **Dual-prize target**:
Main/Impact Track (Digital Equity & Inclusivity) + Special Tech
(llama.cpp). Win both → $20K+.

**70% of judging is non-code** (Impact 40% / Video 30% / Technical 30%).
Video and story matter as much as engineering — don't tunnel into
engineering alone.

## 2. Confirmed decisions (2026-04-22)

| Item | Decision | Rationale |
|---|---|---|
| Track | **Digital Equity & Inclusivity** ($10K) | AI Composer "natural language → workflow" directly aligns with the "remove coding barrier" story |
| Team | **Solo** | 26 days / already familiar with the codebase. Team onboarding cost is too high |
| Embedding | **Gemma-4 E2B pooling default + BGE-M3 fallback** | novelty + Technical Depth. A/B inside W1, switch if it fails |
| Live demo | **Cloud Run GPU L4 min=0** | Spot = disqualified on preemption. always-on = over budget. 30–60s cold start is offset by the UX story |
| Plan doc | New PLAN_11 | PLAN_10 (ops guardrails) deferred until after the hackathon |

## 3. Required submission artifacts

1. **Kaggle Writeup** ≤ 1,500 words
2. **YouTube video** ≤ 3 minutes (public, no sign-in required)
3. **Public code repo** (this repo)
4. **Live demo URL** — kept up throughout judging, no sign-in / paywall

## 4. Aligning with judging criteria

| Criterion | Weight | Our evidence |
|---|---|---|
| Impact & Vision | 40% | "Workflow automation without coding = digital equity" narrative. Three non-developer use scenarios (nonprofit admin / solo operator / educator) |
| Video Pitch & Storytelling | 30% | 3-min video: hook 5s / problem 30s / live demo 90s / tech 30s / closing 15s |
| Technical Depth & Execution | 30% | Gemma 4 26B-A4B Q4 GGUF on L4 + llama.cpp (Special Tech alignment). E2B pooling personalization. SSE-streamed compose |

## 5. Milestones (26-day backward plan)

### W1 (04/22-04/28) — backend swap + E2E #1

**Exit criteria**: locally, one natural-language input passes through AI_Agent compose → API_Server → WorkflowSchema persist.

- **PR 1** (AI_Agent + API_Server) — **code migration**. Move map per `docs/SPLIT.md §3` + new `AIAgentHTTPBackend`. `§5.3` copy → switch → delete order. Predecessor PR that runs right after this PLAN merges.
- **PR 2** (AI_Agent) — implement `LlamaCppGemmaBackend` + Dockerfile skeleton + `llama-server` subprocess boot script (`scripts/run_llama_server.sh`).
- **PR 3** (AI_Agent) — `EmbeddingBackend` Protocol + `GemmaE2BPoolingBackend` + `BgeM3Backend` (fallback). Runtime toggle via env `EMBEDDING_BACKEND`.
- **PR 4** (AI_Agent) — E2B vs BGE-M3 A/B test (5–10 queries, Recall@K on node search) + record default-backend switch decision (`docs/EMBEDDING_CHOICE.md`).

**Parallel**: request the GCP L4 quota (us-central1, 1–3 days for approval). Run local smokes while waiting.

### W2 (04/29-05/05) — deploy + three scenarios

**Exit criteria**: 3 scenarios pass compose→execute on the public demo URL.

- **PR 5** (infra) — Deploy AI_Agent to Cloud Run GPU L4 min=0. Model weights from a GCS bucket or Artifact Registry OCI artifact. Startup probe = `/v1/health`. IAM invoker restricted to the API_Server service account.
- **PR 6** (AI_Agent) — Node-catalog RAG. Embedding index in `app/catalog/` (in-memory FAISS or simple numpy dot) + search + compose-prompt injection.
- **PR 7** (AI_Agent) — Three demo-scenario fixtures, aligned with the Digital Equity story:
  - Nonprofit admin: "automatic donation thank-you emails"
  - Solo operator: "Slack alert on customer inquiry + Notion logging"
  - Educator: "auto-classify submitted assignments in Drive + grading list"
- **PR 8** (Frontend) — ChatPanel cold-start UX. Loading spinner + "Scale-to-zero architecture" microcopy + "Try one of these" scenario buttons.

### W3 (05/06-05/12) — polish + video / Writeup drafts

**Exit criteria**: video rough cut + Writeup draft complete.

- Frontend polish: onboarding modal, error-recovery copy, execution-result preview.
- 3-min video script fix → record → rough cut.
- Writeup 1,500-word draft (Impact 40% story / technical highlights / llama.cpp Special Tech points).

### W4 (05/13-05/18) — finals week

**Exit criteria**: submitted before 05/18 UTC 23:59.

- Video edit → upload to YouTube → verify public / no sign-in.
- Writeup finalized (fact check, word count, submission checklist).
- Demo URL final stabilization: cold-start simulation, error guard, prep backup recording attached to Writeup.
- **05/17 final dry run** → **05/18 submit**.

## 6. PR plan summary

The split decision in `docs/SPLIT.md` is a premise of PR 1. After that
most PRs are AI_Agent internal, with minimal API_Server / infra
involvement.

| # | Lead branch | Main changes | Depends on |
|---|---|---|---|
| 1 | AI_Agent + API_Server | PLAN_02 symbol move + `AIAgentHTTPBackend` | After SPLIT spec merges |
| 2 | AI_Agent | `LlamaCppGemmaBackend` + Dockerfile | PR 1 |
| 3 | AI_Agent | `EmbeddingBackend` (E2B pooling + BGE-M3) | PR 1 |
| 4 | AI_Agent | E2B vs BGE-M3 A/B + default-backend decision | PR 2, PR 3 |
| 5 | infra | Cloud Run GPU deploy | PR 2 (image ready) |
| 6 | AI_Agent | Node-catalog RAG | PR 3 |
| 7 | AI_Agent | Demo-scenario fixtures | PR 4, PR 6 |
| 8 | Frontend | Cold-start UX + scenario buttons | PR 5 |

## 7. Budget (entire hackathon)

| Phase | Item | Amount |
|---|---|---|
| 26-day dev | Cloud Run GPU scale-to-zero (1 tester) | ~$40 |
| ~3-week live demo | Cloud Run GPU min=0 + DB/Redis | ~$130 |
| Other | Artifact Registry / GCS / (optional) domain | ~$10 |
| **Estimated total** | — | **~$170** |
| **Recommended with buffer** | Quota / re-measure / retry | **~$250** |

## 8. Risks + mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| L4 quota approval delay (1–3 days) | W2 deploy slips | File request day 1 of W1. Run local smokes while waiting |
| Gemma-4 E2B pooling quality is poor | Technical Depth weakens | Pre-designed `EmbeddingBackend` Protocol with BGE-M3 fallback |
| 26B-A4B Q4 GGUF quality / stability (HF uploaded a day before) | Compose failure across the board | Fallback: `unsloth/gemma-4-E4B-it-GGUF` (lower quality, 5GB VRAM) |
| 30–60s cold start | Judge UX degraded | Loading spinner + "scale-to-zero" video framing. Accept traffic only after the startup probe |
| Solo-team video quality | Video 30% weakens | Hold a $50–100 edit-only outsourcing option in reserve |
| Demo URL down during judging | Disqualification risk | Attach a backup recording to the Writeup |
| HTTP boundary contract unsettled | Blocks anything after PR 1 | Draft in `docs/SPLIT.md §4`. Fix in PR 1 |

## 9. Dual-prize target

> *"Projects are eligible to win both a Main Track Prize and a Special Technology Prize."*

- **Main/Impact Track**: Digital Equity & Inclusivity ($10K).
- **Special Tech**: llama.cpp ($10K). Choosing 26B-A4B Q4 GGUF on L4 aligns naturally with the "resource-constrained hardware" story.
- Win both → **$20K+**.

## 10. Related PLAN / memory / docs

- Predecessor spec: [`../docs/SPLIT.md`](../docs/SPLIT.md) — split boundary, move map, HTTP contract draft
- Deferred: `API_Server/plans/PLAN_10_AI_COMPOSER_OPS.md` — ops guardrails (resumed after the hackathon)
- Extension base: `API_Server/plans/PLAN_02_WORKFLOW_CRUD.md` — AI Composer (this PLAN extends it)
- auto-memory `project_gemma4_hackathon.md` — prize / rules / judging detail
- auto-memory `project_gemma4_model_decisions.md` — model / serving decision rationale (26B-A4B Q4 GGUF + llama.cpp)
- auto-memory `project_llm_backend_swap_plan.md` — backend-swap contract (realized in PR 1–2 here)
