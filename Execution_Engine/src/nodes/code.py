"""CodeNode — execute user-defined Python in a RestrictedPython sandbox.

Runs in a separate thread via asyncio.to_thread to avoid blocking the
event loop. asyncio.wait_for enforces the timeout at the coroutine level.
"""
from __future__ import annotations

import asyncio

from src.nodes.base import BaseNode
from src.nodes.registry import registry
from src.runtime.sandbox import run_restricted


class CodeNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "code"

    async def execute(self, input_data: dict, config: dict) -> dict:
        code = config["source"]
        timeout = config.get("timeout_seconds", 30)
        return await asyncio.wait_for(
            asyncio.to_thread(run_restricted, code, input_data, timeout_seconds=timeout),
            timeout=timeout,
        )


registry.register(CodeNode)
