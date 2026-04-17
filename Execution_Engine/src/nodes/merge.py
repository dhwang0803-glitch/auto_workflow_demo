"""MergeNode — 명시적 수렴점.

executor 가 이미 predecessor output 을 input_data 에 dict merge 하므로
(executor.py line 60~64) 본 노드는 사실상 no-op passthrough. 그래프 상
"분기 후 합치기" 의도를 명시하는 용도.
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
