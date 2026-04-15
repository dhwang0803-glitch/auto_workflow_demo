"""Unit tests for the pure DAG validator — PLAN_02."""
from __future__ import annotations

import pytest

from app.models.workflow import EdgeSpec, NodeSpec, WorkflowGraph
from app.services.dag_validator import DAGError, validate_dag


def _g(nodes, edges=()):
    return WorkflowGraph(
        nodes=[NodeSpec(id=n, type="noop") for n in nodes],
        edges=[EdgeSpec(source=s, target=t) for (s, t) in edges],
    )


def test_simple_chain_ok():
    validate_dag(_g(["a", "b", "c"], [("a", "b"), ("b", "c")]))


def test_diamond_ok():
    validate_dag(_g(["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]))


def test_single_node_ok():
    validate_dag(_g(["only"]))


def test_cycle_rejected():
    with pytest.raises(DAGError, match="cycle"):
        validate_dag(_g(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")]))


def test_self_loop_rejected():
    with pytest.raises(DAGError, match="cycle"):
        validate_dag(_g(["a"], [("a", "a")]))


def test_duplicate_node_id_rejected():
    with pytest.raises(DAGError, match="duplicate"):
        validate_dag(
            WorkflowGraph(
                nodes=[NodeSpec(id="x", type="t1"), NodeSpec(id="x", type="t2")],
                edges=[],
            )
        )


def test_unknown_edge_source_rejected():
    with pytest.raises(DAGError, match="source"):
        validate_dag(_g(["a"], [("ghost", "a")]))


def test_unknown_edge_target_rejected():
    with pytest.raises(DAGError, match="target"):
        validate_dag(_g(["a"], [("a", "ghost")]))
