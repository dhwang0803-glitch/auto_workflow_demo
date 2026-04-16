"""External webhook receiver — PLAN_05. No JWT auth, HMAC only."""
from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

from app.services.workflow_service import WorkflowService

router = APIRouter()


@router.post("/{path:path}", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(
    path: str,
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
) -> dict:
    svc: WorkflowService = request.app.state.workflow_service
    body = await request.body()
    full_path = f"/webhooks/{path}"
    ex = await svc.receive_webhook(full_path, body, x_webhook_signature)
    return {"execution_id": str(ex.id)}
