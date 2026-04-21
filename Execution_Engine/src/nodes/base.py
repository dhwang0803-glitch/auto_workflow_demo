"""BaseNode ABC — all node plugins inherit from this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class BaseNode(ABC):
    display_name: ClassVar[str] = ""
    category: ClassVar[str] = "misc"
    description: ClassVar[str] = ""
    config_schema: ClassVar[dict[str, Any]] = {}

    @property
    @abstractmethod
    def node_type(self) -> str: ...

    @abstractmethod
    async def execute(self, input_data: dict, config: dict) -> dict: ...
