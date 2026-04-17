"""SlackNotifyNode — Incoming Webhook 알림 전송."""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class SlackNotifyNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "slack_notify"

    async def execute(self, input_data: dict, config: dict) -> dict:
        webhook_url = config["webhook_url"]
        text = config["text"]
        timeout = config.get("timeout_seconds", 10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(webhook_url, json={"text": text})
            resp.raise_for_status()
            return {"status_code": resp.status_code, "ok": True}


registry.register(SlackNotifyNode)
