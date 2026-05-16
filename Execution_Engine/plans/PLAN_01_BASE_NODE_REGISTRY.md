# PLAN_01 — Package setup + BaseNode + NodeRegistry + HttpRequestNode

> **Branch**: `Execution_Engine` · **Written**: 2026-04-16 · **Status**: Draft
>
> First PLAN for Execution_Engine. Sets up the package infrastructure,
> the skeleton of the node plugin system (BaseNode ABC + NodeRegistry),
> and the first implementation (HttpRequestNode).

## 1. Scope

**In**
- `pyproject.toml` — package metadata + dependencies (httpx, auto-workflow-database)
- `pytest.ini` — asyncio_mode=auto
- `src/nodes/__init__.py`
- `src/nodes/base.py` — `BaseNode` ABC (`node_type`, `execute`)
- `src/nodes/registry.py` — `NodeRegistry` (dict-based type→class mapping)
- `src/nodes/http_request.py` — `HttpRequestNode` (external API calls via httpx)
- `tests/test_http_request_node.py` — 4 tests

**Out**
- DAG executor (runtime/) — PLAN_02
- Celery dispatcher — PLAN_03
- Agent daemon — PLAN_04
- ConditionNode, CodeNode — PLAN_05

## 2. BaseNode ABC

```python
class BaseNode(ABC):
    @property
    @abstractmethod
    def node_type(self) -> str: ...

    @abstractmethod
    async def execute(self, input_data: dict, config: dict) -> dict: ...
```

- `input_data`: output of the previous node (empty dict for the first node)
- `config`: per-node config from the workflow graph
- return: output dict (passed to the next node as `input_data`)
- On failure raise an exception — the executor catches and handles it

## 3. NodeRegistry

```python
class NodeRegistry:
    def register(self, node_class: type[BaseNode]) -> None
    def get(self, node_type: str) -> type[BaseNode]
    def list_types(self) -> list[str]

registry = NodeRegistry()  # module-level singleton
```

## 4. HttpRequestNode

- `node_type = "http_request"`
- config: `{"method": "GET", "url": "...", "headers": {}, "body": {}}`
- Issue the request via `httpx.AsyncClient`, return the response as `{"status_code": N, "body": ..., "headers": ...}`
- timeout: `timeout_seconds` from config (default 30)

## 5. Tests

1. `test_http_request_get_happy` — mock server GET → 200
2. `test_http_request_post_with_body` — POST + JSON body
3. `test_http_request_timeout` — exception when timeout exceeded
4. `test_registry_register_and_get` — register + lookup + list_types

## 6. Avoid function sprawl

- `BaseNode.execute` is the single method. No `validate_config`, `pre_execute`, or `post_execute` hooks.
- `HttpRequestNode.execute` runs the httpx call and response transform inline.
- `NodeRegistry` is a dict wrapper — no magic like `_validate` or `_auto_discover`.
