# PLAN — Node credential-injection pipeline (BYO + per-execution)

> **Scope**: a cross-branch blueprint. The actual implementation
> PLANs live in each branch's `plans/`.
> **Source ADR**:
> [ADR-016](./decisions.md#adr-016--노드-자격증명-주입-파이프라인-별도-plan--후속-adr-로-설계-분리)
> **Related storage / transport ADRs**: ADR-004 (Fernet at rest),
> ADR-013 (Agent delivery)
> **Status**: **COMPLETE (full end-to-end across Serverless + Agent
> segments)** — PRs #47 / #48 / #50 / #52 / #53 all merged. Only the
> GET / LIST endpoint is deferred (§5).
> **Last updated**: 2026-04-17 — added the Agent-path WS protocol and
> operations guide.

## 0. Decision summary

- **Supply model**: **BYO** — customers register their own SMTP / DB /
  Slack credentials with us. We handle storage and runtime injection
  only. SaaS sending (SendGrid / SES …) is out of scope for this
  PLAN.
- **Decryption scope**: **Per-execution** — at the moment a workflow
  is triggered, we **bulk-decrypt all required `credential_id`s in
  one shot**, merge into config, dispatch. No re-decryption per node
  invocation. Plaintext lifetime is scoped to "trigger → dispatch".

## 1. Cross-branch contract (all three branches obey)

### 1.1. `credential_ref` — declaration shape inside the workflow graph

The node's `config` carries a `credential_ref` key; the execution
pipeline removes that key and merges the decrypted result into the
config. Nodes never see `credential_ref`.

```json
{
  "id": "n1",
  "type": "email_send",
  "config": {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "from": "bot@example.com",
    "to": ["alice@example.com"],
    "subject": "hi",
    "body": "plain",
    "credential_ref": {
      "credential_id": "uuid-...",
      "inject": {
        "user":     "smtp_user",
        "password": "smtp_password"
      }
    }
  }
}
```

- `credential_id`: the `credentials.id` UUID
- `inject`: a mapping from decrypted-dict key → config key.
  E.g. decrypted `{"user": "u", "password": "p"}` + inject
  `{"user":"smtp_user","password":"smtp_password"}`
  → config gains `{"smtp_user":"u", "smtp_password":"p"}`
- After the merge, the pipeline **drops the original
  `credential_ref` key** → it is absent in the dict the node sees

### 1.2. `credential_type` catalog (MVP)

| type | Required dict keys | Used by |
|------|--------------------|---------|
| `smtp` | `host`, `port`, `user`, `password` | email_send |
| `postgres_dsn` | `dsn` *or* `host`+`port`+`user`+`password`+`database` | (planned) db_query |
| `slack_webhook` | `url` | slack_notify (optional — passing `webhook_url` directly is still allowed today) |
| `http_bearer` | `token` | http_request (for Authorization header injection) |

- `credentials.type` starts as **text + CHECK constraint**, not an
  enum (more flexible). Extend the set via Database migrations.
- Per-type dict-key validation is performed **only in API_Server's
  credential-registration router** (Database only stores / decrypts
  the JSON blob).

### 1.3. `CredentialStore.bulk_retrieve` — new Database method

```python
async def bulk_retrieve(
    self, credential_ids: list[UUID], *, owner_id: UUID
) -> dict[UUID, dict]:
    """Return the decrypted plaintext dicts, keyed by credential_id.
    Drop credentials whose owner_id does not match (prevents
    cross-tenant leakage).
    Raise KeyError if any credential_id is missing — partial
    resolution is not allowed.
    """
```

- **Ownership filter required** — `WHERE owner_id = :owner_id AND id
  = ANY(:ids)`
- Any missing ID fails the whole call (no partial success → prevents
  workflows from running with partial credentials)
- The existing single-shot `retrieve(credential_id)` stays — the API
  uses it for post-registration validation

### 1.4. Add `credentials.type` column — Database migration

```sql
ALTER TABLE credentials
    ADD COLUMN type text NOT NULL DEFAULT 'unknown';
ALTER TABLE credentials
    ADD CONSTRAINT credentials_type_known
    CHECK (type IN ('smtp', 'postgres_dsn', 'slack_webhook', 'http_bearer', 'unknown'));
```

- Backfill existing rows as `unknown` (test fixtures aside, prod
  shouldn't have any).
- New rows must come in with an explicit type from the API.

### 1.5. Agent mode — reuses ADR-013 (no change)

- `retrieve_for_agent(credential_id, agent_public_key_pem)` is
  already implemented.
- The Agent-mode dispatcher does **not** call `bulk_retrieve`.
  Instead it calls `retrieve_for_agent` **per credential_id**,
  collects the hybrid-encrypted payloads, and pushes them to the
  Agent over WS. The Agent decrypts inside its VPC, then merges
  into config.
- In other words: **"the server never sees plaintext; the Agent
  receives ciphertext only"** principle is preserved.

### 1.6. Security invariants (every PLAN obeys)

1. Plaintext credentials exist **only inside the scope between
   `workflow_service.execute_workflow` entry and the Celery
   `send_task` / Agent WS push**. Never returned, logged, or
   exception-leaked.
2. **Never inline plaintext in the workflow graph** — go through
   `credential_ref`. (Requests like "I just want to send one email,
   registration is annoying" are deferred to a later phase.)
3. Execution audit logs (`execution_node_logs` or the future
   `credential_audit`) record only `credential_id` — never
   plaintext or decryption results.
4. Even when an execution that used a credential fails, the
   credential must not surface in logs — error messages need
   sanitizing.

## 2. Implementation PR breakdown + ordering

> **Update (2026-04-17)** — §2 originally described "API_Server
> resolves `credential_ref` inside execute_workflow", but during
> implementation that turned out to be infeasible with our current
> architecture: Celery `send_task` carries only the `execution_id`,
> and the Worker re-reads the graph from DB. So even if API_Server
> injected plaintext into in-memory data, it wouldn't reach the
> Worker. Putting plaintext into Celery args instead would violate
> invariant 1 (§1.6) by routing plaintext through the Redis broker.
>
> **Revised responsibility split:**
> - **API_Server (PLAN_07, merged)** — credential CRUD plus, inside
>   `execute_workflow`, **validation-only** `bulk_retrieve(ids,
>   owner_id)` (verify existence + ownership, then drop plaintext
>   immediately). It does not perform plaintext injection.
> - **Execution_Engine (PLAN_08, new — promoted from what was
>   originally ③'s "~10 LOC" footnote)** — inject `CredentialStore`
>   into `WorkerContainer`; `_execute()` calls `bulk_retrieve` right
>   before invoking each node, merges plaintext into `config`, and
>   strips the `credential_ref` key. Serverless path only. The Agent
>   path still uses the ADR-013 passthrough (server never sees
>   plaintext).
>
> This redistribution keeps plaintext **out of broker and DB** —
> invariant 1 (§1.6) is preserved.

> **Update (2026-04-17, Agent path closed out)** — In Agent mode the
> server attaches `credential_payloads` (ciphertext) to the WS
> message; the Agent decrypts with its private key inside the VPC,
> then runs the same merge. Implemented in API_Server PLAN_08 (PR
> #52) + Execution_Engine PLAN_10 (PR #53). **Both Serverless and
> Agent paths now satisfy §1.6** (plaintext never touches the
> broker, DB, or any wire). The WS protocol is in §2.5; the
> operations guide is in §2.6.

```
┌──────────────────────────────────────────────────────────────┐
│ ① Database/plans/PLAN_09_CREDENTIAL_PIPELINE_DB.md  [DONE]   │
│    PR #47 merged — migration 20260601 + bulk_retrieve        │
│    ~40 LOC + 1 migration + 13 tests                          │
└──────────────────────────────────────────────────────────────┘
                             │  (bulk_retrieve API frozen)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ ② API_Server PLAN_07 (validation) + PLAN_08 (Agent payload)  │
│    PR #48 merged — /credentials CRUD + execute validation    │
│    PR #52 merged — Agent execute WS now carries              │
│    credential_payloads (retrieve_for_agent + b64). Includes  │
│    a silent bug fix (agent_connections or {} → is not None). │
└──────────────────────────────────────────────────────────────┘
                             │
                   ┌─────────┴─────────┐
                   ▼                   ▼
┌────────────────────────┐  ┌──────────────────────────────────┐
│ ③ Serverless path      │  │ ④ Agent path                     │
│   Execution_Engine     │  │   Execution_Engine                │
│   PLAN_08 [DONE]       │  │   PLAN_10 [DONE]                  │
│   PR #50 merged        │  │   PR #53 merged                   │
│   Worker decrypts      │  │   In-VPC hybrid_decrypt          │
│   directly via         │  │   (PreDecryptedStore wrapper)    │
│   FernetStore          │  │   → reuses the same              │
│   → resolve_credential │  │     resolve_credential_refs       │
│   _refs → plaintext    │  │                                  │
│   into nodes           │  │                                  │
└────────────────────────┘  └──────────────────────────────────┘
```

### 2.5. Agent WS `execute` message — added fields (frozen in PR #52 + #53)

When the server sends an `execute` to the Agent, it adds a
`credential_payloads` array on top of the existing fields (`type`,
`execution_id`, `workflow_id`, `graph`). Each item is the
base64-encoded ADR-013 hybrid-encryption envelope:

```json
{
  "type": "execute",
  "execution_id": "<uuid>",
  "workflow_id": "<uuid>",
  "graph": { ... },
  "credential_payloads": [
    {
      "credential_id": "<uuid>",
      "wrapped_key":   "<b64 RSA-OAEP-SHA256-wrapped AES-256 key>",
      "nonce":         "<b64 12-byte AES-GCM nonce>",
      "ciphertext":    "<b64 AES-GCM ciphertext of the JSON plaintext>"
    }
  ]
}
```

If the graph has no `credential_ref`, send `credential_payloads:
[]`. On the Agent side, if the graph has refs but `payloads` is
empty or the private key is missing, the Agent records the
execution as `failed` with the generic message
`"credential resolution failed"` — never exposing any
credential_id in the error.

### 2.6. Agent-deploy operations guide (PLAN_10)

The Agent daemon generates an RSA keypair inside the customer's
VPC:

1. **Keypair generation** (one-time, inside the VPC): RSA 2048 or
   higher. The private key lives on the VPC filesystem with mode
   600. Never exposed externally.
2. **Public-key registration**: the customer sends the public-key
   PEM via `POST /api/v1/agents/register` → stored on the
   `agents.public_key` column + JWT issued. The server uses that
   public key for `retrieve_for_agent` to wrap the AES key.
3. **Daemon run**: `python scripts/agent_run.py --server-url
   <wss://...> --agent-token <JWT> --agent-private-key <PEM path>`
   — the private key is loaded once at daemon boot and lives only
   in process memory. The file path only appears in argv.
4. **Key rotation** (Phase 2): no rotation mechanism today —
   replacing the key requires re-registering the Agent. Automation
   is a follow-up ADR.

### PR dependencies (frozen)
① (PR #47) → ② validation side (PR #48) → ③ Worker (PR #50) + ②'
agent payload (PR #52) → ④ Agent (PR #53)

Once these 5 PRs are merged, every node that uses `credential_ref`
— Email / Slack / DB Query / future LLM nodes — works end-to-end on
both Serverless and Agent paths.

## 3. Questions each branch PLAN must answer

### Database PLAN_09
- Does `bulk_retrieve` decrypt everything with a single Fernet key?
  (→ Yes, keep the current structure.)
- Missing `credential_id` → whole call fails. Error type?
  (→ Same `KeyError` as the existing `retrieve`.)
- Backfill existing rows to `unknown` in the migration — prod
  probably has none, but include it defensively.

### API_Server PLAN_07 [RESOLVED — PR #48]
- `/credentials` endpoints: `POST /api/v1/credentials` +
  `DELETE /{id}`. GET / LIST is **deferred** until
  `CredentialStore.list_by_owner()` exists (Database supplement).
- Per-type dict-key validation: Pydantic `Literal` enforces enum-
  level only. Field-presence enforcement waits for Phase 2 (after
  Frontend UX is locked).
- Plaintext appears only in the request body; responses return
  `{id, name, type}` only.
- `credential_ref` collection scope is **depth 1** — only the
  `config.credential_ref` of each node. Nested declarations are
  not allowed today.
- Agent-mode dispatch payload is out of scope for this PR.
  Execution_Engine PLAN_08 handles it via the ADR-013 path (the
  server bundles the `retrieve_for_agent` results into the WS
  message for the Agent).

### Execution_Engine PLAN_08 [RESOLVED — PR #50]
- Resolution timing: **per-execution, in bulk at the top of
  `_execute()`**. `resolve_credential_refs` copies the graph →
  `bulk_retrieve` → deep-copy → inject plaintext per the mapping →
  remove the `credential_ref` key.
- The original `workflow.graph` stays immutable — no plaintext in
  retries or logs.
- Resolution failure (`KeyError` from `bulk_retrieve`): mark
  execution `failed` with the generic message
  `"credential resolution failed"` (no `credential_id` exposure).
  If `CredentialStore` is `None` but the graph has refs, use
  `"credential store not configured"`.

### Execution_Engine PLAN_10 (Agent path) [RESOLVED — PR #53]
- The Agent daemon decrypts each WS-message `credential_payloads`
  entry with `hybrid_decrypt`.
- A `PreDecryptedCredentialStore` wrapper lets us reuse
  `resolve_credential_refs` — `owner_id` is ignored (the server has
  already filtered).
- The private key is fed via `--agent-private-key <PEM path>` CLI.
  Never appears in storage / logs / responses.
- Decryption failure (wrong key, tampered ciphertext) → the same
  generic `"credential resolution failed"` message, no
  `credential_id` exposed.

### API_Server PLAN_08 (Agent payload bundling) [RESOLVED — PR #52]
- In the Agent branch of `workflow_service.execute_workflow`, loop
  `retrieve_for_agent` per ID → re-wrap with the Agent's public key
  → base64 → embed into the WS message's `credential_payloads`.
- Server-side plaintext exists only inside this re-wrap loop. Never
  leaks to broker / DB / log / response.
- **Silent bug fix**: the `agent_connections or {}` falsy-empty-dict
  bug was corrected — before this PR, Agent dispatch did not
  actually work. Now `if agent_connections is not None else {}`.

## 4. Test invariants (every PR covers)

- **No-leak test** — execution-failure responses, audit logs, and
  error messages contain no plaintext credential strings.
- **Ownership test** — using another user's `credential_id` in the
  graph causes resolution failure.
- **`credential_ref` removal test** — the config that reaches a
  node's `execute` has no `credential_ref` key.
- **Agent-mode passthrough test** — the server doesn't log /
  persist plaintext; it forwards ciphertext as-is to the Agent.

## 5. Out of scope (explicit)

- **Credential rotation / expiry** — Phase 2. Today: DELETE +
  re-register (no UPDATE).
- **SaaS sending (SendGrid / SES, etc.)** — ADR-016 model B.
  Needs its own PLAN.
- **Frontend credential-registration UI** — when the Frontend
  branch starts, it consumes this PLAN's `/credentials` API as-is.
- **Credential sharing / team permissions** — Phase 2. Today
  ownership is per single user.
- **LLM-node (ADR-007) API-key injection** — reuses this pipeline.
  When the LLM PLAN adds `credential_type=llm_api_key`, it just
  works.

## 6. Derived doc locations

- Database: `Database/plans/PLAN_09_CREDENTIAL_PIPELINE_DB.md` (PR
  #47 merged)
- API_Server validation:
  `API_Server/plans/PLAN_07_CREDENTIAL_PIPELINE.md` (PR #48 merged)
- API_Server Agent payload:
  `API_Server/plans/PLAN_08_AGENT_CREDENTIAL_PAYLOAD.md` (PR #52
  merged)
- Execution_Engine worker resolution:
  `Execution_Engine/plans/PLAN_08_CREDENTIAL_RESOLUTION.md` (PR #50
  merged)
- Execution_Engine Agent decrypt:
  `Execution_Engine/plans/PLAN_10_AGENT_CREDENTIAL_DECRYPT.md` (PR
  #53 merged)
- If this blueprint changes, consider back-porting into the ADR-016
  Update section.
