"""ConditionNode — evaluates a condition on input_data, returns {"result": bool}.

Supported operators: eq, ne, gt, gte, lt, lte, in, not_in, contains.
"""
from __future__ import annotations

import operator

from src.nodes.base import BaseNode
from src.nodes.registry import registry

_OPS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
}


class ConditionNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "condition"

    async def execute(self, input_data: dict, config: dict) -> dict:
        left = input_data.get(config["left_field"])
        op = config["operator"]
        right = config["right_value"]

        if left is None:
            return {"result": False}

        if op in _OPS:
            return {"result": _OPS[op](left, right)}
        if op == "in":
            return {"result": left in right}
        if op == "not_in":
            return {"result": left not in right}
        if op == "contains":
            return {"result": right in left}

        raise ValueError(f"unknown operator: {op}")


registry.register(ConditionNode)
