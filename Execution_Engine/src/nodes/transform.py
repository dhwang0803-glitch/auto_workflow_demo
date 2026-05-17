"""TransformNode — declarative field mapping.

For each value in config.mapping:
- if the value matches the pattern "{input.foo.bar}", do a dotted-path lookup in input_data and substitute
- otherwise preserve the string/primitive value as-is

Missing keys fall back to config.defaults[key] if present, otherwise None.
"""
from __future__ import annotations

import re
from typing import Any

from src.nodes.base import BaseNode
from src.nodes.registry import registry


_TEMPLATE_RE = re.compile(r"^\{([a-zA-Z_][\w.]*)\}$")


def _resolve(path: str, ctx: dict) -> Any:
    parts = path.split(".")
    cur: Any = ctx
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return _MISSING
    return cur


_MISSING = object()


class TransformNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "transform"

    async def execute(self, input_data: dict, config: dict) -> dict:
        mapping: dict = config.get("mapping", {})
        defaults: dict = config.get("defaults", {})
        ctx = {"input": input_data}
        out: dict = {}
        for key, raw in mapping.items():
            if isinstance(raw, str):
                m = _TEMPLATE_RE.match(raw)
                if m:
                    resolved = _resolve(m.group(1), ctx)
                    out[key] = defaults.get(key) if resolved is _MISSING else resolved
                    continue
            out[key] = raw
        return out


registry.register(TransformNode)
