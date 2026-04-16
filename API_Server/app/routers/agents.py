"""Agent registration + WebSocket — PLAN_06."""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt as pyjwt

from auto_workflow_database.repositories.base import User

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect, status

from app.dependencies import get_current_user
from app.models.agent import AgentRegisterRequest, AgentRegisterResponse
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
    agent = await svc.register_agent(user, body.public_key, body.gpu_info)
    s = request.app.state.settings
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {
            "sub": f"agent:{agent.id}",
            "purpose": "agent",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=s.agent_jwt_ttl_hours)).timestamp()),
        },
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    return AgentRegisterResponse(agent_id=agent.id, agent_token=token)


@router.websocket("/ws")
async def agent_ws(
    websocket: WebSocket,
    token: str = Query(),
) -> None:
    s = websocket.app.state.settings
    try:
        payload = pyjwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except pyjwt.InvalidTokenError:
        await websocket.close(code=4001, reason="invalid token")
        return
    sub = payload.get("sub", "")
    if not sub.startswith("agent:") or payload.get("purpose") != "agent":
        await websocket.close(code=4001, reason="not an agent token")
        return
    agent_id = UUID(sub.removeprefix("agent:"))
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
