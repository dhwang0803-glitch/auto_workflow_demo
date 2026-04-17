"""PLAN_12 — MergeNode tests."""
from __future__ import annotations

import pytest

from src.nodes.merge import MergeNode


@pytest.fixture
def node():
    return MergeNode()


async def test_merge_returns_input_as_output(node):
    result = await node.execute({"a": 1, "b": 2}, {})
    assert result == {"a": 1, "b": 2}


async def test_merge_with_empty_input(node):
    result = await node.execute({}, {})
    assert result == {}
