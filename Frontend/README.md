# auto_workflow Frontend

Workflow editor UI for `auto_workflow_demo`. Next.js 14 App Router + TypeScript +
Tailwind. PR A scope: scaffolding + node catalog fetch — the React Flow editor
itself lands in PR B (see `plans/PLAN_01_WORKFLOW_EDITOR_MVP.md`).

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

Visit `/` — should render the node catalog grouped by category (`network`,
`email`, `ai`, …) once auth + backend are reachable.

## Layout

- `src/app/` — App Router pages
- `src/lib/api.ts` — typed fetch wrapper (sets Bearer from `NEXT_PUBLIC_DEV_TOKEN`)
- `src/providers/query-provider.tsx` — TanStack Query client
- `next.config.mjs` — dev-only rewrite from `/api/*` to FastAPI

## Stack

`next@14.2`, `react@18`, `@tanstack/react-query@5`, `tailwindcss@3.4`. Editor
deps (`reactflow`, `zustand`, `react-hook-form`, `zod`) land with PR B.
