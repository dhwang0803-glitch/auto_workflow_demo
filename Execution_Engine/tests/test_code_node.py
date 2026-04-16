"""PLAN_05 — CodeNode + RestrictedPython sandbox tests."""
from __future__ import annotations

import pytest

from src.nodes.code import CodeNode
from src.runtime.sandbox import run_restricted


@pytest.fixture
def node():
    return CodeNode()


async def test_simple_computation(node):
    result = await node.execute(
        {"a": 3, "b": 7},
        {"source": 'result["sum"] = inputs["a"] + inputs["b"]'},
    )
    assert result["sum"] == 10


async def test_loop_and_list(node):
    result = await node.execute(
        {"numbers": [1, 2, 3, 4, 5]},
        {"source": (
            'total = 0\n'
            'for n in inputs["numbers"]:\n'
            '    total += n\n'
            'result["total"] = total'
        )},
    )
    assert result["total"] == 15


def test_import_blocked():
    with pytest.raises(ImportError):
        run_restricted("import os", {})


def test_open_blocked():
    with pytest.raises(NameError):
        run_restricted('open("/etc/passwd")', {})


async def test_timeout_exceeded(node):
    # Bounded loop that takes several seconds — triggers timeout but
    # the thread eventually finishes on its own (Python threads can't
    # be killed; true isolation requires subprocess/Docker, see CLAUDE.md)
    with pytest.raises(TimeoutError):
        await node.execute(
            {},
            {
                "source": "x = 0\nfor i in range(10**8): x += 1",
                "timeout_seconds": 1,
            },
        )
