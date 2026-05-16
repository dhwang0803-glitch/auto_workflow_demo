# PLAN_04 — Agent Daemon (customer-VPC executor)

> Status: DRAFT
> Branch: `Execution_Engine`
> Predecessors: PLAN_01 (NodeRegistry), PLAN_02 (DAG executor), PLAN_03 (Celery dispatcher)

## Purpose

A lightweight executor installed in the customer VPC. Opens a WebSocket
to the central server, receives `execute` commands → runs the workflow
locally → returns results over the WS.

The Agent has **no direct DB access** — execution state updates are
reported to the server via WS messages.

## Protocol (per CLAUDE.md)

```
Agent → Server:  heartbeat          (every 10–30 s)
Server → Agent:  execute            (workflow graph + encrypted creds)
Agent → Server:  status_update      (per-node execution state)
Agent → Server:  execution_result   (final result, metadata only)
Agent → Server:  get_credential     (credential request)
Server → Agent:  heartbeat_ack
Server → Agent:  credential         (RSA-AES encrypted credential)
```

## Core design: WebSocketExecutionRepository

The Agent has no DB, so it implements the executor's
`ExecutionRepository` interface as WS message sends. **The existing
`run_workflow()` code keeps working unchanged.**

```
run_workflow(graph, execution, ws_repo, registry)
                                ↑
                    WebSocketExecutionRepository
                    - update_status → ws.send({"type": "status_update", ...})
                    - append_node_result → ws.send({"type": "node_result", ...})
                    - finalize → ws.send({"type": "execution_result", ...})
```

## File changes

### New
| File | Role |
|------|------|
| `src/agent/__init__.py` | Empty package |
| `src/agent/main.py` | WebSocket client loop + heartbeat + command dispatch |
| `src/agent/command_handler.py` | `execute` command → call `run_workflow()` |
| `src/agent/ws_repo.py` | WebSocketExecutionRepository — report state over WS |
| `scripts/agent_run.py` | CLI entrypoint (`--server-url`, `--agent-token`) |
| `tests/test_agent.py` | Unit tests |

### Unchanged
- `src/runtime/executor.py` — no changes (Repository ABC makes this possible)

## Implementation details

### 1. src/agent/ws_repo.py — WebSocketExecutionRepository

```python
class WebSocketExecutionRepository(ExecutionRepository):
    """Report execution state to the server via WS messages — no DB."""

    def __init__(self, ws, execution: Execution):
        self._ws = ws
        self._execution = execution

    async def update_status(self, execution_id, status, *, error=None, ...):
        self._execution.status = status
        await self._ws.send(json.dumps({
            "type": "status_update",
            "execution_id": str(execution_id),
            "status": status,
            "error": error,
        }))

    async def append_node_result(self, execution_id, node_id, result, **kw):
        self._execution.node_results[node_id] = result
        await self._ws.send(json.dumps({
            "type": "node_result",
            "execution_id": str(execution_id),
            "node_id": node_id,
            "result": result,
        }))

    async def finalize(self, execution_id, *, duration_ms):
        self._execution.duration_ms = duration_ms
        await self._ws.send(json.dumps({
            "type": "execution_result",
            "execution_id": str(execution_id),
            "duration_ms": duration_ms,
            "node_results": self._execution.node_results,
        }))

    # get / list / create — unused on the agent, raise NotImplementedError
```

### 2. src/agent/main.py — main loop

```python
async def run_agent(server_url: str, token: str):
    async with websockets.connect(f"{server_url}?token={token}") as ws:
        heartbeat_task = asyncio.create_task(_heartbeat_loop(ws))
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "heartbeat_ack":
                    continue
                elif msg["type"] == "execute":
                    # Run in a separate task (supports concurrent executions)
                    asyncio.create_task(handle_execute(ws, msg, registry))
                elif msg["type"] == "credential":
                    # Handle the credential response (resolve a Future)
                    ...
        finally:
            heartbeat_task.cancel()

async def _heartbeat_loop(ws, interval=15):
    while True:
        await ws.send(json.dumps({"type": "heartbeat"}))
        await asyncio.sleep(interval)
```

### 3. src/agent/command_handler.py — handle execute

```python
async def handle_execute(ws, msg: dict, node_registry: NodeRegistry):
    execution_id = msg["execution_id"]
    graph = msg["graph"]
    execution = Execution(id=UUID(execution_id), ...)
    ws_repo = WebSocketExecutionRepository(ws, execution)
    await run_workflow(graph, execution, ws_repo, node_registry)
```

### 4. scripts/agent_run.py

```python
import asyncio, argparse
from src.agent.main import run_agent

parser = argparse.ArgumentParser()
parser.add_argument("--server-url", required=True)
parser.add_argument("--agent-token", required=True)
args = parser.parse_args()
asyncio.run(run_agent(args.server_url, args.agent_token))
```

## Test strategy

Mock the WebSocket with a pair of asyncio.Queues:
1. `test_heartbeat_sends_periodically` — verify N heartbeat messages
2. `test_execute_command_runs_workflow` — execute command → success reported
3. `test_execute_failure_reports_error` — failing node → failed reported
4. `test_ws_repo_sends_status_updates` — verify update_status / append_node_result / finalize messages
5. `test_unknown_message_ignored` — unknown message types are ignored

## Dependency addition

```toml
dependencies = [
    "httpx>=0.27",
    "celery[redis]>=5.3",
    "websockets>=12.0",
    "auto-workflow-database",
]
```

## Checklist

- [ ] `src/agent/ws_repo.py` — WebSocketExecutionRepository
- [ ] `src/agent/main.py` — WS client + heartbeat
- [ ] `src/agent/command_handler.py` — handle execute command
- [ ] `scripts/agent_run.py` — CLI entrypoint
- [ ] `pyproject.toml` — add websockets
- [ ] Write 5 tests + pass
- [ ] Commit → push → PR

## Follow-ups (API_Server branch)

- Add server-side WS handlers for `status_update` / `node_result` / `execution_result`
- In `execute_workflow()`, branch on `execution_mode=agent` → send execute to the matching Agent WS
- Idempotency: prevent duplicate execution by `execution_id`
