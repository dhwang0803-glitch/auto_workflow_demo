"""AnthropicChatNode — Anthropic Messages API 단일 응답.

OpenAI 와 달리 x-api-key 헤더, system 은 top-level 필드, max_tokens 필수.
api_token 은 http_bearer credential_type 으로 주입되어 config 에 평문 존재.
"""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class AnthropicChatNode(BaseNode):
    display_name = "Anthropic Chat (Claude)"
    category = "ai"
    description = "Call Anthropic Messages API for a single Claude response."
    config_schema = {
        "type": "object",
        "required": ["api_token", "model", "messages", "max_tokens"],
        "properties": {
            "api_token": {"type": "string", "format": "secret_ref"},
            "model": {"type": "string", "examples": ["claude-opus-4-7", "claude-sonnet-4-6"]},
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["role", "content"],
                    "properties": {
                        "role": {"type": "string", "enum": ["user", "assistant"]},
                        "content": {"type": "string"},
                    },
                },
            },
            "max_tokens": {"type": "integer", "minimum": 1},
            "system": {"type": "string"},
            "temperature": {"type": "number", "minimum": 0, "maximum": 1},
            "timeout_seconds": {"type": "integer", "default": 60, "minimum": 1},
        },
    }

    @property
    def node_type(self) -> str:
        return "anthropic_chat"

    async def execute(self, input_data: dict, config: dict) -> dict:
        api_token = config["api_token"]
        body: dict = {
            "model": config["model"],
            "messages": config["messages"],
            "max_tokens": config["max_tokens"],
        }
        if "system" in config:
            body["system"] = config["system"]
        if "temperature" in config:
            body["temperature"] = config["temperature"]

        timeout = config.get("timeout_seconds", 60)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_token,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            # content is an array of content blocks; take first text block.
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break
            return {
                "content": text,
                "model": data.get("model", config["model"]),
                "stop_reason": data.get("stop_reason"),
                "usage": data.get("usage", {}),
            }


registry.register(AnthropicChatNode)
