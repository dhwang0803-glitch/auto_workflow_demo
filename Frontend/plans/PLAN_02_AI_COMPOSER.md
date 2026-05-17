# PLAN_02 — AI Composer (Frontend + API_Server)

> **Branch**: `Frontend` + `API_Server` integration · **Authored**: 2026-04-21 · **Status**: Draft
>
> When the user types an intent in natural language, the AI selects and
> connects nodes from the catalog and produces a workflow draft (DAG
> draft). Instead of completing in one shot, it follows
> **clarification dialogue → draft → diff-based iterative refinement**.
> This is a layer on top of the PLAN_01 editor; the draft always
> renders in the editor, and the user can edit it by hand.

## 1. Goals

1. Natural-language input panel (left Chat sidebar) — laid out separately from the editor
2. **Clarification step** — for ambiguous specs, the LLM asks first (data source / address book / template, etc.)
3. **DAG draft generation** — once the spec is locked, the LLM picks nodes from the catalog + connects + emits config hints
4. **Editor injection** — load the draft into the React Flow store, user can edit
5. **Iterative refinement** — "change this part" → LLM returns a diff → partially applied to the editor
6. **Rationale SSE streaming** — only the LLM's explanation (rationale) is typed into the chat bubble token by token. The DAG/diff is sent as one shot after completion
7. **Agent integration** — registered as a single system Agent (`composer-agent`), consistent with the existing Agent_Management

## 2. Scope

**In**
- `API_Server/app/routers/ai_composer.py` — REST endpoint for natural-language → DAG generation + SSE streaming endpoint
- `API_Server/app/services/ai_composer_service.py` — Claude API call (stream=True) + node-catalog context injection + prompt management
- Frontend `ChatPanel` — natural-language input + clarification dialogue UI + rationale typing effect
- Frontend reuse of the `loadFromJson` store action (the hook exposed in PLAN_01)
- Diff viewer — visualize node/edge changes between the current DAG and the proposed DAG
- Prompt template — include node catalog + config schema in the system prompt
- **SSE parser** — Frontend `fetch` + `ReadableStream` receives `rationale` token chunks → append to chat bubble
- Tests:
  - API_Server: unit tests for composer_service with a Claude API mock (both stream/non-stream)
  - Frontend: 1 Playwright E2E "natural-language input → rationale streaming → draft received → renders on the canvas"

**Out (follow-up PLANs)**
- LLM diversification (OpenAI/Gemini) — MVP is Claude 4.7 only
- Per-user template learning/storage — PLAN_11 Template System
- Fine-tuning / embedding-based node recommendation — future
- Execution-log-driven self-repair (LLM fixes the DAG on failure) — future
- Voice input — future

## 3. Core scenario (user story)

> User: "Pull yesterday's Korean stock market overview and news, build a report in docs, and email it to executives via gmail"

**Step 1 — Clarification (LLM asks)**
- "Which stock data source should I use? (KRX official API / Yahoo Finance / Naver Finance)"
- "Is the executive address book managed as a group in Gmail contacts, or do you enter it manually?"
- "Do you have a docs report template? If not, I can propose a default."

**Step 2 — DAG draft (after the user answers)**
```
[http_request: Yahoo Finance Korean index for yesterday]
[http_request: news API (Naver or Google News)]
  → [anthropic_chat: summarize both inputs → produce report body]
    → [google_docs_append_text: create new doc + fill body]
      → [gmail_send: to=executive list, subject="...", body=doc link]
```

**Step 3 — Editor injection**
- The draft renders on the React Flow canvas (auto layout)
- The user can edit node properties directly (e.g., enter recipient emails)
- Incomplete config is shown with a **yellow badge** (placeholder values)

**Step 4 — Iterative refinement**
- User: "Use Slides instead of Docs"
- LLM: returns a diff replacing only that node with `google_slides_create_presentation`
- Frontend: highlights the diff and shows **Accept/Reject** buttons

## 4. Backend API spec

### `POST /api/v1/ai/compose` (SSE streaming)

**Request**
```json
{
  "session_id": "uuid (optional — generated server-side on first request)",
  "message": "yesterday's stock overview...",
  "current_dag": { "nodes": [...], "edges": [...] } | null
}
```

**Response** — `Content-Type: text/event-stream`. 3 event kinds:

```
event: rationale_delta
data: {"token": "This "}

event: rationale_delta
data: {"token": "workflow "}

... (token accumulation) ...

event: result
data: {
  "session_id": "...",
  "intent": "draft | clarify | refine",
  "clarify_questions": ["...", "..."] | null,
  "proposed_dag": { "nodes": [...], "edges": [...] } | null,
  "diff": {
    "added_nodes": [...],
    "removed_node_ids": [...],
    "modified_nodes": [...]
  } | null,
  "rationale": "full accumulated rationale (for delta-sum verification)"
}

event: error
data: {"code": "rate_limit_exceeded", "message": "..."}
```

**Streaming strategy**:
- Anthropic SDK `stream=True` + tool use or JSON response
- **Prompt the model to emit rationale first** — have the model output a `<rationale>...</rationale>` block first and emit the tokens inside it as `rationale_delta`
- Tokens after `<rationale>` closes accumulate in the JSON buffer → once complete, parse and send as a single `result` event
- DAG/diff parsing failure → `error` event + close the stream

**Non-stream fallback**: `?stream=false` query flag for testing/debugging. In that case, return the existing JSON response in one shot

- **Stateful session**: `session_id` keeps the dialogue history (reuses Redis, Memorystore)
- **Rate limit**: 10 / minute per user (LLM cost guard)
- **Auth**: reuse the existing `Depends(get_current_user)`
- **Cancellation**: if the client disconnects, the server handles `asyncio.CancelledError` and closes the Anthropic stream

### Node catalog context size

- 30+ nodes × ~1KB avg (schema + description) = ~30KB → fits directly in the system prompt
- For expansion (100+ nodes), consider switching to embedding-based RAG — separate PLAN

## 5. Prompt structure

```
[SYSTEM]
You are a workflow automation agent. You may only pick from the node catalog below.
If the user's request is ambiguous, ask questions before building the DAG.
Output must conform to the JSON Schema. (intent, clarify_questions, proposed_dag, diff, rationale)

<node_catalog>
{json_dump_of_catalog}
</node_catalog>

<current_dag>
{user's current workflow or null}
</current_dag>

[USER]
{user message}

[ASSISTANT — JSON]
```

- **Use prompt caching** (Anthropic SDK `cache_control`) — the node catalog is invariant within a session, so cache it
- Cap `max_tokens` reasonably (4k) — a DAG usually has fewer than 10 nodes

## 6. Frontend structure

- `src/components/ChatPanel.tsx` — left 250px fixed panel (toggleable)
- `src/lib/composer.ts` — SSE client for `/api/v1/ai/compose`
  - Parse `event: ...` / `data: ...` frames with `fetch` + `ReadableStream` + `TextDecoder`
  - `rationale_delta` → incremental append to the chat bubble
  - `result` → invoke the DAG/diff receive callback
  - `error` → toast + close the stream
  - Cancellation = `AbortController.abort()` — when the user clicks stop
- `src/store/composer.ts` — Zustand slice (session_id, messages, streaming_rationale, pending_diff)
- Clarification questions render as **chat bubbles**, the user's answer is free text
- During rationale typing, a **blinking cursor** sits at the end of the bubble (explicit UX signal)
- On DAG draft receive: `editorStore.loadFromJson(proposed_dag)` + auto layout
- On diff receive: highlight diff nodes in **green (added) / red (removed) / yellow (modified)** + Accept/Reject buttons

## 7. Security / cost guardrails

- **LLM calls must go through the backend** — never expose the Claude API key on the frontend
- **Anthropic API key in Secret Manager** — do not reuse the existing credential store (do not mix with user credentials; operator-owned)
- **Rate limit**: 10/minute and 200/day per user (environment-variable override allowed)
- **Cost telemetry**: log each call's `input_tokens / output_tokens` → Cloud Logging
- **Prompt injection prevention**: pass the user message only via the user role (not the system prompt) + strict JSON schema validation

## 8. Acceptance criteria

- [ ] `POST /api/v1/ai/compose` SSE endpoint works (tested with a Claude API mock)
- [ ] `?stream=false` fallback returns a single JSON response
- [ ] User message → intent=`clarify` response scenario passes
- [ ] User message → intent=`draft` response + a valid DAG (passes server-side `dag_validator`)
- [ ] With `current_dag` present → intent=`refine` + diff generated
- [ ] Multiple SSE `rationale_delta` events followed by one `result` event
- [ ] Client `AbortController.abort()` → server-side Anthropic stream confirmed closed
- [ ] Input on the Frontend ChatPanel → rationale typing effect → DAG renders on the canvas
- [ ] On diff receive, Accept applies to the editor, Reject ignores
- [ ] Empty user message returns 400 / no auth returns 401
- [ ] Rate-limit exceeded returns 429 (HTTP header response before the SSE stream begins)

## 9. Open questions

1. **Session store** — MVP in-memory (Python dict) vs. Redis. in-memory loses sessions when Cloud Run scales out. **Adopted: Redis (Memorystore already exists)** — reuse ADR-021 Memorystore
2. **SSE streaming scope** — streaming DAG/diff too creates partial JSON parsing issues → **stream rationale only, send DAG/diff as one shot after completion** (locked 2026-04-21). Cost: backend +0.5d, frontend +1d
3. **LLM call failure fallback** — on timeout / rate limit, show the user a "retry later" message + leave the editor unchanged
4. **Relationship to the Agent Framework** — should `composer-agent` be registered as a system agent in the existing `agents` table, or stand as a separate service. **Adopted: separate service** — the composer does not execute against user VPCs, so do not mix it into the Agent table (the service is still named `composer-agent` to keep naming consistent)

## 10. Downstream impact

- **PLAN_07 Credentials UI** — if the AI-generated DAG contains `secret_ref` fields, the user must wire them up via the Credentials UI. Note the dependency
- **PLAN_11 Template System** — store frequently used composer outputs as templates per user. The AI recommends past templates first
- **Cost Guard** — an operational switch is needed to auto-cut off once daily Anthropic cost exceeds $X. Candidate for a new ADR
- Independent of **ADR-022 (Frontend stack)**, consider **ADR-023 (AI Composer)** — LLM dependency is an architectural decision
