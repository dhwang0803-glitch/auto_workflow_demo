# auto_workflow_demo

## A workflow automation engine that learns from your team — not the other way around

Built for the Gemma 4 Good Hackathon — **Main Track + llama.cpp Special Tech Track**.

---

### The problem

Tools like n8n, Zapier, and Make have made it dramatically easier to wire
SaaS together — but they stop at the canvas. Every new flow starts blank.
The same team policy ("invoices go to #ap-finance, never #engineering")
gets pasted into the prompt of every new draft. The same five edits to
fix the same five quirks ("our HTTP node always needs the X-Tenant
header") happen on every workflow forever. The tool never absorbs them.

This is exactly the gap local-frontier LLMs are positioned to close, if
they are wired into the right product loop: a model that's small enough
to run on a single L4 (Gemma 4 26B-A4B at Q4) but capable enough to
*understand* policy text and workflow diffs, plus an application layer
that captures every approval and every edit as durable signal. The
result is a system where each team's automations get more correct, more
opinionated, and more theirs every time someone touches them.

### The three-track loop the demo shows

The 30-second video walks through three live tracks on a real, running
stack (no mockups; the recording driver invokes the actual REST
endpoints):

**Track A — Marketplace.** Alice drops a policy paragraph in. The
extract endpoint streams a SkillCard back; she approves it. From bob's
session a moment later, the same skill appears in his "Team
marketplace" panel, ready to adopt. This is the **bootstrap input**:
team knowledge that already exists in writing becomes structured,
retrievable skill rows.

**Track B — Personalization.** Alice asks the composer for a new
workflow. The draft is close but she edits the slack node from
`#general` to `#finance` and renames a step. She saves. The system
diffs `v1` (AI draft) against `v2` (user edit), runs the
*personalization agent* — a two-node reflective pattern: `propose`
(LLM hint generation) followed by `judge` (rule check + LLM check that
the diff is generalizable, not 1-shot noise) — and creates a
`pending_review` personal-skill candidate scoped to alice. Alice opens
the library, sees "Suggested from your edits", and activates. The
*next* draft she asks for already reflects the edit, with no further
prompting. This is the **closed loop** competitors don't have.

**Track C — Share.** Bob's personal skill turns out to be useful for
the whole team, so he hits Share. One click flips `scope='user'` to
`scope='workspace'`. From alice's session, the new skill is now in her
marketplace too. Edits propagate from individuals → team without
ever leaving the product.

### How Gemma 4 powers the loop

**Model**: `unsloth/gemma-4-26B-A4B-it-GGUF`, quantization `UD-Q4_K_M`.
This is the 26B-total / 4B-active MoE variant. At Q4 it fits in ~16-18 GB
of VRAM, leaving generous KV-cache headroom on a 24 GB L4. The active
4B parameters give us 30-50 tok/s — fast enough that streamed SkillCard
extraction feels live.

**Serving**: `llama.cpp` exclusively. The `llama-server` binary is the
inference engine; the project never depends on vLLM, TGI, or Ollama in
the production path. vLLM does not yet support GGUF Gemma 4, and
community AWQ/GPTQ quantizations were not available when we started
— so llama.cpp wasn't a fallback, it was the only sensible choice for
this model on this GPU. That alignment is also why we qualify for the
llama.cpp Special Tech Track.

**Deployment**: Modal hosts the L4 container. `@modal.enter()` boots
`llama-server` with the GGUF model mmap'd from a Modal Volume;
`@modal.asgi_app()` exposes a Bearer-gated FastAPI wrapper. Scale-to-zero
keeps idle cost at $0; first-call cold start is 30-90 s.

**Three call sites, one model**:

1. **`/v1/compose`** — natural language → workflow DAG. BGE-M3
   retrieves the top-K skills from the union of workspace + active
   personal skills for the calling user; they are injected as a single
   `## Skills` system-prompt section. The single-section choice is
   deliberate (ADR-023): the user should not be made aware which of
   their skills came from team policy vs their own past edits — the
   invisibility *is* the experience.

2. **`/v1/skills/extract`** — policy doc → SkillCard. Streams via SSE
   so the UI can render the `<rationale>` block live during the 30-60 s
   generation. The recovered structured output drives the marketplace
   row.

3. **`/v1/personalization/extract_from_diff`** — `v1` vs `v2` workflow
   diff → personal-skill candidate. The reflective `propose → judge`
   agent reuses the harness we built for the workflow composer; only
   the prompts change.

### Architecture in one diagram

```
Frontend (Next.js, React Flow)  ↔  API_Server (FastAPI)  ↔  AI_Agent (Modal, L4 + llama.cpp)
                                          ↕
                              Database (PostgreSQL 16 + Redis)
                                          ↕
                              Execution_Engine (Celery, 21 node types)
```

The application core is a strict 4-layer pattern (presentation / core /
execution / data) with `BaseNode` plugin contracts shared between
serverless and Agent execution modes. AI_Agent is intentionally split
out behind an HTTP boundary, so the inference path can be replaced or
scaled without touching workflow CRUD. Credentials are stored
AES-256-GCM at rest in Postgres, re-encrypted with RSA-OAEP-SHA256
when forwarded to a customer-VPC Agent.

### Technical challenges we actually had to solve

**Reasoning-trace stripping**. The Gemma 4 chat template strips
`<think>` blocks before they reach the OpenAI-compatible response —
we discovered this only after a 76 s call returned 165 visible tokens
of 3832 generated. Setting `enable_thinking=False` and
`reasoning_format=none` cut latency by 91 % but lost 29 % recall on the
GitLab-Handbook smoke fixture. The fix was *not* a model swap or a
finetune; it was an aggressive prompt rewrite that recovered 60 % of
that loss on the same fixture (PR #155).

**Wire format**. The first agent harness used XML envelopes — Gemma 4
hallucinated closing tags. Switching the tool-call wire to
`{"action": "tool_call|finish", ...}` JSON (ADR-024 §3) gave a clean
+3-candidate recall delta on GitLab-Handbook with no other changes.
Gemma 4's RLHF clearly prefers JSON, and the rest of our agents have
inherited the same wire shape.

**Streamed parser quirk**. Streamed responses arrive prefixed with
`<|channel>thought\n<channel|>{json}` — there is no `<rationale>`
wrapper to anchor on. The parser falls back to first-/last-`{…}`
extraction when the envelope is missing, with a `_raw_full` mirror as
the source of truth. This was a one-day debug because the previous
parser silently surfaced `invalid_response`.

**Cold-start equivalence**. Personalization registers tools
*conditionally* per user — if a caller has zero personal skills, no
personalization tools register, and the output is bit-for-bit
identical to the workspace-only baseline. We verified this across 5
fixture chunks (PR #172), making the personalization layer safe to
ship as default-on without regressing the baseline draft quality
seasoned users already rely on.

**Live-fixture regression discipline.** Every recall-affecting PR ran
the GitLab-Handbook smoke (5 chunks, 16 candidates baseline) as a
guard, with NDJSON traces archived per session. When PR-ζ
(`max_tokens=2048`) fixed an ecommerce parse error but regressed
GitLab chunk 16 by −1 candidate, we caught it the same day and
reverted; the lesson — multi-fixture regression guards are mandatory
for any prompt or sampler change — is encoded in our internal
feedback notes.

### Why the technical choices were the right ones

We chose a 26B-class MoE specifically because it gives 27B-tier
*comprehension* (essential to read policy paragraphs and judge whether
a diff is a generalizable pattern) at 4B-tier *inference cost*
(essential to keep `/v1/compose` interactive). We chose llama.cpp
because it's the only path to GGUF Gemma 4 today, and GGUF is the
only quantization path that fits L4. We chose Modal because GCP Cloud
Run GPU quotas were unobtainable on our timeline and GCE L4 spot
capacity in us-central1 was empty for our zones — a real-world
operational constraint that pushed us off Google's own infrastructure
and onto a serverless GPU host that worked first try. Every decision
is documented in the in-repo ADR set (`docs/`).

### Impact

The closed-loop pattern matters beyond this demo. Knowledge workers
spend a measurable fraction of their day re-teaching tools that have
no memory; if open-weight LLMs can absorb that repetition into
durable, per-user, per-team structure, the productivity ceiling
shifts. Our product is one instance of that pattern; the same
diff-→-skill → retrieval loop generalizes to any
draft-and-edit workflow (docs, code, contracts). Gemma 4 26B-A4B at
Q4 on a single L4 makes that economically viable for a team to run —
no premium API spend, no cross-team data leaving the deployment.

### What's in the repo

- 256 unit + integration tests across all modules, 22 merged PRs in the
  hackathon phase alone
- `submission/live_demo/` — seed + scenario driver, judges can replay
  the three tracks locally in five minutes
- Cover image and video in `submission/media/`
- Per-module decision history in `docs/` and a public-facing
  `README.md` with stack-up instructions

GitHub: see Project Links. Live demo: `submission/live_demo/` files
attached. Video: YouTube link in Project Links.
