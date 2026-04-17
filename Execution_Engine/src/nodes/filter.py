"""FilterNode — 배열 필터링.

input_data[items_key] 에서 배열을 꺼내 config.condition 을 각 item 에
적용. operator 는 eq/ne/gt/lt/gte/lte/contains/in/truthy 지원.

복합 조건 (AND/OR) 은 filter 를 체이닝하거나 code 노드로 대체.
"""
from __future__ import annotations

from typing import Any

from src.nodes.base import BaseNode
from src.nodes.registry import registry


def _field(item: dict, path: str) -> Any:
    cur: Any = item
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def _match(item: dict, condition: dict) -> bool:
    val = _field(item, condition["field"])
    op = condition["operator"]
    if op == "truthy":
        return bool(val)
    expected = condition.get("value")
    if op == "eq":
        return val == expected
    if op == "ne":
        return val != expected
    if op == "gt":
        return val is not None and val > expected
    if op == "lt":
        return val is not None and val < expected
    if op == "gte":
        return val is not None and val >= expected
    if op == "lte":
        return val is not None and val <= expected
    if op == "contains":
        return expected in val if val is not None else False
    if op == "in":
        return val in expected
    raise ValueError(f"unknown filter operator: {op}")


class FilterNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "filter"

    async def execute(self, input_data: dict, config: dict) -> dict:
        items_key = config.get("items_key", "items")
        items = input_data.get(items_key, []) or []
        condition = config["condition"]
        kept = [it for it in items if _match(it, condition)]
        return {"items": kept, "count": len(kept)}


registry.register(FilterNode)
