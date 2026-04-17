"""OpenAIChatNode — Chat Completions 단일 응답.

api_token 은 http_bearer credential_type 으로 주입되어 config 에 평문 존재.
"""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class OpenAIChatNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "openai_chat"

    async def execute(self, input_data: dict, config: dict) -> dict:
        api_token = config["api_token"]
        body: dict = {
            "model": config["model"],
            "messages": config["messages"],
        }
        if "temperature" in config:
            body["temperature"] = config["temperature"]
        if "max_tokens" in config:
            body["max_tokens"] = config["max_tokens"]

        timeout = config.get("timeout_seconds", 60)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_token}"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            return {
                "content": choice["message"]["content"],
                "model": data.get("model", config["model"]),
                "finish_reason": choice.get("finish_reason"),
                "usage": data.get("usage", {}),
            }


registry.register(OpenAIChatNode)
