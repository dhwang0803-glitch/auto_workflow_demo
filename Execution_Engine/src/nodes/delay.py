"""DelayNode — 후속 노드 실행 전 일정 시간 대기."""
from __future__ import annotations

import asyncio

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class DelayNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "delay"

    async def execute(self, input_data: dict, config: dict) -> dict:
        seconds = config["seconds"]
        await asyncio.sleep(seconds)
        return {"waited_seconds": seconds}


registry.register(DelayNode)
