# Project MAP — directory & file role map

> The single source of truth for "what is this file for?". Update whenever a
> new top-level folder or file is introduced.

## Top-level layout (main branch)

```
teamlift/
├── _claude_templates/   ← per-branch CLAUDE.md templates (copied by the post-checkout hook)
├── _agent_templates/    ← agent instruction docs (per role: TDD / security / refactor)
├── .claude/commands/    ← slash-command definitions (e.g. /PR-report)
├── .githooks/           ← post-checkout hook (auto-scaffolds the branch folder on switch)
├── .github/             ← PR template, etc.
├── docs/context/        ← these docs: architecture / decisions / map
└── README.md
```

`main` contains **shared configuration only**. The actual source lives in
isolated branches: `API_Server` / `Database` / `Execution_Engine` / `Frontend`.

The `docs` branch is **wiki-only**: only `docs/context/*` edits are allowed,
and code branches reference this wiki read-only. See
[`_claude_templates/CLAUDE_docs.md`](../../_claude_templates/CLAUDE_docs.md)
for the full rules.

The `infra` branch is **infra-only** (created 2026-04-20, long-lived). It
owns cross-module operational files — Terraform HCL, deploy / runbook
scripts, GCP IAM, CI/CD workflows. Operational files that belong to a single
module (e.g. a branch's Dockerfile) stay in that module's branch. Do **not**
spin up disposable `feat/xxx` or `fix/xxx` branches for infra changes —
they go straight to PRs against `infra`.

## Per-branch layout

### `API_Server` (Core Layer — FastAPI)
```
API_Server/
├── app/
│   ├── routers/         workflows.py / executions.py / agents.py / webhooks.py
│   ├── services/        workflow_service / dag_scheduler / trigger_manager / agent_manager
│   ├── models/          Pydantic schemas (WorkflowSchema, NodeConfig …)
│   └── main.py          FastAPI app + DI wiring
├── tests/               pytest + httpx TestClient
├── config/              per-environment YAML
└── agents/              copies of _agent_templates
```
Details: [`_claude_templates/CLAUDE_API_Server.md`](../../_claude_templates/CLAUDE_API_Server.md)

### `Database` (Data Layer — PostgreSQL)
```
Database/
├── schemas/             CREATE TABLE / INDEX DDL
├── migrations/          YYYYMMDD_*.sql history
├── src/
│   ├── repositories/    Postgres{Workflow,Execution}Repository + CredentialStore
│   └── models/          SQLAlchemy ORM
├── scripts/             migrate.py / seed.py / validate.py
├── tests/               pytest against a real test DB
└── docs/                ERD, design notes
```
Details: [`_claude_templates/CLAUDE_Database.md`](../../_claude_templates/CLAUDE_Database.md)

### `Execution_Engine` (Execution Layer — Celery + Agent)
```
Execution_Engine/
├── src/
│   ├── nodes/           BaseNode + HTTP / Condition / Code + NodeRegistry
│   ├── dispatcher/      serverless.py (Celery) / agent_client.py (WS)
│   ├── runtime/         executor.py (DAG) / sandbox.py (RestrictedPython + Docker)
│   └── agent/           main / heartbeat / command_handler (customer-VPC daemon)
├── scripts/             worker.py / agent_run.py
├── tests/               pytest (per-node + integration)
├── config/              Celery config, etc.
└── docs/                node guide, sandbox design
```
Details: [`_claude_templates/CLAUDE_Execution_Engine.md`](../../_claude_templates/CLAUDE_Execution_Engine.md)

### `Inference_Service` *(planned — ADR-008)*
```
Inference_Service/
├── serving/             vLLM entrypoint, OpenAI-compatible API wrapper
├── models/              Gemma 4 weight management (download scripts, checksums)
├── config/              vLLM runtime options, quantization, tokenizer presets
├── scripts/             start_vllm.sh, warmup.py, canary_check.py
└── tests/               serving health checks, structured-output validation
```
The matching template (`_claude_templates/CLAUDE_Inference_Service.md`) and
post-checkout hook case branch are **follow-up work**. Today's structure is
the projection from the ADR-008 draft.

### `infra` (Infrastructure Layer — Terraform + GCP)
```
infra/
├── terraform/          Cloud SQL / Cloud Run / Secret Manager / VPC / IAM HCL
│   ├── main.tf         cloud sql + secret manager
│   ├── cloud_run.tf    Cloud Run v2 + AR + SA + IAM + Auth Proxy sidecar
│   ├── network.tf      VPC + service-networking peering
│   ├── variables.tf    outputs.tf / versions.tf
│   └── environments/   staging.tfvars.example / prod.tfvars.example (real values are gitignored)
├── scripts/            inject_oauth_secrets.sh / migrate_via_proxy.sh / run_e2e_workspace_node.sh
├── docs/               README.md (Cloud Run deploy runbook) / README_oauth.md (OAuth runbook)
├── agents/             infra TDD role agents (ORCHESTRATOR / DEVELOPER / TESTER / …)
├── plans/              per-phase ADR execution PLANs
├── reports/            per-phase completion reports
└── tests/              bats unit tests (static + plan validation)
```
Related ADRs: ADR-018 (Cloud SQL), ADR-019 (Google OAuth), ADR-020 (Cloud
Run deployment). Details: [`infra/CLAUDE.md`](../../infra/CLAUDE.md)

### `Frontend` (Frontend Layer — Next.js)
```
Frontend/
├── src/
│   ├── components/      WorkflowCanvas / NodePalette / NodeConfigPanel / ExecutionMonitor …
│   ├── pages/           editor/[id].tsx, executions/index.tsx
│   └── services/        workflowApi.ts / executionApi.ts / useExecutionStream.ts
├── public/
└── tests/               Jest + Playwright
```
Details: [`_claude_templates/CLAUDE_Frontend.md`](../../_claude_templates/CLAUDE_Frontend.md)

## Key file index

| File | Role |
|------|------|
| `.githooks/post-checkout` | On branch switch, scaffolds the branch's folder and copies CLAUDE.md |
| `.claude/commands/PR-report.md` | `/PR-report` slash command: security scan → stage only the branch folder → open PR |
| `_claude_templates/CLAUDE_DEFAULT.md` | Root-level shared guidelines (security rules, etc.) |
| `_agent_templates/DEVELOPER.md` | TDD Green-stage implementation agent |
| `_agent_templates/TEST_WRITER.md` | TDD Red-stage test-authoring agent |
| `_agent_templates/SECURITY_AUDITOR.md` | S01–S08 security review |
| `_agent_templates/IMPACT_ASSESSOR.md` | 4-layer impact analysis |

## Related docs

- Full architecture: [`architecture.md`](./architecture.md)
- Decision history: [`decisions.md`](./decisions.md)
