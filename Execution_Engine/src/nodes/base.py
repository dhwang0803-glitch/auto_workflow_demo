"""BaseNode ABC — all node plugins inherit from this."""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseNode(ABC):
    @property
    @abstractmethod
    def node_type(self) -> str: ...

    @abstractmethod
    async def execute(self, input_data: dict, config: dict) -> dict: ...
