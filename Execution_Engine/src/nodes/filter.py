"""FilterNode — filter an array.

Pull the array from input_data[items_key] and apply config.condition to each item.
operator supports eq/ne/gt/lt/gte/lte/contains/in/truthy.

For compound conditions (AND/OR), chain filters or fall back to a code node.
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
