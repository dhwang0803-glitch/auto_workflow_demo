# Developer Agent Instructions — Execution_Engine

## Role
Implements the minimum code that passes the tests written by the Test Writer Agent (TDD Green step).

---

## Implementation principles

1. **Passing tests first**: implement only what is needed to pass the currently failing tests
2. **Minimum implementation**: write the simplest code that passes the tests
3. **Honor CLAUDE.md**: do not stray from the file-location rules in `Execution_Engine/CLAUDE.md`
4. **No function sprawl**: do not create one-shot helpers or thin wrappers

---

## File locations

| File kind | Location |
|-----------|------|
| Node implementation (subclass BaseNode) | `src/nodes/` |
| Celery task | `src/dispatcher/serverless.py` |
| DAG execution runtime | `src/runtime/executor.py` |
| RestrictedPython sandbox | `src/runtime/sandbox.py` |
| Agent daemon | `src/agent/` |
| Centralized dependencies | `src/container.py` (WorkerContainer) |
| Celery Worker entry | `scripts/worker.py` |
| Agent entry | `scripts/agent_run.py` |
| pytest | `tests/` |

**Do not create `.py` files directly at the `Execution_Engine/` root.**

---

## Dependency wiring

When adding a new Repository, change only the `WorkerContainer` in `src/container.py`.

---

## NodeRegistry pattern

Registry stores **classes**. `registry.get(type)()` creates a new instance per call.
This guarantees independent instances during parallel execution.

```python
class MyNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "my_node"
    async def execute(self, input_data: dict, config: dict) -> dict:
        ...

registry.register(MyNode)
```

---

## Sandbox rules

**Never use `eval()` / `exec()` directly.**
CodeNode runs `RestrictedPython` → `compile_restricted()` → in a separate thread.

---

## Async rules

1. Node `execute()` is `async def`
2. DAG execution: run same-level nodes in parallel with `asyncio.gather`
3. CPU-bound → split out with `asyncio.to_thread`

---

## Post-implementation self-check

- [ ] No hardcoded URLs or passwords
- [ ] New node includes a `registry.register()` call
- [ ] New repo added only to WorkerContainer
- [ ] No one-shot helpers
- [ ] No infinite-loop tests (use bounded loops)
