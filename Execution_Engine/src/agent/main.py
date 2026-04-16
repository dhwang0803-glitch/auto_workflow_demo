"""Agent daemon — WebSocket client that connects to the central server,
sends heartbeats, and executes workflows on command.

Runs inside a customer VPC with no direct DB access. All execution state
is reported back to the server via WebSocket messages.
"""
from __future__ import annotations

import asyncio
import json
import logging

import websockets

from src.agent.command_handler import handle_execute
from src.nodes.registry import NodeRegistry, registry as default_registry

logger = logging.getLogger(__name__)


async def _heartbeat_loop(ws, interval: int = 15) -> None:
    while True:
        await ws.send(json.dumps({"type": "heartbeat"}))
        await asyncio.sleep(interval)


async def run_agent(
    server_url: str,
    token: str,
    *,
    node_registry: NodeRegistry | None = None,
    heartbeat_interval: int = 15,
) -> None:
    reg = node_registry or default_registry
    uri = f"{server_url}?token={token}"
    logger.info("connecting to %s", server_url)

    async with websockets.connect(uri) as ws:
        logger.info("connected, starting heartbeat (interval=%ds)", heartbeat_interval)
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(ws, interval=heartbeat_interval)
        )
        try:
            async for raw in ws:
                msg = json.loads(raw)
                msg_type = msg.get("type")
                if msg_type == "heartbeat_ack":
                    continue
                elif msg_type == "execute":
                    asyncio.create_task(handle_execute(ws, msg, reg))
                elif msg_type == "credential":
                    logger.debug("credential response received")
                elif msg_type == "error":
                    logger.warning("server error: %s", msg.get("message"))
                else:
                    logger.debug("ignoring unknown message type: %s", msg_type)
        finally:
            heartbeat_task.cancel()
