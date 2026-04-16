"""PLAN_05 — ConditionNode tests."""
from __future__ import annotations

import pytest

from src.nodes.condition import ConditionNode


@pytest.fixture
def node():
    return ConditionNode()


async def test_eq_true(node):
    result = await node.execute(
        {"status": 200},
        {"left_field": "status", "operator": "eq", "right_value": 200},
    )
    assert result["result"] is True


async def test_eq_false(node):
    result = await node.execute(
        {"status": 404},
        {"left_field": "status", "operator": "eq", "right_value": 200},
    )
    assert result["result"] is False


async def test_gt_operator(node):
    result = await node.execute(
        {"score": 85},
        {"left_field": "score", "operator": "gt", "right_value": 80},
    )
    assert result["result"] is True


async def test_contains_operator(node):
    result = await node.execute(
        {"message": "hello world"},
        {"left_field": "message", "operator": "contains", "right_value": "world"},
    )
    assert result["result"] is True


async def test_missing_field_returns_false(node):
    result = await node.execute(
        {},
        {"left_field": "nonexistent", "operator": "eq", "right_value": 42},
    )
    assert result["result"] is False
