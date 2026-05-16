# PLAN_05 — ConditionNode + CodeNode (RestrictedPython sandbox)

> Status: DRAFT
> Branch: `Execution_Engine`
> Predecessors: PLAN_01 (BaseNode/Registry), PLAN_02 (DAG executor)

## Purpose

Support workflow branching (ConditionNode) and user-defined code
execution (CodeNode). For CodeNode, **never call eval()/exec() directly**
— guarantee safe execution via RestrictedPython AST inspection + builtin
allowlist.

## File changes

### New
| File | Role |
|------|------|
| `src/nodes/condition.py` | ConditionNode — conditional branching |
| `src/nodes/code.py` | CodeNode — sandboxed execution via RestrictedPython |
| `src/runtime/sandbox.py` | RestrictedPython compile + execution helper |
| `tests/test_condition_node.py` | ConditionNode tests |
| `tests/test_code_node.py` | CodeNode + sandbox tests |

### Modified
| File | Change |
|------|--------|
| `pyproject.toml` | Add `RestrictedPython` dependency |

## Implementation details

### 1. ConditionNode (`src/nodes/condition.py`)

Evaluate the condition against input_data → return `"result": true/false`.
The executor's edge system forwards the output to downstream nodes, so
downstream nodes can branch on `input_data["result"]`.

Supported operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`

```python
class ConditionNode(BaseNode):
    node_type = "condition"

    async def execute(self, input_data, config):
        left = input_data.get(config["left_field"])
        op = config["operator"]
        right = config["right_value"]
        # Per-operator comparison → {"result": bool}
```

### 2. sandbox.py (`src/runtime/sandbox.py`)

A single function that compiles and runs user code with RestrictedPython.
The timeout is bounded with `asyncio.wait_for`.

```python
def run_restricted(code: str, inputs: dict, *, timeout_seconds: int = 30) -> dict:
    byte_code = compile_restricted(code, '<user_code>', 'exec')
    safe_globals = {
        "__builtins__": safe_builtins,
        "_getiter_": default_guarded_getiter,
        "_getattr_": default_guarded_getattr,
        "inputs": dict(inputs),
        "result": {},
    }
    exec(byte_code, safe_globals)
    return safe_globals["result"]
```

**Allowed**: basic operators, loops, conditionals, string manipulation, math
**Blocked**: import, open, eval, exec, __import__, os, sys, subprocess

### 3. CodeNode (`src/nodes/code.py`)

```python
class CodeNode(BaseNode):
    node_type = "code"

    async def execute(self, input_data, config):
        code = config["source"]
        timeout = config.get("timeout_seconds", 30)
        return await asyncio.wait_for(
            asyncio.to_thread(run_restricted, code, input_data, timeout_seconds=timeout),
            timeout=timeout,
        )
```

Use `asyncio.to_thread` to run in a separate thread → avoids blocking
the main event loop. `asyncio.wait_for` enforces the overall timeout.

## Test strategy

### test_condition_node.py
1. `test_eq_true` — equal values → result=True
2. `test_eq_false` — different values → result=False
3. `test_gt_operator` — numeric comparison
4. `test_contains_operator` — substring check
5. `test_missing_field_returns_false` — field absent from input → False

### test_code_node.py
1. `test_simple_computation` — `result["sum"] = inputs["a"] + inputs["b"]`
2. `test_loop_and_list` — uses a loop
3. `test_import_blocked` — `import os` → CompileError
4. `test_open_blocked` — `open("/etc/passwd")` → blocked
5. `test_timeout_exceeded` — infinite loop → TimeoutError

## Dependency addition

```toml
dependencies = [
    "httpx>=0.27",
    "celery[redis]>=5.3",
    "websockets>=12.0",
    "RestrictedPython>=7.0",
    "auto-workflow-database",
]
```

## Checklist

- [ ] `src/runtime/sandbox.py` — RestrictedPython execution function
- [ ] `src/nodes/condition.py` — ConditionNode + registry registration
- [ ] `src/nodes/code.py` — CodeNode + registry registration
- [ ] `pyproject.toml` — add RestrictedPython
- [ ] Write 10 tests + pass
- [ ] Commit → push → PR

## Follow-ups

- After PLAN_05, refactor the Container (API_Server + Execution_Engine)
- Expand additional node types (Slack, Email, DB Query, etc.)
