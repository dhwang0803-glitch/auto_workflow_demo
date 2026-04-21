# auto_workflow Frontend

Workflow editor UI for `auto_workflow_demo`. Next.js 14 App Router + TypeScript +
Tailwind + React Flow. See `plans/PLAN_01_WORKFLOW_EDITOR_MVP.md` for scope.

## Dev setup

```bash
# from repo root
cd Frontend
pnpm install

# copy env template and fill in NEXT_PUBLIC_DEV_TOKEN
cp .env.example .env.local

# in another shell, boot API_Server (uvicorn on :8000) — Frontend rewrites
# /api/* → http://127.0.0.1:8000 in dev so CORS doesn't apply.

pnpm dev   # http://localhost:3000
```

Visit `/` — should render the workflow list with a "+ New workflow" button.
The editor lives at `/workflows/new` and `/workflows/{id}`.

## Layout

- `src/app/` — App Router pages
- `src/components/editor/` — React Flow editor (Toolbar, NodePalette, WorkflowCanvas, PropertyPanel, ResultDrawer, CustomNode)
- `src/store/editor-store.ts` — Zustand + zundo store (nodes/edges/undo/redo/activeExecutionId)
- `src/lib/api.ts` — typed fetch wrapper (sets Bearer from `NEXT_PUBLIC_DEV_TOKEN`)
- `src/lib/auto-layout.ts` — dagre LR auto-layout
- `src/lib/dag.ts` — cycle-detection helper
- `src/providers/query-provider.tsx` — TanStack Query client
- `next.config.mjs` — dev-only rewrite from `/api/*` to FastAPI

## Smoke test (local)

Playwright drives a browser through the create→save→execute flow. It assumes
both servers (uvicorn + `pnpm dev`) and a real user token are available.

```bash
# one-time: install browser binaries (~300MB)
pnpm exec playwright install chromium

# then, with API_Server on :8000 and .env.local configured:
pnpm test:smoke
```

The runner reuses an already-running dev server if one is listening on :3000;
otherwise it starts one via `webServer` in `playwright.config.ts`.

## Stack

`next@14.2`, `react@18`, `reactflow@11.11`, `@tanstack/react-query@5`,
`zustand@5` + `zundo@2`, `dagre@0.8`, `tailwindcss@3.4`,
`@playwright/test@1.59` (dev).
