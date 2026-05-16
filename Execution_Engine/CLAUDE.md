# Execution_Engine — Claude Code branch guide

> Applied alongside the security rules in the root `CLAUDE.md`.

## Module role

**Execution Layer** — the engine that actually runs workflow nodes.
Supports two execution modes:

1. **Serverless Worker**: Celery + Redis queue → Cloud Run container
   (Light / Middle users)
2. **Agent**: a lightweight runner installed in the customer's VPC,
   connected to the central server over WebSocket (Heavy users)

Both modes share the same `BaseNode` plugin interface and `NodeRegistry`.

## File-location rules (MANDATORY)

```
Execution_Engine/
├── src/
│   ├── nodes/            ← BaseNode implementations (plugins, import-only)
│   │   ├── base.py           ← BaseNode ABC
│   │   ├── http_request.py   ← HttpRequestNode
│   │   ├── condition.py      ← ConditionNode
│   │   ├── code.py           ← CodeExecutionNode (sandbox required)
│   │   └── registry.py       ← NodeRegistry
│   ├── dispatcher/       ← execution dispatchers
│   │   ├── serverless.py     ← Celery task dispatch
│   │   └── agent_client.py   ← WebSocket client (Agent → server)
│   ├── runtime/          ← DAG execution runtime
│   │   ├── executor.py       ← per-level node execution (parallel via asyncio.gather)
│   │   └── sandbox.py        ← RestrictedPython / Docker isolation
│   └── agent/            ← Agent daemon (installed inside the customer VPC)
│       ├── main.py           ← Agent entrypoint
│       ├── heartbeat.py      ← liveness signal
│       └── command_handler.py ← receive / execute server commands
├── scripts/      ← worker.py, agent_run.py (run directly)
├── tests/        ← pytest (per-node unit + integration)
├── config/       ← Celery config, default node parameters
└── docs/         ← node-development guide, sandbox design notes
```

| File kind | Location |
|-----------|----------|
| Node implementations (subclass `BaseNode`) | `src/nodes/` |
| Celery tasks / Agent client | `src/dispatcher/` |
| DAG execution runtime | `src/runtime/` |
| Agent daemon (deployed to the customer VPC) | `src/agent/` |
| Celery worker entrypoint | `scripts/worker.py` |
| Agent entrypoint | `scripts/agent_run.py` |
| pytest | `tests/` |

**Do not create `.py` files directly at the `Execution_Engine/` root or
the project root.**

## Tech stack

```python
from celery import Celery
import redis.asyncio as redis
import httpx                      # HTTP node
import websockets                 # Agent ↔ Server
from RestrictedPython import compile_restricted  # code-node sandbox
from cryptography.hazmat.primitives.asymmetric import rsa  # Agent keypair
```

## Plugin extension (adding a new node)

```python
from src.nodes.base import BaseNode
from src.nodes.registry import registry

class SlackSendMessageNode(BaseNode):
    @property
    def node_name(self) -> str:
        return "slack_send_message"

    async def execute(self, input_data, parameters):
        ...

registry.register(SlackSendMessageNode)
```

Adding a node requires a matching `tests/nodes/test_{node_name}.py`.

## Entrypoints per run mode

```bash
# Serverless worker (our cloud)
python scripts/worker.py --queue workflow_tasks --concurrency 10

# Agent (deployed inside the customer VPC)
python scripts/agent_run.py --agent-key <KEY> --server-url wss://api.example.com/agents/ws
```

## Sandbox (CodeExecutionNode)

**Never call `eval()` / `exec()` directly.**

- 1st defense: `RestrictedPython` for AST inspection + builtin allowlist
- 2nd defense: run inside an isolated Docker container (network / FS
  restricted)
- A timeout is mandatory (default 30 s)

## Agent communication protocol

| Direction | Message | Purpose |
|-----------|---------|---------|
| Agent → Server | `register` | Initial handshake (agent_key → JWT) |
| Agent → Server | `heartbeat` | Liveness signal every 10–30 s |
| Server → Agent | `execute` | `AgentCommand` (workflow JSON + encrypted creds) |
| Agent → Server | `status_update` | Per-node execution state |
| Agent → Server | `execution_result` | Final result (metadata only — bulk data stays in the VPC) |

**Idempotency**: every `execute` message must guard against duplicate
execution via its `execution_id`.

## Interfaces

- **Upstream**: `API_Server` — receives execution commands via the
  Celery queue or WebSocket
- **Downstream**:
  - `Database` — store execution metadata (via `ExecutionRepository`)
  - External services — the actual APIs that nodes call

## Test-execution rules (MANDATORY)

1. **Keep exactly one test process at a time** — kill the previous one
   before starting a new run:
   ```bash
   taskkill //F //IM python.exe 2>/dev/null; python -m pytest tests/ -v
   ```
2. In the **fail → fix → rerun** loop, leaving the previous failure
   running stacks up zombie processes that hog CPU / memory and slow
   later tests.
3. **No background execution (`run_in_background`)** — we want results
   immediately, so run in the foreground and read output directly.
4. **Beware infinite-loop tests** — CPU-bound infinite loops like
   `while True: pass` cannot be killed from a Python thread. Replace with
   a bounded loop (`range(10**8)`) so the thread exits on its own.

## Security notes

- Decrypt credentials **only at execution time**, inject them as node
  parameters, then drop them immediately.
- The Agent must **not** leak internal customer-VPC data outward
  (metadata only over the wire).
- Custom code nodes always run through the sandbox.
