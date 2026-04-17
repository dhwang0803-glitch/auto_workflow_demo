"""DiscordNotifyNode — Discord Incoming Webhook 메시지 전송.

webhook_url 자체가 secret 이므로 별도 credential 불필요 (Slack 동일 패턴).
"""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class DiscordNotifyNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "discord_notify"

    async def execute(self, input_data: dict, config: dict) -> dict:
        webhook_url = config["webhook_url"]
        body: dict = {"content": config["content"]}
        if "username" in config:
            body["username"] = config["username"]

        timeout = config.get("timeout_seconds", 10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(webhook_url, json=body)
            resp.raise_for_status()
            return {"status_code": resp.status_code, "ok": True}


registry.register(DiscordNotifyNode)
