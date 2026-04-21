"""Pydantic response schemas for the node catalog endpoint.

Mirrors the metadata surface on Execution_Engine's BaseNode. Unmigrated nodes
fall back to empty defaults — Frontend must treat missing `config_schema` as
"render a JSON textarea" rather than crashing.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class NodeCatalogEntry(BaseModel):
    type: str
    display_name: str
    category: str
    description: str
    config_schema: dict[str, Any]


class NodeCatalogResponse(BaseModel):
    nodes: list[NodeCatalogEntry]
    total: int
    categories: list[str]
