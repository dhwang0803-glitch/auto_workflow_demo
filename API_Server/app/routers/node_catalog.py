"""Node catalog router — exposes Execution_Engine's NodeRegistry to Frontend.

The registry lives in Execution_Engine (installed as `auto-workflow-execution-engine`
in API_Server's container). Imports are function-local so API_Server is
importable in environments that haven't `pip install -e`'d Execution_Engine
yet — matches the pattern in `app/services/workflow_service.py`.
"""
from __future__ import annotations

from auto_workflow_database.repositories.base import User
from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models.node import NodeCatalogEntry, NodeCatalogResponse

router = APIRouter()


@router.get("", response_model=NodeCatalogResponse)
async def get_catalog(
    _user: User = Depends(get_current_user),
) -> NodeCatalogResponse:
    import src.nodes  # noqa: F401 — triggers node self-registration
    from src.nodes.registry import registry as node_registry

    entries: list[NodeCatalogEntry] = []
    for node_type in node_registry.list_types():
        cls = node_registry.get(node_type)
        entries.append(
            NodeCatalogEntry(
                type=node_type,
                display_name=cls.display_name or node_type,
                category=cls.category,
                description=cls.description,
                config_schema=cls.config_schema,
            )
        )
    categories = sorted({e.category for e in entries})
    return NodeCatalogResponse(
        nodes=entries,
        total=len(entries),
        categories=categories,
    )
