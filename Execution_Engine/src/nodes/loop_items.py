"""LoopItemsNode — worker 노드를 item 배열로 N 회 실행.

본 노드는 visual subgraph 없이 "노드가 노드를 호출" 패턴으로 loop 를
구현. Frontend visual editor 가 들어오면 별도 PLAN 에서 재설계 예정.

config 템플릿 치환:
- worker_config 의 값이 "{item}" 이면 item 자체 치환
- "{item.field}" 이면 점 경로 조회

실패 격리: 하나가 실패해도 나머지 실행. 실패 결과는 {_error: str} 로 포함.
재귀 방지: worker_type == "loop_items" 는 거부 (depth=1 cap).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from src.nodes.base import BaseNode
from src.nodes.registry import registry


_TEMPLATE_RE = re.compile(r"^\{([a-zA-Z_][\w.]*)\}$")


def _resolve(path: str, ctx: dict) -> Any:
    cur: Any = ctx
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def _interpolate(value: Any, ctx: dict) -> Any:
    if isinstance(value, str):
        m = _TEMPLATE_RE.match(value)
        if m:
            return _resolve(m.group(1), ctx)
        return value
    if isinstance(value, dict):
        return {k: _interpolate(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, ctx) for v in value]
    return value


class LoopItemsNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "loop_items"

    async def execute(self, input_data: dict, config: dict) -> dict:
        worker_type = config["worker_type"]
        if worker_type == "loop_items":
            # Prevent recursion; nested loops require a dedicated design.
            raise ValueError("loop_items cannot invoke loop_items (depth cap = 1)")

        items_key = config.get("items_key", "items")
        items = config.get("items") or input_data.get(items_key) or []
        template = config.get("worker_config", {})
        max_concurrency = config.get("max_concurrency", 5)

        worker_cls = registry.get(worker_type)
        sem = asyncio.Semaphore(max_concurrency)

        async def _run(item: Any) -> dict:
            async with sem:
                ctx = {"item": item}
                cfg = _interpolate(template, ctx)
                worker = worker_cls()
                try:
                    return await worker.execute({"item": item}, cfg)
                except Exception as exc:
                    return {"_error": str(exc)}

        results = await asyncio.gather(*[_run(it) for it in items])
        failures = sum(1 for r in results if isinstance(r, dict) and "_error" in r)
        return {"results": results, "count": len(results), "failures": failures}


registry.register(LoopItemsNode)
