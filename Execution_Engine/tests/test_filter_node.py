"""PLAN_12 — FilterNode tests."""
from __future__ import annotations

import pytest

from src.nodes.filter import FilterNode


@pytest.fixture
def node():
    return FilterNode()


_ITEMS = [
    {"name": "alice", "age": 30, "tags": ["vip", "new"]},
    {"name": "bob", "age": 25, "tags": ["new"]},
    {"name": "carol", "age": 40, "tags": ["vip"]},
]


async def test_filter_eq_operator(node):
    result = await node.execute(
        {"items": _ITEMS},
        {"condition": {"field": "name", "operator": "eq", "value": "bob"}},
    )
    assert result["count"] == 1
    assert result["items"][0]["name"] == "bob"


async def test_filter_gt_operator(node):
    result = await node.execute(
        {"items": _ITEMS},
        {"condition": {"field": "age", "operator": "gt", "value": 28}},
    )
    assert result["count"] == 2
    assert {it["name"] for it in result["items"]} == {"alice", "carol"}


async def test_filter_contains_operator(node):
    result = await node.execute(
        {"items": _ITEMS},
        {"condition": {"field": "tags", "operator": "contains", "value": "vip"}},
    )
    assert result["count"] == 2
    assert {it["name"] for it in result["items"]} == {"alice", "carol"}


async def test_filter_truthy_operator(node):
    items = [{"x": 0}, {"x": 1}, {"x": None}, {"x": "ok"}]
    result = await node.execute(
        {"items": items},
        {"condition": {"field": "x", "operator": "truthy"}},
    )
    assert result["count"] == 2
    assert [it["x"] for it in result["items"]] == [1, "ok"]


async def test_filter_empty_list_returns_empty(node):
    result = await node.execute(
        {"items": []},
        {"condition": {"field": "name", "operator": "eq", "value": "x"}},
    )
    assert result == {"items": [], "count": 0}
