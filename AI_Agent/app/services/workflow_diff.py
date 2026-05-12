"""PLAN_14 PR-C — semantic workflow diff (pure function).

Consumes two workflow payloads (the JSONB shape stored in
`workflow_revisions.payload` per PLAN_14 §4.3) and returns a structured
diff. No LLM, no I/O, no model imports — the diff stays decoupled from
API_Server's Pydantic schemas so it can run inside the Modal agent
without dragging the API layer along.

Payload shape mirrors API_Server's `WorkflowGraph`:

    {
        "nodes": [{"id": str, "type": str, "config": dict}, ...],
        "edges": [{"source": str, "target": str}, ...],
    }

Output is deterministic — sets are sorted by node id / edge tuple before
materializing, so repeated calls on the same input yield byte-identical
results. PLAN_14 §4.3's `suggestion_hash` rides on top of this diff and
needs that stability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class NodeChange:
    """A node whose id is in both payloads but whose type or config differs.

    `changed_keys` is the union of:
    - the literal "type" if the node type field changed
    - "config.<key>" for each top-level config key whose value differs
      (deep equality on the value).
    """

    id: str
    before: dict[str, Any]
    after: dict[str, Any]
    changed_keys: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowDiff:
    nodes_added: tuple[dict[str, Any], ...]
    nodes_removed: tuple[dict[str, Any], ...]
    nodes_modified: tuple[NodeChange, ...]
    edges_added: tuple[tuple[str, str], ...]
    edges_removed: tuple[tuple[str, str], ...]
    ordering_changed: bool

    @property
    def is_empty(self) -> bool:
        return not (
            self.nodes_added
            or self.nodes_removed
            or self.nodes_modified
            or self.edges_added
            or self.edges_removed
            or self.ordering_changed
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes_added": [dict(n) for n in self.nodes_added],
            "nodes_removed": [dict(n) for n in self.nodes_removed],
            "nodes_modified": [
                {
                    "id": nc.id,
                    "before": dict(nc.before),
                    "after": dict(nc.after),
                    "changed_keys": list(nc.changed_keys),
                }
                for nc in self.nodes_modified
            ],
            "edges_added": [list(e) for e in self.edges_added],
            "edges_removed": [list(e) for e in self.edges_removed],
            "ordering_changed": self.ordering_changed,
        }


def _index_by_id(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build id -> node dict. Later occurrences win on duplicate id —
    Frontend's editor guarantees uniqueness; the lenient fallback keeps
    the diff total even on a degenerate payload."""
    return {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}


def _changed_keys(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, ...]:
    keys: set[str] = set()
    if before.get("type") != after.get("type"):
        keys.add("type")
    before_config = before.get("config") or {}
    after_config = after.get("config") or {}
    for key in set(before_config) | set(after_config):
        if before_config.get(key) != after_config.get(key):
            keys.add(f"config.{key}")
    return tuple(sorted(keys))


def _edge_set(edges: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (e["source"], e["target"])
        for e in edges
        if isinstance(e, dict) and e.get("source") and e.get("target")
    }


def diff_workflow(v1: dict[str, Any], v2: dict[str, Any]) -> WorkflowDiff:
    """Compute the semantic diff between two workflow payloads.

    PLAN_14 §4.4. Node identity is the `id` field — Frontend preserves
    ids across edits and only mints new ones for inserted nodes, so set
    arithmetic on the id space is the right primitive.
    """
    v1_nodes = list(v1.get("nodes") or [])
    v2_nodes = list(v2.get("nodes") or [])
    v1_by_id = _index_by_id(v1_nodes)
    v2_by_id = _index_by_id(v2_nodes)

    added_ids = sorted(set(v2_by_id) - set(v1_by_id))
    removed_ids = sorted(set(v1_by_id) - set(v2_by_id))
    common_ids = set(v1_by_id) & set(v2_by_id)

    nodes_added = tuple(v2_by_id[i] for i in added_ids)
    nodes_removed = tuple(v1_by_id[i] for i in removed_ids)

    modifications: list[NodeChange] = []
    for nid in sorted(common_ids):
        before = v1_by_id[nid]
        after = v2_by_id[nid]
        keys = _changed_keys(before, after)
        if keys:
            modifications.append(
                NodeChange(id=nid, before=before, after=after, changed_keys=keys)
            )
    nodes_modified = tuple(modifications)

    e1 = _edge_set(v1.get("edges") or [])
    e2 = _edge_set(v2.get("edges") or [])
    edges_added = tuple(sorted(e2 - e1))
    edges_removed = tuple(sorted(e1 - e2))

    v1_order = [n["id"] for n in v1_nodes if isinstance(n, dict) and n.get("id") in common_ids]
    v2_order = [n["id"] for n in v2_nodes if isinstance(n, dict) and n.get("id") in common_ids]
    ordering_changed = v1_order != v2_order

    return WorkflowDiff(
        nodes_added=nodes_added,
        nodes_removed=nodes_removed,
        nodes_modified=nodes_modified,
        edges_added=edges_added,
        edges_removed=edges_removed,
        ordering_changed=ordering_changed,
    )


__all__ = ["NodeChange", "WorkflowDiff", "diff_workflow"]
