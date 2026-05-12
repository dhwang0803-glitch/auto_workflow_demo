"""Workflow revision recording — PLAN_14 PR-B.

Thin orchestration around `WorkflowRevisionRepository.record` that picks
the parent revision so callers don't have to. Lives next to
`workflow_service.py` instead of inside it because (a) the same hook
will be called from `/v1/compose` Apply later (PLAN_14 PR-D/E), and
(b) the parent-lookup is the only non-trivial step — pushing it into
each call site would mean 3+ near-identical copies.

The repository is single-write (no update/delete), so this helper has
no other methods — `WorkflowService.list_revisions` reads through the
repo directly with an ownership check.
"""
from __future__ import annotations

from uuid import UUID

from auto_workflow_database.repositories.base import (
    WorkflowRevision,
    WorkflowRevisionRepository,
    WorkflowRevisionSource,
)


async def record_save_revision(
    repo: WorkflowRevisionRepository,
    *,
    workflow_id: UUID,
    payload: dict,
    source: WorkflowRevisionSource,
    created_by: UUID,
) -> WorkflowRevision:
    """Append a revision row whose parent is the current latest.

    On the seed save the workflow has no prior revisions and
    `parent_revision_id` lands NULL. On every subsequent save we look
    up the head (`list_by_workflow(limit=1)`) and link to it — this
    gives PLAN_14 PR-C's diff function a deterministic v1/v2 chain
    without storing the diff itself.
    """
    head = await repo.list_by_workflow(workflow_id, limit=1)
    parent_id = head[0].id if head else None
    return await repo.record(
        workflow_id=workflow_id,
        source=source,
        payload=payload,
        parent_revision_id=parent_id,
        created_by=created_by,
    )
