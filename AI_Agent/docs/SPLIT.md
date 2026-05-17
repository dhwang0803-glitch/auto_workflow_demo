# API_Server ↔ AI_Agent split spec

> This document locks the split design that was agreed **before** PLAN_11 was written.
> Background decision: 2026-04-22 session. This document is the prerequisite for PLAN_11.

## Progress (PLAN_11 PR 1 reflected, 2026-04-22)

PLAN_11 PR 1 followed the principles below (Model X — low-level LLM proxy):

- **AIComposerService remains in API_Server**. Prompt build / JSON parsing /
  stream parser / rate limiter all stay in API_Server.
- AI_Agent exposes only low-level LLM endpoints: `POST /v1/complete`,
  `POST /v1/stream`, `GET /v1/health`. (§4's `/v1/compose` is added later in
  PLAN_11 if needed.)
- `AIAgentHTTPBackend` (`API_Server/app/services/ai_agent_client.py`) is the
  `LLMBackend` Protocol implementation; when `settings.ai_agent_base_url` is set,
  it replaces the existing Anthropic / Stub backends.
- **Code is "copied," not "moved"**. `AnthropicBackend` / `StubLLMBackend` exist
  in AI_Agent as copies, while still remaining in API_Server. The "delete" step in
  §5.3 is deferred to a follow-up PLAN_11 PR (once stable).

The "move" terms in §3–§5 below assume Model Y (high-level compose endpoint).
Model X reduced it to "copy + add a 1-layer HTTP boundary." Move to Model Y is
revisited post-hackathon if prompt engineering needs to migrate inside AI_Agent.

## 1. Background

PLAN_02 (AI Composer) packs the LLM backend + prompt + stream parser + rate limiter + node
catalog loader into a single 671-LOC file (`API_Server/app/services/ai_composer_service.py`).
PLAN_11 (hackathon) adds:

- `LlamaCppGemmaBackend` + a `llama-server` subprocess inside the Dockerfile
- `EmbeddingBackend` (Gemma E2B pooling + BGE-M3 fallback)
- Node-catalog RAG (embedding index + search)
- Demo-scenario fixtures + extended prompt templates

Adding all of this to `API_Server` would bind the AI dependencies (llama-cpp, GPU runtime,
HF Hub, embedding models) to the workflow-CRUD server's deployment unit. Without the split,
the `API_Server` Cloud Run service is locked to a GPU container.

## 2. Deployment options (locked in: A)

| Option | Structure | Hackathon choice |
|---|---|---|
| **A** | A single `AI_Agent` Cloud Run GPU container. `llama-server` runs as a subprocess inside the same container as the Python FastAPI. One cold start. | ✅ |
| B | AI_Agent (CPU) + `llama.cpp-server` (GPU) split into two services. | Reconsider after the hackathon when expansion is needed |

## 3. Move map

### 3.1 API_Server → AI_Agent (move)

| Current location | Symbol | New AI_Agent location |
|---|---|---|
| `API_Server/app/services/ai_composer_service.py:69` | `LLMBackend` Protocol | `AI_Agent/app/backends/protocols.py` |
| `…:97` | `AnthropicBackend` | `AI_Agent/app/backends/anthropic.py` |
| `…:158` | `StubLLMBackend` | `AI_Agent/app/backends/stub.py` |
| `…:304-333` | `RationaleDelta` / `Result` / `StreamError` | `AI_Agent/app/models/compose.py` |
| `…:334` | `_RationaleStreamParser` | `AI_Agent/app/services/stream_parser.py` |
| `…:446` | `AIComposerService` (orchestration logic) | `AI_Agent/app/services/compose_service.py` |
| `…:645` | `build_node_catalog_provider` | `AI_Agent/app/catalog/provider.py` |
| `API_Server/tests/test_ai_composer.py` (583 LOC) | backend / service / parser tests | `AI_Agent/tests/` (split out) |
| `API_Server/app/config.py:77` | `ai_composer_use_stub` | folded into the AI_Agent env var `LLM_BACKEND` |

### 3.2 Remains in API_Server

| Location | Symbol | Reason |
|---|---|---|
| `app/routers/ai_composer.py` (100 LOC) | SSE router | owns public traffic + auth |
| `ai_composer_service.py:45,59` | `ComposerDisabledError`, `ComposerRateLimitError` | raised at the proxy layer |
| `ai_composer_service.py:406` | `_InMemoryRateLimiter` | rate limit is a traffic-layer concern |
| `config.py:81-82` | `ai_compose_rate_per_minute`, `ai_compose_max_tokens` | rate-limiter parameters |

**Error-type split caveat**: `InvalidComposerResponseError` (line 52) is raised by the parser,
so **AI_Agent side is the primary owner**. API_Server translates to HTTP 422 for the client
(details locked in PLAN_11 PR 0).

### 3.3 New in API_Server

| Location | Symbol | Role |
|---|---|---|
| `API_Server/app/services/ai_agent_client.py` | `AIAgentHTTPBackend` | a thin httpx client that calls AI_Agent's `/v1/compose`. Takes the existing `LLMBackend` DI seat |
| `API_Server/app/config.py` | `ai_agent_base_url`, `ai_agent_timeout_s`, `ai_agent_auth_audience` (Cloud Run IAM ID token audience) | AI_Agent service connection info |

## 4. HTTP boundary (API_Server → AI_Agent)

3 endpoints. The detailed schemas / SSE frame format are **locked in PLAN_11 PR 0**.

| Method | Path | Role |
|---|---|---|
| POST | `/v1/compose` | natural language + context → WorkflowSchema. SSE stream (consider keeping Anthropic `message_start` / `content_block_delta` compatibility) |
| POST | `/v1/embed` | text array → vector array (node-search update) |
| GET  | `/v1/health` | model-ready state. Used by Cloud Run startup / liveness probes |

**Auth**: Cloud Run IAM invoker (service-account ID token). Not publicly exposed.

## 5. PLAN_11 impact

### 5.1 PR re-allocation (vs. pre-split proposal)

| Pre-split | Post-split |
|---|---|
| W1 PR 1 (API_Server) — llama.cpp smoke + `LlamaCppGemmaBackend` | **AI_Agent** PR — backend implementation + Dockerfile skeleton |
| W1 PR 2 (API_Server) — `EmbeddingBackend` Protocol + Gemma E2B pooling | **AI_Agent** PR — embedding backend + pooling |
| W1 PR 3 (API_Server) — quality A/B record | **AI_Agent** PR — A/B test + default backend switch |
| (none) | **API_Server** PR — `AIAgentHTTPBackend` proxy + move PLAN_02 symbols |
| W2 PR 4 (infra) — Cloud Run GPU deployment | **infra** PR — AI_Agent Cloud Run GPU service (option A) |

### 5.2 Schedule impact

- W1 early: **0.5–1 day additional**: beyond setting up the AI_Agent directory, moving
  existing symbols + API_Server-side HTTP client + splitting tests.
- Offsetting upside: after PLAN_11, every AI feature expansion stays inside AI_Agent.
  API_Server only manages the proxy.
- Risk: if the HTTP contract is not locked, both PRs progress in parallel and block each
  other — **place PLAN_11 PR 0 (HTTP contract + move-as-copy) as the highest-priority
  first PR**.

### 5.3 Migration order (PLAN_11 PR 0 candidate)

1. **Copy** `AnthropicBackend` / `StubLLMBackend` / `_RationaleStreamParser` /
   `AIComposerService` core logic into AI_Agent (not yet deleted from API_Server).
2. Bring up AI_Agent `/v1/compose` HTTP API, reuse existing pytest locally to verify behavior.
3. Implement `AIAgentHTTPBackend` in API_Server, swap the `LLMBackend` implementation in DI.
4. After existing `API_Server/tests/test_ai_composer.py` passes, remove the duplicated
   symbols on the API_Server side.
5. Move the `LLMBackend` Protocol definition to AI_Agent. API_Server keeps only the HTTP
   client interface.

The copy → switch → delete order exists to allow any-time rollback.

## 6. Related references

- auto-memory `project_gemma4_hackathon.md` — hackathon background / prize / evaluation weighting
- auto-memory `project_gemma4_model_decisions.md` — 26B-A4B Q4 GGUF + llama.cpp
- auto-memory `project_llm_backend_swap_plan.md` — must reflect this split (backend swap redefined as inside-AI_Agent work)
- Follow-up: `AI_Agent/plans/PLAN_11_HACKATHON_SUBMISSION.md` (to be written)
