# PLAN_12 — Four Flow Primitives (loop/transform/merge/filter)

> Predecessor: ADR-017 (node-catalog minimum spec) — closes the
> Flow/Transform-category gap toward the 21-node target. This PLAN
> covers four of them (PR A).

## Purpose

Today's 3 Flow primitives (`condition`, `code`, `delay`) only support
*branching*. Real workflows combine *branching + iteration + merging +
dropping + transformation* — see ADR-017 §2. This PR fills all four
axes at once.

## Design choice — avoid executor changes

The current executor (`src/runtime/executor.py`):
- Kahn topological sort + per-level parallel execution via `asyncio.gather`
- Dict-merges predecessor outputs into `input_data` (lines 60–64)
- No skip / subgraph / loop semantics

This PLAN implements the four primitives **without touching the executor**. Reasons:

1. **No Frontend** — there's no editor for drawing a loop body as a visual subgraph. Without visual representation, introducing subgraph semantics now isn't worth the investment.
2. **Risk isolation** — executor changes risk regressions across all existing nodes/dispatcher/Agent paths. Keep it inside this PLAN's scope.
3. **Sufficient** — all 4 primitives can be expressed as data ops or as the "node calls node" pattern.

If we genuinely need visual subgraphs later, a separate PLAN can
refactor the executor and reimplement `loop_items` (this PLAN may be
deprecated then).

## Scope

4 nodes:

| node_type | Type | Responsibility |
|---|---|---|
| `merge` | Flow | Multi-predecessor convergence point. Returns input_data unchanged. For graph readability. |
| `transform` | Data | Declarative field mapping. `{input.foo}` template substitution. |
| `filter` | Data | Filter an `items` array. Evaluates a condition expression. |
| `loop_items` | Flow | Invoke a worker node N times. Each iteration receives an item. |

## File changes

### New
| File | Role |
|------|------|
| `src/nodes/merge.py` | MergeNode — no-op passthrough |
| `src/nodes/transform.py` | TransformNode — template substitution |
| `src/nodes/filter.py` | FilterNode — array filter |
| `src/nodes/loop_items.py` | LoopItemsNode — repeated worker execution |
| `tests/test_merge_node.py` | Unit tests |
| `tests/test_transform_node.py` | Unit tests |
| `tests/test_filter_node.py` | Unit tests |
| `tests/test_loop_items_node.py` | Unit tests |

Modified: none (no executor / registry changes).

## Node specs

### 1. MergeNode

```
config: (none)
input_data: dict-merge of predecessor outputs (already handled by the executor)
output: returns input_data unchanged
```

An explicit convergence point on the graph. The executor already merges
predecessors (lines 60–64), so it's effectively a no-op — but it
expresses "branch then merge" intent in the UI.

### 2. TransformNode

```
config:
  mapping: dict[str, str|int|bool|None]
    # If the value is a str matching the "{...}" pattern, look up the key in input_data and substitute
    # Nested keys support dot paths, e.g. "{input.foo.bar}"
  defaults?: dict  # fallback when substitution fails

output: the mapping with templates substituted
```

Example:
```json
{
  "mapping": {
    "name": "{input.user.name}",
    "age": "{input.user.age}",
    "source": "airtable"
  }
}
```

### 3. FilterNode

```
config:
  items_key: str (default "items")  # key to pull the array from in input_data
  condition:
    field: str            # item field name (dot path)
    operator: str         # eq, ne, gt, lt, gte, lte, contains, in, truthy
    value?: any           # unnecessary when operator is truthy

input_data: { items_key: list[dict] }
output: { items: [filtered], count: int }
```

### 4. LoopItemsNode

```
config:
  items_key?: str (default "items")  # pull array from input_data
  items?: list                       # or static items
  worker_type: str                   # node type registered in the registry
  worker_config: dict                # template. "{item}" or "{item.field}" substitution
  max_concurrency?: int (default 5)  # asyncio.gather limit

Behavior:
  - For each item, substitute the worker_config template
  - Instantiate the worker via registry.get(worker_type)()
  - await worker.execute({"item": item}, interpolated_config)
  - Limited parallelism via asyncio.Semaphore(max_concurrency)

output: { results: list[dict], count: int, failures: int }
```

**Failure policy**: a single worker failure does not abort the rest.
Failed results are included as `{_error: str}` in results. failures
holds the count. (All-or-nothing comes later as a separate `transaction`
pattern node.)

## Shared implementation utility — template substitution

Both `transform` and `loop_items` need `{input.foo.bar}` /
`{item.field}` substitution. To avoid duplication while preserving the
three-line rule, **each node file holds a simple `_interpolate(value,
ctx)` function** (only two callers, so don't extract a shared util).

Logic: if the string exactly matches `{path}`, resolve as a dot path;
otherwise do a `str.format_map`-style substitution. Missing keys fall
back to defaults or remain as-is.

## Security invariants

- The only constraint on what `loop_items` may invoke as a worker is the
  registry — to prevent recursive loop_items we **hard-cap depth=1** (if
  the worker is another loop_items, raise KeyError)
- worker_config template substitution does not use repr/eval — string
  replacement only

## Test strategy (3–5 per node, 17 total)

### test_merge_node.py (2)
- `test_merge_returns_input_as_output`
- `test_merge_with_empty_input`

### test_transform_node.py (4)
- `test_simple_mapping` — plaintext substitution
- `test_nested_path_substitution` — `{input.user.name}` dot path
- `test_static_values_preserved` — non-template values pass through
- `test_missing_key_uses_default`

### test_filter_node.py (5)
- `test_filter_eq_operator`
- `test_filter_gt_operator`
- `test_filter_contains_operator`
- `test_filter_truthy_operator` (value omitted)
- `test_filter_empty_list_returns_empty`

### test_loop_items_node.py (6)
- `test_loop_calls_worker_per_item` — mock worker, verify N calls
- `test_loop_interpolates_item_in_config` — `{item.name}` substitution
- `test_loop_aggregates_results`
- `test_loop_respects_concurrency_limit` — Semaphore behavior
- `test_loop_failure_does_not_abort_siblings` — one failure, others succeed
- `test_loop_recursive_loop_items_rejected` — depth=1 cap

## Checklist

- [ ] `src/nodes/merge.py` + 2 tests
- [ ] `src/nodes/transform.py` + 4 tests
- [ ] `src/nodes/filter.py` + 5 tests
- [ ] `src/nodes/loop_items.py` + 6 tests
- [ ] Overall tests 79 → 96 pass
- [ ] Commit → push → PR A

## Out of scope

- Visual subgraph loop (drawing loop body as a graph) — needs Frontend + executor refactor
- Batch size / pagination (n8n SplitInBatches equivalent) — split into a follow-up `batch_split` node
- All-or-nothing transactional loop — follow-up `transaction` node or a config flag
- Complex condition expressions (AND/OR combinations) — current filter is single-operator only. Combine via chaining or the code node.
- JSONata / jq-style complex template engines — this PLAN only does dot-path substitution
