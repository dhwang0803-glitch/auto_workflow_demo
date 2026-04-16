"""Agent registration + WebSocket — PLAN_06."""
from __future__ import annotations

import base64
import logging
from uuid import UUID

from auto_workflow_database.repositories.base import User

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect, status

from app.dependencies import get_current_user
from app.errors import InvalidTokenError
from app.models.agent import AgentRegisterRequest, AgentRegisterResponse
from app.services.auth_service import AuthService
from app.services.workflow_service import WorkflowService

router = APIRouter()
logger = logging.getLogger("app.agents")


@router.post("/register", response_model=AgentRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_agent(
    body: AgentRegisterRequest,
    request: Request,
    user: User = Depends(get_current_user),
) -> AgentRegisterResponse:
    svc: WorkflowService = request.app.state.workflow_service
    auth: AuthService = request.app.state.auth_service
    agent = await svc.register_agent(user, body.public_key, body.gpu_info)
    token = auth.issue_agent_token(agent.id)
    return AgentRegisterResponse(agent_id=agent.id, agent_token=token)


@router.websocket("/ws")
async def agent_ws(
    websocket: WebSocket,
    token: str = Query(),
) -> None:
    auth: AuthService = websocket.app.state.auth_service
    try:
        agent_id = auth.decode_agent_token(token)
    except InvalidTokenError:
        await websocket.close(code=4001, reason="invalid token")
        return
    agent_repo = websocket.app.state.agent_repo
    agent = await agent_repo.get(agent_id)
    if agent is None:
        await websocket.close(code=4004, reason="agent not found")
        return

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "heartbeat":
                await agent_repo.update_heartbeat(agent_id)
                await websocket.send_json({"type": "heartbeat_ack"})
            elif msg_type == "get_credential":
                cred_id = data.get("credential_id")
                if not cred_id:
                    await websocket.send_json({"type": "error", "message": "credential_id required"})
                    continue
                try:
                    cred_store = websocket.app.state.credential_store
                    envelope = await cred_store.retrieve_for_agent(
                        UUID(cred_id), agent_public_key_pem=agent.public_key.encode()
                    )
                    await websocket.send_json({
                        "type": "credential",
                        "payload": {
                            "wrapped_key": base64.b64encode(envelope.wrapped_key).decode(),
                            "nonce": base64.b64encode(envelope.nonce).decode(),
                            "ciphertext": base64.b64encode(envelope.ciphertext).decode(),
                        },
                    })
                except Exception as e:
                    await websocket.send_json({"type": "error", "message": str(e)})
            else:
                await websocket.send_json({"type": "error", "message": f"unknown type: {msg_type}"})
    except WebSocketDisconnect:
        pass
