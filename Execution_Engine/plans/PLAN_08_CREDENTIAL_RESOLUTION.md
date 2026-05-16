# PLAN_08 — Credential Resolution (Execution_Engine portion)

> Blueprint: [`docs/context/PLAN_credential_pipeline.md`](../../docs/context/PLAN_credential_pipeline.md) §2 Update
> Predecessors: Database PLAN_09 (PR #47) — `bulk_retrieve`. API_Server PLAN_07 (PR #48) — validation.
> Follow-up: API_Server Agent-path `credential_payloads` support (cross-branch follow-up, separate PR)

## Purpose

Just before invoking a node, the Serverless Worker resolves
`credential_ref` and merges the plaintext into `config`, passing the
node a config with `credential_ref` removed. Plaintext exists only in
**Worker process memory** (never crosses broker/DB).

## File changes

### New
| File | Role |
|------|------|
| `src/runtime/credentials.py` | `resolve_credential_refs(graph, store, owner_id)` — clones the graph and injects plaintext |
| `tests/test_credential_resolution.py` | Resolution-logic unit tests |
| `tests/test_dispatcher_credentials.py` | E2E that `_execute()` calls `run_workflow` after resolution |

### Modified
| File | Change |
|------|--------|
| `src/container.py` | `WorkerContainer` carries a `credential_store` field (production: Fernet, tests: injected/None) |
| `src/dispatcher/serverless.py` | `_execute()` accepts `credential_store` and resolves before run_workflow |

### Out of scope (explicit)
- **Agent path** — reuses ADR-013 hybrid transport. API_Server includes
  the ciphertext bundle in the WS message via `retrieve_for_agent` →
  Agent `command_handler` does the same merge after `hybrid_decrypt`
  inside the VPC. **A WS payload change on the API_Server side is part
  of it**, so this lives in a cross-branch follow-up, not this PR.
- If a graph with credential_ref arrives via the Agent path in the
  current PR, the node simply fails with missing config — same as
  today's behavior (still no credential support).

## Implementation details

### 1. `resolve_credential_refs(graph, store, owner_id)` — pure resolver

```python
async def resolve_credential_refs(
    graph: dict,
    store: CredentialStore,
    owner_id: UUID,
) -> dict:
    # Walk nodes, collect credential_ref.credential_id
    ids: list[UUID] = []
    for node in graph.get("nodes", []):
        ref = (node.get("config") or {}).get("credential_ref")
        if ref and "credential_id" in ref:
            ids.append(UUID(ref["credential_id"]))
    if not ids:
        return graph  # no work, return input (executor treats as immutable anyway)

    decrypted = await store.bulk_retrieve(ids, owner_id=owner_id)

    # Deep copy + in-place mutation of the copy. Keeps the input graph
    # pristine so retries / logs don't accidentally show resolved plaintext.
    import copy
    resolved = copy.deepcopy(graph)
    for node in resolved.get("nodes", []):
        cfg = node.get("config") or {}
        ref = cfg.get("credential_ref")
        if not ref:
            continue
        cid = UUID(ref["credential_id"])
        plaintext = decrypted[cid]
        inject = ref.get("inject", {})
        for src_key, dst_key in inject.items():
            cfg[dst_key] = plaintext[src_key]
        cfg.pop("credential_ref", None)
    return resolved
```

**Design choices:**
- **Per-execution resolution**: one `bulk_retrieve`, then merge across the entire graph. Matches blueprint Q2.
- **Deep copy**: keeps the original `workflow.graph` immutable (no plaintext in retries/logs).
- **Propagate `bulk_retrieve` KeyError**: should not happen in the normal path since API_Server already validated. Defensively caught in dispatch and turned into execution failed.
- **Missing inject dict or missing key → propagate KeyError**: a workflow-graph design error → fail-fast.

### 2. WorkerContainer extension

```python
class WorkerContainer:
    def __init__(
        self,
        *,
        exec_repo: ExecutionRepository | None = None,
        wf_repo: WorkflowRepository | None = None,
        node_registry: NodeRegistry | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        if exec_repo is not None and wf_repo is not None:
            # Test mode
            self.exec_repo = exec_repo
            self.wf_repo = wf_repo
            self.node_registry = node_registry or default_registry
            self.credential_store = credential_store
            self._engine = None
            return

        # Production mode
        engine = build_engine(os.environ["DATABASE_URL"])
        sm = build_sessionmaker(engine)
        self._engine = engine
        self.exec_repo = PostgresExecutionRepository(sm)
        self.wf_repo = PostgresWorkflowRepository(sm)
        self.node_registry = node_registry or default_registry
        master_key = os.environ.get("CREDENTIAL_MASTER_KEY", "").encode("utf-8")
        self.credential_store = (
            FernetCredentialStore(sm, master_key=master_key) if master_key else None
        )
```

If the `CREDENTIAL_MASTER_KEY` env var is **absent**, set
`credential_store = None` — lets the Worker run without credentials in
dev. If the graph contains `credential_ref`, dispatch fails explicitly.

### 3. `_execute()` resolution integration

```python
async def _execute(
    execution_id: str,
    *,
    exec_repo: ExecutionRepository,
    wf_repo: WorkflowRepository,
    node_registry: NodeRegistry,
    credential_store: CredentialStore | None = None,
) -> None:
    eid = UUID(execution_id)
    execution = await exec_repo.get(eid)
    if execution is None: ...
    workflow = await wf_repo.get(execution.workflow_id)
    if workflow is None: ...

    try:
        if credential_store is not None:
            graph = await resolve_credential_refs(
                workflow.graph, credential_store, workflow.owner_id
            )
        else:
            graph = workflow.graph
            # Defensive: graph has refs but no store available
            if _graph_has_credential_refs(graph):
                await exec_repo.update_status(
                    eid, "failed",
                    error={"message": "credential store not configured"},
                )
                return
    except KeyError:
        # bulk_retrieve failure (shouldn't happen post API_Server validation,
        # but race condition with credential DELETE between validation and
        # Worker pickup is possible). Generic message — no id leakage.
        await exec_repo.update_status(
            eid, "failed", error={"message": "credential resolution failed"},
        )
        return

    await run_workflow(graph, execution, exec_repo, node_registry)
```

## Test strategy

### test_credential_resolution.py (pure function, no DB)
1. `test_no_refs_returns_original` — graph without credential_ref → returned unchanged
2. `test_single_ref_injects_and_strips` — verify resolution of one node's credential_ref (injection succeeds + credential_ref key removed)
3. `test_multiple_refs_bulk_resolve` — credential_ids across multiple nodes are processed in one `bulk_retrieve`
4. `test_owner_filter_propagates` — using a different user's credential_id propagates KeyError
5. `test_inject_missing_key_raises` — inject references a nonexistent key → KeyError
6. `test_original_graph_not_mutated` — original graph is not modified (deep-copy check)

### test_dispatcher_credentials.py (E2E with fakes)
1. `test_dispatch_resolves_and_runs` — graph with credential_ref → nodes run with resolved plaintext config
2. `test_dispatch_without_store_fails_when_refs_present` — credential_store=None + graph has refs → failed
3. `test_dispatch_without_store_works_without_refs` — credential_store=None + no refs → success (regression)
4. `test_dispatch_resolve_failure_marks_failed` — `bulk_retrieve` KeyError → execution failed (generic message)

## Checklist

- [ ] `src/runtime/credentials.py` — `resolve_credential_refs` function
- [ ] `src/container.py` — add `credential_store` to WorkerContainer
- [ ] `src/dispatcher/serverless.py` — `_execute()` performs resolution
- [ ] `tests/fakes.py` — add `InMemoryCredentialStore` if needed (check whether the Database fake is reusable)
- [ ] 10 tests pass, overall stays 33→43
- [ ] Existing tests still compatible (`_execute()` `credential_store` kwarg defaults to None)
- [ ] Commit → push → PR

## Out of scope

- Agent-path credential support — cross-branch follow-up (API_Server composes the WS payload + Agent `command_handler` decrypts)
- Credential rotation (Phase 2)
- Audit logging (decision pending)
