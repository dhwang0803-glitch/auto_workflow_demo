"""MergeNode — an explicit convergence point.

executor already dict-merges predecessor output into input_data
(executor.py lines 60–64), so this node is effectively a no-op passthrough. Its purpose
is to make the graph's "branch then merge" intent explicit.
"""
from __future__ import annotations

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class MergeNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "merge"

    async def execute(self, input_data: dict, config: dict) -> dict:
        return dict(input_data)


registry.register(MergeNode)
