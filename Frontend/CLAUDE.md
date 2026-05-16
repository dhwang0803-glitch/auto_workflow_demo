# Frontend — Claude Code branch guide

> Applied alongside the security rules in the root `CLAUDE.md`.

## Related docs

- Upstream dependency: `API_Server` — REST contract + (planned) WebSocket
  realtime stream
- Decision rationale: `docs/context/decisions.md`
- PLAN docs: `Frontend/plans/PLAN_01_*.md`, `PLAN_02_*.md` (+ Frontend
  work items inside `AI_Agent/PLAN_12`)

## Module role

**Workflow Editor + AI interface UI** — a Next.js 14 (App Router) web
client where users do three things:

1. **Edit workflows** (PLAN_01) — drag / connect nodes on the React Flow
   canvas, edit parameters, save, run manually, and inspect results
2. **AI Composer chat** (PLAN_02) — natural-language input → SSE stream
   of rationale tokens + a proposed DAG → Apply commits to the canvas
3. **Skill Bootstrap Wizard** (PLAN_12 W2-5 / W2-6) — pick a domain chip →
   interview questions → produce SkillDrafts → review SkillCards
   (Approve / Reject / Answer follow-up)

This is the **Frontend Layer** in the 4-layer architecture. Business
state machines live in Zustand stores; server cache lives in React Query
(TanStack Query).

## File-location rules (MANDATORY)

```
Frontend/
├── src/
│   ├── app/                ← Next.js App Router routes (RSC + client components)
│   │   ├── layout.tsx          ← root layout (includes QueryProvider)
│   │   ├── page.tsx            ← home (`/`) — workflows list
│   │   ├── workflows/[id]/page.tsx
│   │   └── skills/new/page.tsx ← Skill wizard entry
│   ├── components/         ← UI components (no direct execution)
│   │   ├── editor/             ← canvas / palette / property panel (PLAN_01)
│   │   ├── skills/             ← skill-card, skill-wizard (PLAN_12)
│   │   └── workflows-list.tsx  ← home list
│   ├── lib/                ← API client + domain utils (NOT services/)
│   │   ├── api.ts              ← apiFetch wrapper + workflows / executions / nodes
│   │   ├── composer.ts         ← compose JSON + SSE stream
│   │   ├── skills.ts           ← bootstrap / answer / approve / reject
│   │   ├── dag.ts              ← DAG serialization utils
│   │   └── auto-layout.ts      ← dagre auto-layout
│   ├── store/              ← Zustand (composer-store, editor-store, skill-wizard-store)
│   └── providers/          ← React Query Provider, other cross-cutting
├── public/                 ← static assets
├── plans/                  ← `PLAN_NN_*.md`
└── tests/                  ← Playwright (`*.spec.ts`)
```

| File kind | Location |
|-----------|----------|
| Routes / pages | `src/app/<route>/page.tsx` (App Router — never `pages/`) |
| Reusable UI components | `src/components/<domain>/*.tsx` |
| API client + domain utils | `src/lib/*.ts` |
| Zustand store | `src/store/*-store.ts` (one file per domain) |
| Cross-cutting Provider | `src/providers/*.tsx` |
| Playwright specs | `tests/*.spec.ts` |

**Do not create `.ts` / `.tsx` files directly at `Frontend/` or `src/`
root.**

## Tech stack

```typescript
// framework / language
Next.js 14 (App Router) · TypeScript 5 · Tailwind CSS 3

// core libraries
import ReactFlow from "reactflow";           // workflow canvas
import { create } from "zustand";            // client state
import { useQuery } from "@tanstack/react-query"; // server cache
import dagre from "dagre";                   // auto-layout
import zundo from "zundo";                   // undo / redo (editor)

// testing
import { test, expect } from "@playwright/test";
```

## Key components / pages

| Component / page | Role | PLAN |
|-------------------|------|------|
| `app/page.tsx` (`WorkflowsList`) | Home — workflows list + skill-wizard link | PLAN_01 |
| `app/workflows/[id]/page.tsx` | Single-workflow editor | PLAN_01 |
| `app/skills/new/page.tsx` | Skill wizard entry | PLAN_12 W2-5 |
| `components/editor/workflow-canvas.tsx` | React Flow canvas (node / edge editing) | PLAN_01 |
| `components/editor/node-palette.tsx` | Node catalog drag source (`/api/v1/nodes/catalog`) | PLAN_01 |
| `components/editor/property-panel.tsx` + `property-form.tsx` | Edit the selected node's parameters | PLAN_01 |
| `components/editor/result-drawer.tsx` | Display `ExecutionResponse.node_results` per node | PLAN_01 |
| `components/editor/chat-panel.tsx` | AI Composer SSE chat + Apply draft | PLAN_02 |
| `components/skills/skill-wizard.tsx` | Domain chips → interview → card review | PLAN_12 W2-5 / W2-6 |
| `components/skills/skill-card.tsx` | CONDITION / ACTION / RATIONALE + Approve / Reject / Follow-up | PLAN_12 W2-6 |

## Data flow

```
Workflow editor (PLAN_01):
  NodePalette (catalog from /api/v1/nodes/catalog)
    → drag onto WorkflowCanvas (React Flow)
    → edit NodeConfig in PropertyPanel
    → editor-store tracks dirty + zundo undo/redo
    → [Save]   POST /api/v1/workflows  | PUT /api/v1/workflows/{id}
    → [Execute] POST /api/v1/workflows/{id}/execute
    → ResultDrawer polls ExecutionResponse (stops on TERMINAL_STATUSES)

AI Composer (PLAN_02):
  ChatPanel → composer-store
    → composeStream (POST /api/v1/ai/compose?stream=true)
    → SSE frames: session / rationale_delta / result / error
    → result.intent ∈ {clarify, draft, refine}
    → draft|refine: pendingDraft saved → [Apply] calls editor-store.applyComposerDraft

Skill Wizard (PLAN_12 W2-5/6):
  DomainPicker → POST /api/v1/skills/bootstrap
    → flat queue of (policy_id, question)
    → AskingTurn ↔ wizard-input → POST /api/v1/skills/answer
    → accumulate WizardDraft → reach "done" phase
    → SkillCard list → POST /skills/{id}/approve|reject
    → needs_clarification → pushFollowUpQuestion → re-enter wizard
```

## Interfaces

- **Upstream**: `API_Server`
  - REST: `/api/v1/workflows` · `/executions` · `/nodes/catalog` ·
    `/ai/compose` (SSE) · `/skills/*` · `/auth`
  - Auth: `NEXT_PUBLIC_DEV_TOKEN` (local dev) → `Authorization: Bearer
    <token>` header
  - Dev rewrite: `next.config.mjs` proxies `/api/*` →
    `http://127.0.0.1:8000/api/*`
- **Downstream**: the user's browser

## Security notes

- **Do not retain credential-input fields in state.** Clear them
  immediately after submit. Never put passwords / tokens in stores,
  `localStorage`, or `sessionStorage`.
- API tokens (JWT) live in memory or in an `httpOnly` cookie.
  **Never use `localStorage`** (XSS exposure).
- `NEXT_PUBLIC_*` env vars are **inlined into the client bundle** —
  never put API keys / secrets there. The dev token is the one
  exception (and in production it is replaced by OAuth + a server
  session anyway).
- `.env.local` is git-ignored. Commit only `.env.example`.
- When the AI Composer / Skill Wizard renders LLM output, **do not
  raw-inject HTML or markdown** — trust React's default escaping; do
  not use `dangerouslySetInnerHTML`.
- Never send user credentials inside the workflow JSON. Input forms
  redact immediately, and the API persists the value into
  `CredentialStore`.

## Validation commands

```bash
# Typecheck + lint + build (mandatory before opening a PR)
npx tsc --noEmit
npx next lint
npx next build

# Playwright (mock-based — passes without API_Server)
npx playwright test tests/ai-composer.spec.ts tests/skill-wizard.spec.ts

# Live integration (requires API_Server uvicorn on :8000)
npx playwright test tests/smoke.spec.ts
```

## Related PLANs / memory

- `Frontend/plans/PLAN_01_WORKFLOW_EDITOR_MVP.md` — React Flow canvas +
  execution trigger + ResultDrawer
- `Frontend/plans/PLAN_02_AI_COMPOSER.md` — AI Composer 4 PRs
  (non-stream / SSE / Apply)
- `AI_Agent/plans/PLAN_12_skill_bootstrap.md` — Frontend work items
  W2-5 (interview) / W2-6 (review cards) / W3-1 (document upload —
  not started)
- Memory `feedback_hackathon_ui_english.md` — keep UI text in English
  (Kaggle judges)
- Memory `feedback_no_merge_commits_in_branch.md` — sync brand branches
  with `rebase`, never `merge`
- Memory `feedback_test_before_pr.md` — before opening a PR, all
  external checks (typecheck / lint / build / playwright) must be green
