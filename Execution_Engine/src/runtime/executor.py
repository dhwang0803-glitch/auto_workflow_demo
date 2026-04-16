"""DAG executor — runs workflow nodes in topological (level) order.

Kahn's algorithm groups nodes by dependency level so independent nodes
within the same level run in parallel via asyncio.gather. Each node is
a fresh instance from registry.get(type)() — see NodeRegistry docstring.

Node output flows forward: a node's output becomes input_data for its
direct successors. When a node has multiple predecessors, their outputs
are merged (later key wins).
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

from auto_workflow_database.repositories.base import Execution, ExecutionRepository

from src.nodes.registry import NodeRegistry


async def run_workflow(
    graph: dict,
    execution: Execution,
    repo: ExecutionRepository,
    registry: NodeRegistry,
) -> None:
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = graph.get("edges", [])

    # -- Kahn topological sort producing level groups for parallel execution --
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    indeg: dict[str, int] = {nid: 0 for nid in nodes}
    for e in edges:
        adj[e["source"]].append(e["target"])
        indeg[e["target"]] += 1

    levels: list[list[str]] = []
    queue: deque[str] = deque(nid for nid, d in indeg.items() if d == 0)
    while queue:
        level = list(queue)
        levels.append(level)
        queue.clear()
        for nid in level:
            for nxt in adj[nid]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)

    await repo.update_status(execution.id, "running")
    start_ms = time.monotonic()
    # node_id → output dict, used to feed input_data to successors
    outputs: dict[str, dict] = {}

    try:
        for level in levels:

            async def _run_node(nid: str) -> None:
                spec = nodes[nid]
                # Merge outputs from all predecessors as input_data
                predecessors = [e["source"] for e in edges if e["target"] == nid]
                input_data: dict = {}
                for pred in predecessors:
                    input_data.update(outputs.get(pred, {}))
                node = registry.get(spec["type"])()
                result = await node.execute(input_data, spec.get("config", {}))
                outputs[nid] = result
                await repo.append_node_result(execution.id, nid, result)

            await asyncio.gather(*[_run_node(nid) for nid in level])

        elapsed = int((time.monotonic() - start_ms) * 1000)
        await repo.update_status(execution.id, "success")
        await repo.finalize(execution.id, duration_ms=elapsed)
    except Exception as exc:
        await repo.update_status(
            execution.id, "failed", error={"message": str(exc)}
        )
