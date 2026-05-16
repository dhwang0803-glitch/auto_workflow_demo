# Frontend — Claude Code branch guide

> Applied alongside the root `CLAUDE.md` security rules.

## Related documents

- Full architecture / REST + WebSocket flow: [`docs/context/architecture.md`](../docs/context/architecture.md)
- Decision rationale: [`docs/context/decisions.md`](../docs/context/decisions.md)
- File map: [`docs/context/MAP.md`](../docs/context/MAP.md)
- Downstream dependency (API consumer): [`CLAUDE_API_Server.md`](./CLAUDE_API_Server.md)

## Module role

**Workflow editor UI** — a web client where the user places nodes on a canvas and connects
them with lines to visually compose automation workflows.
The result is serialized to JSON and sent to `API_Server`.

Owns the **Frontend Layer** of the 4-layer architecture.

## File layout rules (MANDATORY)

```
Frontend/
├── src/
│   ├── components/   ← reusable UI components (not directly executed)
│   ├── pages/        ← page routes
│   └── services/     ← API_Server client (REST + WebSocket)
├── public/           ← static assets
└── tests/            ← Jest / Playwright
```

| File kind | Storage location |
|-----------|------------------|
| Canvas / node components (`WorkflowCanvas`, `NodePalette`, etc.) | `src/components/` |
| Pages (`editor/[id].tsx`, `executions/index.tsx`, etc.) | `src/pages/` |
| API clients (`workflowApi.ts`, `executionApi.ts`) | `src/services/` |
| Real-time execution-state subscription hook | `src/services/useExecutionStream.ts` |
| Jest unit tests | `tests/` |

**Do not create source files directly under `Frontend/` or the project root.**

## Tech stack

```typescript
// Framework
Next.js 14 (App Router) + TypeScript + Tailwind CSS

// Core libraries
import ReactFlow from 'reactflow';         // node-based canvas
import { useWebSocket } from 'src/services/useExecutionStream';  // real-time logs
```

## Core components

| Component | Role |
|-----------|------|
| `WorkflowCanvas` | React Flow-based node/edge editing canvas |
| `NodePalette` | Draggable node list (queried from NodeRegistry) |
| `NodeConfigPanel` | Parameter editing for the selected node |
| `ExecutionMonitor` | Execution history + per-node real-time state |
| `CredentialManager` | Credential registration / management UI (no plaintext storage) |
| `AgentStatus` | Customer VPC Agent connection-state dashboard |

## Main flows

```
Workflow editing:
  NodePalette → drag onto WorkflowCanvas
    → configure parameters in NodeConfigPanel
    → [Save] POST /api/v1/workflows (JSON serialization)
    → [Run]  POST /api/v1/workflows/{id}/execute

Execution monitoring:
  Subscribe to WebSocket /api/v1/executions/{id}/stream
    → real-time per-node state updates (pending/running/success/failed)
    → render in <ExecutionMonitor> timeline
```

## Interfaces

- **Upstream**: `API_Server` — REST API (CRUD, execution triggers) + WebSocket (execution log stream)
- **Downstream**: the user's browser

## Security notes

- Credential input forms must **not retain values in frontend state long-term**. Reset immediately after submission.
- Keep API tokens (JWT) only in `httpOnly` cookies or memory. Do not use `localStorage`.
