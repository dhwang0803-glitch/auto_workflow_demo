# auto_workflow_demo

> **A workflow automation engine that learns from your team — not the other way around.**
>
> Built for the **Gemma 4 Good Hackathon** (Main Track + llama.cpp Special Tech Track).

n8n and Zapier stop at the canvas: you draw the same nodes, fix the same
parameters, paste the same policy text into every new flow. This project
closes the loop. Drop your team's policy docs in once — the agent extracts
re-usable **skills** from them. Edit an AI-drafted workflow — the agent
turns your edit into a **personal skill** that the next draft already
incorporates. Share a personal skill — it lands in the team marketplace
for anyone to adopt.

The whole loop runs on local-frontier **Gemma 4 26B-A4B** (MoE, 4B
active) via **llama.cpp** on a single L4 GPU.

## Demo

- **30-second video**: see `tmp/demo/final.mp4` (1920×1080 / 30 fps, 33.8 s).
  YouTube link in the Kaggle Writeup.
- **Cover image**: `tmp/demo/frames/frame_27.png` — the share-narrative beat.
- **Reproduce the recording**: see [`scripts/RECORD_DEMO.md`](scripts/RECORD_DEMO.md).

## The three-axis story (what the demo shows)

| Track | Beat | Underlying mechanism |
|---|---|---|
| **A. Marketplace** | Alice's policy doc → workspace skill that bob can adopt | `POST /v1/skills/extract` (Gemma 4 reads the doc, proposes a SkillCard); user approves → row in `skills` with `scope='workspace'` |
| **B. Personalization** | Alice edits an AI draft → the *next* draft already reflects her edit | `POST /v1/personalization/extract_from_diff` (Gemma 4 propose + judge agent); approved candidate is injected into the next `/v1/compose` retrieval pool |
| **C. Share** | Bob promotes Alice's edit-derived skill to the workspace | `POST /v1/personalization/{id}/share` flips `scope` from `user` → `workspace` and surfaces it in everyone's marketplace |

All three tracks were live-verified end to end in scenario harness (`scripts/run_demo_scenarios.py`).

## How Gemma 4 is used

**Model**: [`unsloth/gemma-4-26B-A4B-it-GGUF`](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF)
quantization `UD-Q4_K_M` (~16-18 GB VRAM).

**Serving**: `llama.cpp` (`llama-server` binary) on a Modal-hosted L4
GPU container. OpenAI-compatible HTTP. `@modal.enter()` boots the
server; `@modal.asgi_app()` exposes a Bearer-gated FastAPI shim.

**Three call sites in the app**, all hitting the same Modal endpoint:

1. **`/v1/compose`** — natural language → workflow DAG, with workspace
   + personal skills retrieved (BGE-M3 embeddings) and injected as a
   single `## Skills` system-prompt section.
2. **`/v1/skills/extract`** — policy doc → SkillCard candidates with
   `<rationale>` blocks. Streams via SSE so the UI can render thought
   tokens during the 30-60 s generation.
3. **`/v1/personalization/extract_from_diff`** — workflow `v1` (AI draft)
   vs `v2` (user-edited) → personal-skill candidate. A two-node
   reflective agent: `propose` (LLM hint generation) → `judge` (rule +
   LLM verification that the diff is generalizable, not 1-shot noise).

**Why 26B-A4B + llama.cpp** — Q4 quantization fits L4 24 GB with KV-cache
headroom; activated 4 B params give 30-50 tok/s; vLLM does not yet
support GGUF Gemma 4. The choice slots directly into the llama.cpp
Special Tech Track.

**Non-obvious findings (documented inline in code)**:

- **Reasoning-trace stripping**: Gemma 4's chat template silently
  strips `<think>` blocks; `enable_thinking=False` +
  `reasoning_format=none` cut latency 91 % at the cost of recall, which
  an aggressive system prompt then recovered (+60 %).
- **JSON envelope wire**: switched the agent tool-call protocol from
  XML to `{"action": "tool_call|finish", ...}` because Gemma 4 RLHF
  prefers JSON — recall +3 candidates on the GitLab Handbook fixture.
- **Channel-mode prefix**: streamed responses arrive as
  `<|channel>thought\n<channel|>{json}` (no `<rationale>` wrapper);
  the parser falls back to first/last `{…}` extraction if the envelope
  is missing.
- **Cold-start equivalence**: per-user personalization registers tools
  conditionally, so cold-start (no personal skills) produces output
  bit-for-bit identical to the workspace-only baseline.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Frontend  (Next.js 14, App Router + React Flow + React Query)   │
│  - Workflow editor canvas                                        │
│  - AI Composer chat (SSE)                                        │
│  - Skill Bootstrap Wizard + Personal-skill review UI             │
└─────────────────────────────┬────────────────────────────────────┘
                              │  REST + SSE
┌─────────────────────────────▼────────────────────────────────────┐
│  API_Server  (FastAPI)                                           │
│  - Workflow CRUD, DAG scheduler, trigger manager                 │
│  - Auth (JWT), cross-user isolation                              │
│  - Proxies /api/v1/ai/* → AI_Agent (Bearer)                      │
└──────────┬──────────────────────────────┬────────────────────────┘
           │ Celery (Redis)               │ HTTPS + SSE
┌──────────▼──────────────┐   ┌───────────▼────────────────────────┐
│ Execution_Engine        │   │ AI_Agent  (Modal, L4)              │
│ - BaseNode registry     │   │ - llama.cpp + Gemma 4 26B-A4B Q4   │
│ - 21 node types         │   │ - Compose / Extract / Personalize  │
│ - Celery worker         │   │ - BGE-M3 embeddings                │
└──────────┬──────────────┘   └───────────┬────────────────────────┘
           │                              │
┌──────────▼──────────────────────────────▼────────────────────────┐
│  Database  (PostgreSQL 16 + Redis)                               │
│  - Workflows, executions, revisions                              │
│  - Skills (scope: workspace | user) + retrieval embeddings       │
│  - Credentials (AES-256-GCM at rest, RSA-OAEP-SHA256 to Agent)   │
└──────────────────────────────────────────────────────────────────┘
```

See [`docs/context/architecture.md`](docs/context/architecture.md) for the full layered breakdown.

## Repository layout

| Path | Layer |
|---|---|
| `Frontend/` | Next.js editor, composer chat, skill UIs, Playwright e2e |
| `API_Server/` | FastAPI core: workflows, executions, skills, personalization |
| `AI_Agent/` | Gemma 4 + llama.cpp service. Modal entrypoint at `scripts/modal_app.py` |
| `Execution_Engine/` | Celery worker + node registry (21 node types across 8 categories) |
| `Database/` | SQLAlchemy models, repositories, Alembic migrations |
| `infra/` | Terraform (GCP) — secrets, Cloud SQL, Cloud Run; Modal Secret sync |
| `docs/` | ADR-style decision log + architecture wiki |
| `scripts/` | Demo seed, recording, smoke tests, environment helpers |

Every top-level module has its own `CLAUDE.md` with file-placement and stack rules.

## Run locally

**Prereqs**: Docker, Python 3.11+, Node 20, pnpm.

```powershell
# 1. Postgres
docker compose up -d postgres redis

# 2. API_Server
cd API_Server
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e .
uvicorn app.main:app --port 8000

# 3. Frontend
cd ../Frontend
pnpm install
pnpm dev   # http://localhost:3000

# 4. AI_Agent (one of the following)
#    a) point at the live Modal endpoint  (set AGENT_ENDPOINT + AGENT_BEARER_TOKEN)
#    b) run llama.cpp locally with Gemma 4 GGUF on a 24 GB GPU
#    c) use the stub backend for offline UI dev  (AI_COMPOSER_USE_STUB=1)
```

To replay the demo scenarios against your local stack:

```powershell
python scripts/seed_demo_data.py             # alice + bob + 1 workspace skill
python scripts/run_demo_scenarios.py --all   # Track A, B, C end-to-end
```

To re-record the 30 s video, see [`scripts/RECORD_DEMO.md`](scripts/RECORD_DEMO.md).

## Tracks targeted

- **Main Track** — overall vision, technical execution, real-world impact.
- **Special Tech: llama.cpp** — Gemma 4 26B-A4B served exclusively
  through `llama-server` on a single 24 GB GPU. No vLLM/TGI/Ollama in
  the production path.

## License

Source code: MIT. Demo fixtures derived from the GitLab Handbook
(MIT-licensed). Gemma 4 weights are subject to the
[Gemma Terms of Use](https://ai.google.dev/gemma/terms). Per the
hackathon rules, the winning submission and code will be re-licensed
under CC-BY 4.0 if selected.
