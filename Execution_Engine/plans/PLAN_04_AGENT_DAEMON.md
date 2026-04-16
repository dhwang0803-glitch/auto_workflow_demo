# PLAN_04 — Agent Daemon (고객 VPC 실행기)

> 상태: DRAFT  
> 브랜치: `Execution_Engine`  
> 선행: PLAN_01 (NodeRegistry), PLAN_02 (DAG executor), PLAN_03 (Celery dispatcher)

## 목적

고객 VPC에 설치되는 경량 실행기. 중앙 서버와 WebSocket으로 연결하여
`execute` 커맨드를 수신 → 로컬에서 워크플로우 실행 → 결과를 WS로 반환.

Agent는 **DB 직접 접근 없음** — 실행 상태 업데이트를 WS 메시지로 서버에 보고.

## 프로토콜 (CLAUDE.md 기준)

```
Agent → Server:  heartbeat          (10~30초 주기)
Server → Agent:  execute            (workflow graph + encrypted creds)
Agent → Server:  status_update      (노드별 실행 상태)
Agent → Server:  execution_result   (최종 결과, 메타데이터만)
Agent → Server:  get_credential     (자격증명 요청)
Server → Agent:  heartbeat_ack
Server → Agent:  credential         (RSA-AES 암호화 자격증명)
```

## 핵심 설계: WebSocketExecutionRepository

Agent는 DB가 없으므로, executor의 `ExecutionRepository` 인터페이스를
WS 메시지 전송으로 구현. **기존 `run_workflow()` 코드 변경 없이** 동작.

```
run_workflow(graph, execution, ws_repo, registry)
                                ↑
                    WebSocketExecutionRepository
                    - update_status → ws.send({"type": "status_update", ...})
                    - append_node_result → ws.send({"type": "node_result", ...})
                    - finalize → ws.send({"type": "execution_result", ...})
```

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/agent/__init__.py` | 빈 패키지 |
| `src/agent/main.py` | WebSocket 클라이언트 루프 + heartbeat + 커맨드 디스패치 |
| `src/agent/command_handler.py` | `execute` 커맨드 → `run_workflow()` 호출 |
| `src/agent/ws_repo.py` | WebSocketExecutionRepository — WS로 상태 보고 |
| `scripts/agent_run.py` | CLI 진입점 (`--server-url`, `--agent-token`) |
| `tests/test_agent.py` | 단위 테스트 |

### 미수정
- `src/runtime/executor.py` — 변경 없음 (Repository ABC 덕분)

## 구현 상세

### 1. src/agent/ws_repo.py — WebSocketExecutionRepository

```python
class WebSocketExecutionRepository(ExecutionRepository):
    """DB 없이 WS 메시지로 실행 상태를 서버에 보고."""

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

    # get / list / create — agent에서 미사용, NotImplementedError
```

### 2. src/agent/main.py — 메인 루프

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
                    # 별도 task로 실행 (동시 실행 지원)
                    asyncio.create_task(handle_execute(ws, msg, registry))
                elif msg["type"] == "credential":
                    # credential 응답 처리 (Future resolve)
                    ...
        finally:
            heartbeat_task.cancel()

async def _heartbeat_loop(ws, interval=15):
    while True:
        await ws.send(json.dumps({"type": "heartbeat"}))
        await asyncio.sleep(interval)
```

### 3. src/agent/command_handler.py — execute 처리

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

## 테스트 전략

WebSocket을 asyncio.Queue 쌍으로 모킹:
1. `test_heartbeat_sends_periodically` — heartbeat 메시지 N회 확인
2. `test_execute_command_runs_workflow` — execute 커맨드 → success 보고
3. `test_execute_failure_reports_error` — 실패 노드 → failed 보고
4. `test_ws_repo_sends_status_updates` — update_status/append_node_result/finalize 메시지 검증
5. `test_unknown_message_ignored` — 알 수 없는 메시지 타입 무시

## 의존성 추가

```toml
dependencies = [
    "httpx>=0.27",
    "celery[redis]>=5.3",
    "websockets>=12.0",
    "auto-workflow-database",
]
```

## 체크리스트

- [ ] `src/agent/ws_repo.py` — WebSocketExecutionRepository
- [ ] `src/agent/main.py` — WS 클라이언트 + heartbeat
- [ ] `src/agent/command_handler.py` — execute 커맨드 처리
- [ ] `scripts/agent_run.py` — CLI 진입점
- [ ] `pyproject.toml` — websockets 추가
- [ ] 테스트 5개 작성 + pass
- [ ] 커밋 → push → PR

## 후속 작업 (API_Server 브랜치)

- 서버 WS에 `status_update` / `node_result` / `execution_result` 수신 핸들러 추가
- `execute_workflow()`에서 `execution_mode=agent` 분기 → 해당 Agent WS로 execute 전송
- 멱등성: execution_id 기반 중복 실행 방지
