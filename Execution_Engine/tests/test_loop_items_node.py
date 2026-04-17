"""PLAN_12 — LoopItemsNode tests."""
from __future__ import annotations

import asyncio

import pytest

from src.nodes.base import BaseNode
from src.nodes.loop_items import LoopItemsNode
from src.nodes.registry import registry


class _RecordingWorker(BaseNode):
    """Test worker — captures calls on a class-level list."""

    calls: list[tuple[dict, dict]] = []

    @property
    def node_type(self) -> str:
        return "_recording_worker"

    async def execute(self, input_data: dict, config: dict) -> dict:
        _RecordingWorker.calls.append((dict(input_data), dict(config)))
        return {"echoed": config.get("echo", None), "item": input_data.get("item")}


class _FlakyWorker(BaseNode):
    """Test worker — fails on the item whose id equals `fail_on_id`."""

    @property
    def node_type(self) -> str:
        return "_flaky_worker"

    async def execute(self, input_data: dict, config: dict) -> dict:
        item = input_data["item"]
        if item.get("id") == config.get("fail_on_id"):
            raise RuntimeError("boom")
        return {"ok": item}


class _ConcurrencyProbeWorker(BaseNode):
    """Test worker — tracks concurrent executions to verify semaphore."""

    in_flight: int = 0
    peak: int = 0

    @property
    def node_type(self) -> str:
        return "_concurrency_probe_worker"

    async def execute(self, input_data: dict, config: dict) -> dict:
        _ConcurrencyProbeWorker.in_flight += 1
        _ConcurrencyProbeWorker.peak = max(
            _ConcurrencyProbeWorker.peak, _ConcurrencyProbeWorker.in_flight
        )
        await asyncio.sleep(0.02)
        _ConcurrencyProbeWorker.in_flight -= 1
        return {}


registry.register(_RecordingWorker)
registry.register(_FlakyWorker)
registry.register(_ConcurrencyProbeWorker)


@pytest.fixture
def node():
    return LoopItemsNode()


@pytest.fixture(autouse=True)
def _reset_probes():
    _RecordingWorker.calls = []
    _ConcurrencyProbeWorker.in_flight = 0
    _ConcurrencyProbeWorker.peak = 0


async def test_loop_calls_worker_per_item(node):
    await node.execute(
        {"items": [{"id": 1}, {"id": 2}, {"id": 3}]},
        {"worker_type": "_recording_worker", "worker_config": {"echo": "x"}},
    )
    assert len(_RecordingWorker.calls) == 3


async def test_loop_interpolates_item_in_config(node):
    items = [{"name": "a"}, {"name": "b"}]
    await node.execute(
        {"items": items},
        {
            "worker_type": "_recording_worker",
            "worker_config": {"echo": "{item.name}"},
        },
    )
    echoed = sorted(cfg["echo"] for _, cfg in _RecordingWorker.calls)
    assert echoed == ["a", "b"]


async def test_loop_aggregates_results(node):
    result = await node.execute(
        {"items": [{"id": 1}, {"id": 2}]},
        {"worker_type": "_recording_worker", "worker_config": {}},
    )
    assert result["count"] == 2
    assert result["failures"] == 0
    assert len(result["results"]) == 2


async def test_loop_respects_concurrency_limit(node):
    await node.execute(
        {"items": [{}] * 10},
        {
            "worker_type": "_concurrency_probe_worker",
            "worker_config": {},
            "max_concurrency": 3,
        },
    )
    assert _ConcurrencyProbeWorker.peak <= 3


async def test_loop_failure_does_not_abort_siblings(node):
    result = await node.execute(
        {"items": [{"id": 1}, {"id": 2}, {"id": 3}]},
        {
            "worker_type": "_flaky_worker",
            "worker_config": {"fail_on_id": 2},
        },
    )
    assert result["count"] == 3
    assert result["failures"] == 1
    errored = [r for r in result["results"] if "_error" in r]
    assert len(errored) == 1
    assert "boom" in errored[0]["_error"]


async def test_loop_recursive_loop_items_rejected(node):
    with pytest.raises(ValueError, match="depth cap"):
        await node.execute(
            {"items": [{}]},
            {"worker_type": "loop_items", "worker_config": {}},
        )
