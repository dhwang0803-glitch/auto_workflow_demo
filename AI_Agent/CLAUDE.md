# AI_Agent — Claude Code branch guide

> Applied alongside the security rules in the root `CLAUDE.md`.

## Related docs

- Upstream dependency: `API_Server` — public endpoints, auth, rate
  limiting, and SSE proxying for AI features
- Downstream: Modal (external GPU hosting, pivoted 2026-04-24) + `infra/`
  (only the GCP Secret Manager used to sync Modal secrets is retained)

## Module role

**AI Orchestration Service** — the AI brain of `teamlift`.
Hosts LLM inference (Gemma 4 via llama.cpp), embeddings (personalized
retrieval), prompt assembly, and node-catalog RAG behind an HTTP boundary,
separated from `API_Server`.

`API_Server` only owns the **endpoints, auth, rate limiting, and SSE
proxy** for AI features; the actual LLM / embedding calls and prompt
orchestration all live in this module.

**Deployment unit**: Modal (L4 GPU, per-second billing, scale-to-zero).
`scripts/modal_app.py` reuses the existing `Dockerfile` to build the image,
caches the GGUF on a Modal Volume, boots `llama-server` as a subprocess
in `@modal.enter()`, and exposes a FastAPI app via `@modal.asgi_app()`.
The `/v1/*` routes are gated by a bearer token (env `AGENT_BEARER_TOKEN`,
injected via Modal Secret). Cold start is 1–2 min (image cache + model
mmap) — we keep `min=0` during the judging window.

**Pivot background**: on 2026-04-24 we pivoted to Modal because GCP infra
was repeatedly blocked (Cloud Run GPU quota not granted → GCE L4 spot in
us-central1 had no zone capacity → on-demand `GPUS_ALL_REGIONS=0`).
The pivot preserves Special Tech (llama.cpp) track eligibility.

## File-location rules (MANDATORY)

```
AI_Agent/
├── app/
│   ├── backends/   ← LLM / Embedding Protocols + implementations
│   │   ├── protocols.py      ← LLMBackend, EmbeddingBackend Protocol
│   │   ├── llamacpp_gemma.py ← Gemma 4 via llama-server HTTP
│   │   ├── gemma_embedding.py← Gemma 4 E2B pooled embeddings
│   │   ├── anthropic.py      ← dev / fallback (to be moved from API_Server)
│   │   └── stub.py           ← local-test stub
│   ├── services/   ← compose orchestration, RAG, prompt assembly
│   │   └── compose_service.py
│   ├── prompts/    ← prompt templates (Jinja or f-string)
│   ├── catalog/    ← node catalog (RAG corpus + search index)
│   ├── models/     ← Pydantic schemas (compose req / res)
│   └── main.py     ← FastAPI app (HTTP API exposed to API_Server)
├── scripts/        ← model download, llama-server boot helpers
├── config/         ← .env.example, llama-server config
├── docs/           ← design docs (SPLIT.md, etc.)
├── plans/          ← PLAN_NN_*.md
└── tests/          ← pytest
```

**Do not create `.py` files directly at the `AI_Agent/` root.**

## Tech stack (planned)

```python
from fastapi import FastAPI
from pydantic import BaseModel
import httpx       # call the llama-server OpenAI-compatible API
# candidates:
# - openai SDK (llama-server is compatible)
# - sentence_transformers (BGE-M3 fallback embedding)
# - transformers + torch (Gemma E2B pooling)
```

```
# Runtime dependencies
- llama.cpp (`llama-server` binary, built in the Dockerfile)
- unsloth/gemma-4-26B-A4B-it-GGUF (UD-Q4_K_M) — model weights (HF). Unsloth
  does not ship plain Q4_K_M; only the UD-* (Unsloth Dynamic) series is
  available.
```

## Core endpoints (called by API_Server)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/compose` | Natural language → WorkflowSchema JSON (SSE or non-stream) |
| POST | `/v1/embed` | Text → vector (node search) |
| GET  | `/v1/health` | llama-server + model readiness |

## Run modes

- **Local dev**: `uvicorn app.main:app --port 8100` (run llama-server in a
  separate terminal)
- **Container (local GPU)**: the `Dockerfile` ENTRYPOINT (`entrypoint.sh`)
  starts llama-server and uvicorn together
- **Modal (production)**: `modal deploy AI_Agent/scripts/modal_app.py` —
  Modal builds the Dockerfile, mounts the Volume, and boots in
  `@enter()`. Model download is one-shot:
  `modal run AI_Agent/scripts/modal_app.py::download_model`

## Interfaces

- **Upstream**: `API_Server` — `/api/v1/ai/compose` proxies this module's
  `/v1/compose`
- **Downstream**:
  - llama.cpp `llama-server` (in-container subprocess, localhost:8080)
  - (optional) Anthropic API — dev / fallback backend
  - `Database` — no direct calls. Usage metering happens in the API_Server
    layer.

## Security notes

- `/v1/*` endpoints **require bearer-token verification** (env
  `AGENT_BEARER_TOKEN`, Modal Secret `agent-bearer-token`). Modal exposes
  the endpoint over public HTTPS, so the token is the single gate. The
  value is synced with the GCP Secret Manager entry
  `agent-bearer-token-staging`, which `API_Server` reads to attach the
  bearer header.
- Only `/v1/health` is reachable without the token (Modal cold-start
  readiness probe + external monitoring).
- Model weights are public GGUF (unsloth/gemma-4-26B-A4B-it-GGUF). The
  `HF_TOKEN` Modal Secret is only for rate-limit avoidance — download
  works without it, but it is not recommended.
- **Do not** put user credentials or PII in prompts. This module only
  receives sanitized context from `API_Server`.

## Related PLANs / memory

- Main PLAN: `plans/PLAN_11_HACKATHON_SUBMISSION.md` (to be written)
- **Split spec**: [`docs/SPLIT.md`](./docs/SPLIT.md) — boundary with
  API_Server, move map, HTTP contract draft
- Model / serving rationale: auto-memory `project_gemma4_model_decisions.md`
- Hackathon background: `project_gemma4_hackathon.md`
- Backend swap contract: `project_llm_backend_swap_plan.md`
