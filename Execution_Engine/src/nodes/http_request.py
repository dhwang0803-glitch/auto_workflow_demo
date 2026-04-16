"""HttpRequestNode — external API call via httpx."""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class HttpRequestNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "http_request"

    async def execute(self, input_data: dict, config: dict) -> dict:
        method = config.get("method", "GET").upper()
        url = config["url"]
        headers = config.get("headers", {})
        body = config.get("body")
        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, headers=headers, json=body)
            return {
                "status_code": resp.status_code,
                "body": resp.text,
                "headers": dict(resp.headers),
            }


registry.register(HttpRequestNode)
