"""PLAN_06 — DelayNode tests."""
from __future__ import annotations

import time

import pytest

from src.nodes.delay import DelayNode


@pytest.fixture
def node():
    return DelayNode()


async def test_delay_waits(node):
    start = time.monotonic()
    await node.execute({}, {"seconds": 0.05})
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05


async def test_delay_returns_waited_seconds(node):
    result = await node.execute({}, {"seconds": 0.01})
    assert result == {"waited_seconds": 0.01}
