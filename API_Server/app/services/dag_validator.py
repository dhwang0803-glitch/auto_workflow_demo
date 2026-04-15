"""Pure DAG validation — PLAN_02.

Kahn's algorithm. Raises `DAGError` on cycle, unknown edge endpoint,
duplicate node id, or empty node list. No I/O, no framework coupling —
unit-testable as a plain function.
"""
from __future__ import annotations

from collections import deque

from app.models.workflow import WorkflowGraph


class DAGError(Exception):
    pass


def validate_dag(graph: WorkflowGraph) -> None:
    nodes = graph.nodes
    edges = graph.edges

    if not nodes:
        raise DAGError("workflow graph must contain at least one node")

    node_ids = [n.id for n in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise DAGError("duplicate node id in graph")
    node_id_set = set(node_ids)

    for e in edges:
        if e.source not in node_id_set:
            raise DAGError(f"edge source {e.source!r} does not match any node")
        if e.target not in node_id_set:
            raise DAGError(f"edge target {e.target!r} does not match any node")

    adj: dict[str, list[str]] = {n: [] for n in node_id_set}
    indeg: dict[str, int] = {n: 0 for n in node_id_set}
    for e in edges:
        adj[e.source].append(e.target)
        indeg[e.target] += 1

    queue: deque[str] = deque(n for n in node_ids if indeg[n] == 0)
    visited = 0
    while queue:
        cur = queue.popleft()
        visited += 1
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)

    if visited != len(node_ids):
        remaining = sorted(n for n, d in indeg.items() if d > 0)
        raise DAGError(f"cycle detected in graph (nodes on cycle: {remaining})")
