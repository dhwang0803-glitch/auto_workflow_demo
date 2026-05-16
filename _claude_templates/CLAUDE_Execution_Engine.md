# Execution_Engine — Claude Code branch guide

> Applied alongside the root `CLAUDE.md` security rules.

## Related documents

- Full architecture / hybrid execution modes: [`docs/context/architecture.md`](../docs/context/architecture.md)
- Hybrid SaaS rationale (ADR-001), sandbox design (ADR-005): [`docs/context/decisions.md`](../docs/context/decisions.md)
- File map: [`docs/context/MAP.md`](../docs/context/MAP.md)
- Upstream dependency: [`CLAUDE_API_Server.md`](./CLAUDE_API_Server.md)
- Downstream dependency: [`CLAUDE_Database.md`](./CLAUDE_Database.md)

## Module role

**Execution Layer** — the engine that actually runs workflow nodes.
Supports both execution modes:

1. **Serverless Worker**: Celery + Redis queue → runs in Cloud Run containers (Light/Middle users)
2. **Agent**: a lightweight executor installed inside the customer VPC, connected to the central server over WebSocket (Heavy users)

Both modes share the same `BaseNode` plugin interface and `NodeRegistry`.

## File layout rules (MANDATORY)

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
│   │   ├── executor.py       ← stage-by-stage node execution (asyncio.gather parallel)
│   │   └── sandbox.py        ← RestrictedPython / Docker isolation
│   └── agent/            ← Agent daemon (installed in customer VPC)
│       ├── main.py           ← Agent entry point
│       ├── heartbeat.py      ← liveness ping
│       └── command_handler.py ← receive / execute server commands
├── scripts/      ← worker.py, agent_run.py (directly executed)
├── tests/        ← pytest (per-node unit + integration)
├── config/       ← Celery config, default node parameters
└── docs/         ← node-development guide, sandbox-design doc
```

| File kind | Storage location |
|-----------|------------------|
| Node implementations (inherit `BaseNode`) | `src/nodes/` |
| Celery tasks / Agent client | `src/dispatcher/` |
| DAG execution runtime | `src/runtime/` |
| Agent daemon (deployed to customer VPC) | `src/agent/` |
| Celery Worker run | `scripts/worker.py` |
| Agent run | `scripts/agent_run.py` |
| pytest | `tests/` |

**Do not create `.py` files directly under `Execution_Engine/` or the project root.**

## Tech stack

```python
from celery import Celery
import redis.asyncio as redis
import httpx                      # HTTP node
import websockets                 # Agent ↔ server
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

When adding a node, you must also write `tests/nodes/test_{node_name}.py`.

## Per-mode entry points

```bash
# Serverless Worker (our cloud)
python scripts/worker.py --queue workflow_tasks --concurrency 10

# Agent (deployed to customer VPC)
python scripts/agent_run.py --agent-key <KEY> --server-url wss://api.example.com/agents/ws
```

## Sandbox (CodeExecutionNode)

**Never use `eval()` / `exec()` directly.**

- First defense: AST inspection via `RestrictedPython` + builtin function whitelist
- Second defense: execution in an isolated Docker container (network / FS restrictions)
- Timeout required (default 30 sec)

## Agent communication protocol

| Direction | Message | Purpose |
|-----------|---------|---------|
| Agent → Server | `register` | Initial connection (agent_key → JWT) |
| Agent → Server | `heartbeat` | Liveness ping every 10–30 sec |
| Server → Agent | `execute` | AgentCommand (workflow JSON + encrypted creds) |
| Agent → Server | `status_update` | Per-node execution state |
| Agent → Server | `execution_result` | Final result (metadata only; large data stays inside the VPC) |

**Idempotency**: Every `execute` message must prevent duplicate execution via `execution_id`.

## Interfaces

- **Upstream**: `API_Server` — receives execution commands via Celery queue or WebSocket
- **Downstream**:
  - `Database` — stores execution-result metadata (via ExecutionRepository)
  - External services — the real APIs nodes call

## Security notes

- Credentials are decrypted **only at execution time**, injected as node parameters, then discarded
- The Agent must not leak customer VPC internal data outside (transmit metadata only)
- Custom code nodes must pass the sandbox before execution
