# Local Testing — AI Composer (PLAN_02)

PR A/B/C are merged but neither of them proves the whole flow in a browser.
This doc is the shortest recipe to drive the ChatPanel end-to-end **without
spending a single Anthropic token** — `StubLLMBackend` returns deterministic
canned responses based on keywords in the user message.

When you want to test against real Claude, just unset the stub flag and set
a real `ANTHROPIC_API_KEY` (§4 below).

## 1. Prerequisites

- Postgres running at `localhost:5435` (the URL already baked into
  `API_Server/.env`). If you use the project's docker-compose, that's port
  5435; otherwise adjust `DATABASE_URL`.
- Execution_Engine installed editable: `pip install -e ../Execution_Engine`
  (needed so `build_node_catalog_provider` can import `src.nodes`).

## 2. Start API_Server with stub backend

Append one line to `API_Server/.env`:

```
AI_COMPOSER_USE_STUB=true
```

Then:

```bash
cd API_Server
uvicorn app.main:app --reload --port 8000
```

You should see the standard startup log. `POST /api/v1/ai/compose` now
answers from `StubLLMBackend` — no key required, no network calls.

### Stub rules (intent selection)

| Message shape | Intent | What you get back |
|---|---|---|
| ends with `?` or starts with `what/who/which/where/how` | `clarify` | 3 canned questions |
| non-empty `current_dag` in the request | `refine` | diff that updates the first node's `url` |
| otherwise | `draft` | 2-node skeleton (`http_request` → `gmail_send`) |

## 3. Mint a dev JWT (skip register/login)

`Frontend/.env.local` carries `NEXT_PUBLIC_DEV_TOKEN` so the UI can call the
API without a login round-trip. The token has a 60-minute TTL and rots
between sessions — re-mint it from `API_Server/`:

```bash
python scripts/mint_dev_token.py
```

This upserts `dev@local.test` (verified), mints a fresh access JWT, and
rewrites only the `NEXT_PUBLIC_DEV_TOKEN` line (other env vars preserved).
Restart `pnpm dev` after running it so Next.js picks up the new value.

If you skip this and your `pnpm dev` returns `{"detail":"invalid token"}`
on first API call, the token has expired — re-run the script.

## 4. Start Frontend

In a second terminal:

```bash
cd Frontend
pnpm install  # first time only
pnpm dev      # next dev on :3000 — proxies /api/* to :8000
```

## 5. Drive the flow

1. Open `http://localhost:3000`
2. Register / login (or reuse a session — the `.env.local` `NEXT_PUBLIC_DEV_TOKEN` is still honored)
3. Click **+ New workflow**
4. Click **AI Composer** in the toolbar — the left chat panel slides in
5. Try the three shapes:
   - Type `Which node should I start with?` → clarify bubble with 3 questions
   - Type `Fetch data and email it to the team` → draft bubble, click **Apply** → canvas gets populated with 2 nodes + edge
   - With nodes on the canvas, type `Change the URL` → refine bubble, **Apply** replaces the canvas with the proposed DAG
6. Save / Execute work as normal (ADR-021 inline mode — DAG runs synchronously in the POST handler)

## 6. Switching to real Claude

When you want real LLM behavior:

```
# .env
AI_COMPOSER_USE_STUB=false           # or remove the line
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6    # default, override if needed
```

Restart uvicorn. Everything else is identical — the Protocol guarantees
the wire format.

## 7. SSE streaming (PR D preview)

PR D hasn't landed yet — the ChatPanel uses the JSON-once path for now.
You can still exercise the streaming endpoint by hand:

```bash
curl -N -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  "http://localhost:8000/api/v1/ai/compose?stream=true" \
  -d '{"message": "Fetch data and email it"}'
```

You'll see `event: session` → `event: rationale_delta` frames (one every
~40ms from the stub) → `event: result`.

## 8. Gotchas

- **Tests require Postgres** — they hit the auth stack. `DATABASE_URL`
  must be exported or present in `.env`.
- **Rate limit** — defaults to 10/min/user. If you're mashing Enter, you
  may trip it; override with `AI_COMPOSE_RATE_PER_MINUTE=60` in `.env`.
- **Stub does not validate node types against the catalog** — the
  2-node skeleton always uses `http_request` + `gmail_send`. If your
  Execution_Engine registry doesn't register those types, the canvas will
  render the nodes but Save may still work because the backend doesn't
  re-check node types against the registry at save time (validation runs
  at execution).
