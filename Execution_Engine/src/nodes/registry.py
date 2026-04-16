"""NodeRegistry — type string → BaseNode class mapping."""
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
