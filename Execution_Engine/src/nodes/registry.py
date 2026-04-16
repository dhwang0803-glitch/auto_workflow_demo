"""NodeRegistry — type string → BaseNode **class** mapping.

This is a class catalog, not an instance store. get() returns the class
itself so callers create a fresh instance per invocation: registry.get(type)().
A workflow with five http_request nodes produces five independent instances,
each with its own config — safe for asyncio.gather parallel execution.
"""
from __future__ import annotations

from src.nodes.base import BaseNode


class NodeRegistry:
    def __init__(self) -> None:
        self._types: dict[str, type[BaseNode]] = {}

    def register(self, node_class: type[BaseNode]) -> None:
        instance = node_class()
        self._types[instance.node_type] = node_class

    def get(self, node_type: str) -> type[BaseNode]:
        cls = self._types.get(node_type)
        if cls is None:
            raise KeyError(f"unknown node type: {node_type}")
        return cls

    def list_types(self) -> list[str]:
        return sorted(self._types.keys())


registry = NodeRegistry()
