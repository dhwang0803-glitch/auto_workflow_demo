# Architecture — 4-Layer Workflow Automation Engine

> Single entry-point for the project's overall structure. Detail
> branches into each module's `CLAUDE.md`.

## Layer overview

```
┌─────────────────────────────────────────────────────────┐
│  Frontend Layer  (Next.js + React Flow)                 │
│  - Node-based workflow-editor UI                        │
│  - Realtime execution-status subscription via WebSocket │
└────────────────────────┬────────────────────────────────┘
                         │ REST + WebSocket
┌────────────────────────▼────────────────────────────────┐
│  Core Layer  (FastAPI / API_Server)                     │
│  - Workflow CRUD, DAG scheduling, trigger watching      │
│  - Coordinates dispatch to Execution_Engine and Agents  │
└──────────┬──────────────────────────────┬───────────────┘
           │ Celery queue                  │ WebSocket
┌──────────▼──────────────┐   ┌───────────▼───────────────┐
│ Serverless Worker       │   │ Agent (in customer VPC)   │
│ (Cloud Run + Celery)    │   │ - Dedicated runner for    │
│ - Light / Middle users  │   │   Heavy users             │
│                         │   │ - Handles data that can't │
│                         │   │   leave the VPC           │
└──────────┬──────────────┘   └───────────┬───────────────┘
           │                               │
┌──────────▼───────────────────────────────▼───────────────┐
│  Data Layer  (PostgreSQL 16 + Redis)                     │
│  - Abstracted by Repository pattern (no direct SQL)      │
│  - Credentials stored AES-256 (Fernet) encrypted         │
└──────────────────────────────────────────────────────────┘
```

## Branch ↔ layer mapping

| Layer | Branch | Detailed guide |
|-------|--------|----------------|
| Frontend | `Frontend` | [`_claude_templates/CLAUDE_Frontend.md`](../../_claude_templates/CLAUDE_Frontend.md) |
| Core | `API_Server` | [`_claude_templates/CLAUDE_API_Server.md`](../../_claude_templates/CLAUDE_API_Server.md) |
| Execution | `Execution_Engine` | [`_claude_templates/CLAUDE_Execution_Engine.md`](../../_claude_templates/CLAUDE_Execution_Engine.md) |
| Data | `Database` | [`_claude_templates/CLAUDE_Database.md`](../../_claude_templates/CLAUDE_Database.md) |
| Inference | `Inference_Service` *(planned)* | vLLM + Gemma 4 serving. Template TBD. |
| Wiki (this doc set) | `docs` | [`_claude_templates/CLAUDE_docs.md`](../../_claude_templates/CLAUDE_docs.md) |

## Execution modes (hybrid SaaS)

Two execution paths, selected by `workflow.settings.execution_mode`:

- **`serverless`** — Light / Middle users. Celery → Redis queue →
  Cloud Run container.
- **`agent`** — Heavy users. The agent inside the customer VPC keeps
  a long-lived WebSocket to the server and the server **pushes**
  `execute` commands.

Both modes share the same `BaseNode` plugin interface and
`NodeRegistry`.

## Key data flows

### 1. Workflow create / run (serverless)
```
Frontend ──POST /api/v1/workflows──▶ API_Server
                                      │
                                      ├─▶ DAGScheduler (Kahn topo sort + cycle check)
                                      ├─▶ WorkflowRepository.save (Database)
                                      └─▶ Celery.enqueue → Worker (Execution_Engine)
                                              │
                                              ├─ CredentialStore.retrieve (only at execution time)
                                              │
                                              └─ Node.execute (parallel: asyncio.gather)
                                                    │
                                                    ├─ ExecutionNodeLogRepository.record_start  (running)
                                                    │
                                                    ├─ [stdout/stderr → GCS upload, only the URI in DB]
                                                    │
                                                    ├─ ExecutionNodeLogRepository.record_finish (success|failed|skipped)
                                                    │      + write the 4 LLM fields (model / tokens / cost) to typed columns
                                                    │
                                                    └─ ExecutionRepository.append_node_result   (latest summary only)
                                                           + update_status
```

Execution detail lives **solely in `execution_node_logs` (monthly
partitions)**; `executions.node_results` only carries the latest
attempt summary. The role split between these two tables and the
2-phase write (start → finish UPDATE) rationale is in
[`decisions.md` ADR-011](./decisions.md). A row exists while the
node is in `running` state, so the Frontend can render progress
animation in realtime (ADR-007 Update 2026-04-15).

### 2. Agent-mode execution
```
Agent (customer VPC) ──dial-out WSS──▶ API_Server   (agent_key → JWT, long-lived socket)
Agent ──heartbeat (10–30s)─────▶ API_Server

Trigger fires → API_Server ──execute(workflow, credential_refs)──▶ Agent
                                                                    │
                                                                    │  (right before a node that needs a credential)
                                                                    ▼
Agent ──get_credential(cred_id)──▶ API_Server
                                        │
                                        ├─ CredentialStore.retrieve_for_agent
                                        │    ├─ Fernet decrypt (master key)
                                        │    └─ Hybrid re-encrypt:
                                        │        AES-256-GCM(payload) + RSA-OAEP-SHA256(AES key)
                                        │        → {wrapped_key, nonce, ciphertext}
                                        ▼
API_Server ──credential_payload──▶ Agent
                                        │
                                        ├─ RSA private key decrypts wrapped_key → AES key
                                        ├─ AES-256-GCM decrypts payload + verifies tag
                                        ├─ Plaintext lives only in process memory for the execution
                                        ├─ Node.execute (inside the VPC)
                                        └─ Zeroize on execution end
                                        ▼
Agent ──status_update / execution_result──▶ API_Server
       (metadata only; the original data stays in the VPC)
```

Core principles (details:
[`decisions.md` ADR-004 / ADR-013](./decisions.md)):

- **Network direction is always Agent → server outbound** — even when
  corporate firewalls block inbound TCP, the long-lived WebSocket the
  Agent dialed out reuses the open path.
- **Credential delivery is pull-based** — the Agent sends a
  `get_credential` frame right before executing a node, and the
  server returns a frame with the hybrid-encrypted payload. The
  initial `execute` frame does **not** bundle credentials
  (least-privilege / least-exposure).
- **No DB / server cache** — re-encrypt on every request. Adding an
  API_Server in-process cache later is fine — the
  `retrieve_for_agent` function is designed as a pure function so
  the option is preserved.
- **Agent's local cache lives only for the execution** — reusing the
  same credential within one run avoids a round-trip; on execution
  end, zeroize.

### 3. Webhook trigger
```
External service ──POST /webhooks/{workflow_id}/{path}──▶ API_Server
                  (HMAC signature check)                  │
                                                          └─▶ Execution dispatch (paths 1 or 2 above)
```

## LLM-inference layer

LLM inference is handled by a **separate `Inference_Service` layer**,
not `Execution_Engine`. The introduction rationale is in
[`decisions.md` ADR-008](./decisions.md).

![Gemma 4 + vLLM deployment strategy](./images/gemma4_vllm_deployment_strategy.svg)

### Backend routing per plan

| Plan | Backend | Model |
|------|---------|-------|
| Light | External API | Claude / Gemini |
| Middle | External API (shared pool) | Claude / Gemini |
| Heavy (Serverless) | **Central `Inference_Service`** | Gemma 4 26B MoE / 31B Dense (fp8) |
| Heavy (Agent, GPU available) | In-Agent vLLM (E4B) — **Phase 2** | Phase 1 uses central or external |
| Heavy (Agent, CPU-only) | In-Agent KTransformers — **Phase 2** | For customers with no GPU + no external-API allowance (ADR-009) |

Routing is **fixed by user plan**. We do not change the backend
based on a runtime complexity judgment (ADR-008). Only **within
Agent mode** does `LLMRouter` branch one more step on the customer's
hardware (`gpu_info`, AMX support) between vLLM / KTransformers /
external API (ADR-009).

### Fallback rules (plan-independent, operational safety net)

1. N consecutive `output_schema` validation failures on the local
   model → retry with a larger local model (26B → 31B).
2. Total local-infra outage → fall back to the external API + raise
   an alert.

### Call flow

```
Execution_Engine / LlmNode
        │
        ├─ look up user plan (via API_Server)
        │
        ├─ Light / Middle → external-API adapter (OpenAI / Anthropic / Gemini)
        │
        └─ Heavy (Serverless) → HTTP POST /v1/chat/completions
                                 (OpenAI-compatible endpoint)
                                 │
                                 ▼
                        Inference_Service
                        (vLLM + Gemma 4)
                        │
                        ├─ native structured-output support (tool_call_parser=gemma4)
                        └─ response → LlmNode validates output_schema
```

## AI / LLM node design principles

LLM nodes are **not** subclassed from `HttpRequestNode` — they form a
first-class abstraction, `LlmNode`. Rationale:
[`decisions.md` ADR-007](./decisions.md).

Core rules:

1. **Structured Output is mandatory** — every node may declare an
   `output_schema` (JSON Schema string); `LlmNode` **requires** it.
   The runtime validates the result and retries on failure (default
   2 attempts); a remaining failure is recorded as a structural
   error.
2. **Narrow scope** — LLM nodes do one thing: extraction /
   classification / summarization / transformation. Complex
   reasoning is decomposed into multiple `LlmNode` + `ConditionNode`
   combos. (Normative guidance.)
3. **Human-in-the-loop (`ApprovalNode`)** — a first-class node that
   pauses execution and waits for a human approval. The MVP delivers
   **the web inbox + notifications (email / Slack)** at the same
   time.
4. **Cost / latency observability** — execution history accumulates
   `token_usage`, `cost_usd`, `duration_ms`, `paused_at_node`.

### Approval flow (summary)

```
DAG run … → ApprovalNode
              │
              ├─ execution status = paused, paused_at_node = N
              │
              ├─ NotificationChannel.send (email + slack)
              │     │
              │     ├─ ApprovalNotificationRepository.record per attempt
              │     │   (append-only audit trail, independent status)
              │     │
              │     └─ message link: /approve/{execution_id}
              │
              └─ wait … (minutes to days)
                    │
                    ▼
           user action (web inbox or notification link)
                    │
              ┌─── web inbox = `executions WHERE status='paused'` query ───┐
              │    (no dedicated table, pagination only)                    │
              └─────────────────────────────────────────────────────────────┘
                    │
              POST /api/v1/executions/{id}/approve | reject
                    │
                    ▼
           runtime resumes (idempotent): runs the downstream nodes
           or takes the failure branch
```

Core design principles:

- **Notification dispatch failures are isolated from the Approval
  state machine** — an SMTP / Slack outage doesn't perturb
  `executions.status`. Dispatch history sits in
  `approval_notifications` with `status='failed'`, and an
  operational dashboard watches for "notifications missing for 24h+"
  separately. Details: [`decisions.md` ADR-012](./decisions.md).
- **The inbox is a read-only path** — it is just a paginated query
  over `executions WHERE status='paused'`, with no separate
  storage. Pending naturally caps at human-processing speed;
  Resolved fits a date-range filter + keyset pagination.
- **Approval lifetime is decoupled from ADR-005's 30 s hard
  timeout** — the code-node sandbox timeout stays as is, but paused
  executions live in a separate no-expiry state.

## Plugin extension points

- **Add a new node**: write a `BaseNode` subclass under
  `Execution_Engine/src/nodes/` → `registry.register()` → required
  test in `tests/nodes/test_{name}.py`.
- **Add a new Repository implementation**: implement the ABC under
  `Database/src/repositories/`; the test suite must be able to swap
  it with `InMemoryXxxRepository`.

## Related docs

- Decision rationale: [`decisions.md`](./decisions.md)
- File / directory map: [`MAP.md`](./MAP.md)
