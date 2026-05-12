"""Unit tests for `app.services.workflow_diff.diff_workflow`.

The diff feeds PLAN_14's `personalization_agent` propose+judge graph,
and the propose prompt is hash-keyed by the diff signature
(`suggestion_hash`, PLAN_14 §4.3) — any non-determinism in the diff
would split the de-duplication index and let the same edit pattern
re-surface as a "new" candidate.

So these tests cover the diff in two passes:
- correctness on each PLAN_14 §4.4 case (added/removed/modified/
  edges/ordering), and
- determinism — repeated calls and shuffled-input calls return
  byte-identical output.
"""
from __future__ import annotations

import json

import pytest

from app.services.workflow_diff import NodeChange, WorkflowDiff, diff_workflow


def _wf(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    return {"nodes": nodes, "edges": edges or []}


def _node(nid: str, ntype: str = "http_request", **config) -> dict:
    return {"id": nid, "type": ntype, "config": dict(config)}


def _edge(src: str, tgt: str) -> dict:
    return {"source": src, "target": tgt}


# --- identity & empty cases -------------------------------------------------


def test_identical_workflow_yields_empty_diff() -> None:
    wf = _wf([_node("a"), _node("b")], [_edge("a", "b")])
    diff = diff_workflow(wf, wf)
    assert diff.is_empty
    assert diff == WorkflowDiff((), (), (), (), (), False)


def test_both_empty_payloads_yield_empty_diff() -> None:
    diff = diff_workflow({}, {})
    assert diff.is_empty


def test_missing_edges_key_treated_as_empty_list() -> None:
    v1 = {"nodes": [_node("a")]}
    v2 = {"nodes": [_node("a")]}
    diff = diff_workflow(v1, v2)
    assert diff.is_empty


# --- node added / removed ---------------------------------------------------


def test_node_added_detected() -> None:
    v1 = _wf([_node("a")])
    v2 = _wf([_node("a"), _node("b", "slack_notify")])
    diff = diff_workflow(v1, v2)
    assert diff.nodes_added == (_node("b", "slack_notify"),)
    assert diff.nodes_removed == ()
    assert diff.nodes_modified == ()


def test_node_removed_detected() -> None:
    v1 = _wf([_node("a"), _node("b")])
    v2 = _wf([_node("a")])
    diff = diff_workflow(v1, v2)
    assert diff.nodes_removed == (_node("b"),)
    assert diff.nodes_added == ()


# --- node modified ----------------------------------------------------------


def test_node_config_value_change_yields_changed_keys() -> None:
    v1 = _wf([_node("a", retry_seconds=30)])
    v2 = _wf([_node("a", retry_seconds=300)])
    diff = diff_workflow(v1, v2)
    assert len(diff.nodes_modified) == 1
    nc = diff.nodes_modified[0]
    assert isinstance(nc, NodeChange)
    assert nc.id == "a"
    assert nc.changed_keys == ("config.retry_seconds",)
    assert nc.before["config"]["retry_seconds"] == 30
    assert nc.after["config"]["retry_seconds"] == 300


def test_node_type_change_yields_type_key() -> None:
    v1 = _wf([_node("a", "http_request")])
    v2 = _wf([_node("a", "webhook")])
    diff = diff_workflow(v1, v2)
    assert diff.nodes_modified[0].changed_keys == ("type",)


def test_node_added_config_key_appears_in_changed_keys() -> None:
    v1 = _wf([_node("a")])
    v2 = _wf([_node("a", retry_seconds=300)])
    diff = diff_workflow(v1, v2)
    assert diff.nodes_modified[0].changed_keys == ("config.retry_seconds",)


def test_node_with_same_type_and_config_not_in_modified() -> None:
    v1 = _wf([_node("a", retry_seconds=30, url="x")])
    v2 = _wf([_node("a", retry_seconds=30, url="x")])
    diff = diff_workflow(v1, v2)
    assert diff.nodes_modified == ()


def test_multiple_changed_keys_sorted() -> None:
    v1 = _wf([_node("a", "http_request", retry_seconds=30, timeout=10)])
    v2 = _wf([_node("a", "webhook", retry_seconds=300, timeout=60)])
    diff = diff_workflow(v1, v2)
    keys = diff.nodes_modified[0].changed_keys
    assert keys == ("config.retry_seconds", "config.timeout", "type")
    assert list(keys) == sorted(keys)


def test_deep_config_value_compared_by_equality() -> None:
    v1 = _wf([_node("a", headers={"X-Auth": "v1", "X-Trace": "t"})])
    v2 = _wf([_node("a", headers={"X-Auth": "v2", "X-Trace": "t"})])
    diff = diff_workflow(v1, v2)
    assert diff.nodes_modified[0].changed_keys == ("config.headers",)


# --- edges ------------------------------------------------------------------


def test_edge_added_and_removed_detected() -> None:
    v1 = _wf([_node("a"), _node("b"), _node("c")], [_edge("a", "b")])
    v2 = _wf([_node("a"), _node("b"), _node("c")], [_edge("a", "c"), _edge("c", "b")])
    diff = diff_workflow(v1, v2)
    assert diff.edges_added == (("a", "c"), ("c", "b"))
    assert diff.edges_removed == (("a", "b"),)


def test_edges_sorted_for_determinism() -> None:
    v1 = _wf([_node("a"), _node("b"), _node("c"), _node("d")])
    v2 = _wf(
        [_node("a"), _node("b"), _node("c"), _node("d")],
        [_edge("c", "d"), _edge("a", "b"), _edge("b", "c")],
    )
    diff = diff_workflow(v1, v2)
    assert diff.edges_added == (("a", "b"), ("b", "c"), ("c", "d"))


# --- ordering ---------------------------------------------------------------


def test_ordering_change_only_flips_flag() -> None:
    v1 = _wf([_node("a"), _node("b"), _node("c")])
    v2 = _wf([_node("a"), _node("c"), _node("b")])
    diff = diff_workflow(v1, v2)
    assert diff.ordering_changed is True
    assert diff.nodes_added == ()
    assert diff.nodes_removed == ()
    assert diff.nodes_modified == ()


def test_ordering_unchanged_when_added_node_appears_at_end() -> None:
    v1 = _wf([_node("a"), _node("b")])
    v2 = _wf([_node("a"), _node("b"), _node("c", "slack_notify")])
    diff = diff_workflow(v1, v2)
    assert diff.ordering_changed is False
    assert diff.nodes_added == (_node("c", "slack_notify"),)


def test_ordering_compares_only_common_ids() -> None:
    # Inserted node "x" between a and b should not flip ordering for the
    # common (a, b) pair — the propose stage cares about whether the user
    # re-sequenced existing nodes, not where a new one landed.
    v1 = _wf([_node("a"), _node("b")])
    v2 = _wf([_node("a"), _node("x", "slack_notify"), _node("b")])
    diff = diff_workflow(v1, v2)
    assert diff.ordering_changed is False
    assert diff.nodes_added == (_node("x", "slack_notify"),)


# --- determinism (suggestion_hash stability) --------------------------------


def test_repeated_call_returns_equal_diff() -> None:
    v1 = _wf([_node("a"), _node("b")], [_edge("a", "b")])
    v2 = _wf(
        [_node("a"), _node("b"), _node("c", "slack_notify")],
        [_edge("a", "b"), _edge("b", "c")],
    )
    first = diff_workflow(v1, v2)
    second = diff_workflow(v1, v2)
    assert first == second
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True
    )


def test_input_node_order_does_not_affect_diff() -> None:
    base_nodes = [_node("a"), _node("b"), _node("c", "slack_notify")]
    v1 = _wf([_node("a"), _node("b")])
    v2a = _wf(base_nodes)
    v2b = _wf(list(reversed(base_nodes)))
    diff_a = diff_workflow(v1, v2a)
    diff_b = diff_workflow(v1, v2b)
    # Set-based outputs (added / removed / edges) must match regardless of
    # input order — only the ordering_changed flag is allowed to differ.
    assert diff_a.nodes_added == diff_b.nodes_added
    assert diff_a.nodes_removed == diff_b.nodes_removed
    assert diff_a.nodes_modified == diff_b.nodes_modified
    assert diff_a.edges_added == diff_b.edges_added
    assert diff_a.edges_removed == diff_b.edges_removed


def test_input_edge_order_does_not_affect_diff() -> None:
    nodes = [_node("a"), _node("b"), _node("c")]
    v1 = _wf(nodes)
    v2a = _wf(nodes, [_edge("a", "b"), _edge("b", "c")])
    v2b = _wf(nodes, [_edge("b", "c"), _edge("a", "b")])
    assert diff_workflow(v1, v2a) == diff_workflow(v1, v2b)


# --- serialization ----------------------------------------------------------


def test_to_dict_is_json_serializable() -> None:
    v1 = _wf([_node("a", retry_seconds=30)])
    v2 = _wf(
        [_node("a", retry_seconds=300), _node("b", "slack_notify")],
        [_edge("a", "b")],
    )
    diff = diff_workflow(v1, v2)
    payload = diff.to_dict()
    # Round-trip through json — no tuples, no dataclass instances leak.
    encoded = json.dumps(payload, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded["nodes_added"][0]["id"] == "b"
    assert decoded["nodes_modified"][0]["id"] == "a"
    assert decoded["nodes_modified"][0]["changed_keys"] == ["config.retry_seconds"]
    assert decoded["edges_added"] == [["a", "b"]]
    assert decoded["ordering_changed"] is False


# --- degenerate input -------------------------------------------------------


def test_node_without_id_silently_skipped() -> None:
    # Frontend invariant is that every node has an id; the lenient skip
    # keeps the diff total when a malformed payload arrives instead of
    # crashing the background candidate-generation job.
    v1 = _wf([_node("a"), {"type": "broken"}])
    v2 = _wf([_node("a")])
    diff = diff_workflow(v1, v2)
    assert diff.is_empty


def test_edge_with_missing_endpoint_silently_skipped() -> None:
    v1 = _wf([_node("a")])
    v2 = _wf([_node("a")], [{"source": "a"}, {"target": "x"}])
    diff = diff_workflow(v1, v2)
    assert diff.edges_added == ()


@pytest.mark.parametrize("missing", ["nodes", "edges"])
def test_none_collections_treated_as_empty(missing: str) -> None:
    v1 = {"nodes": [_node("a")], "edges": [_edge("a", "a")]}
    v2 = dict(v1)
    v2[missing] = None
    # Should not raise; diff is well-defined against the None.
    diff_workflow(v1, v2)
    diff_workflow(v2, v1)
