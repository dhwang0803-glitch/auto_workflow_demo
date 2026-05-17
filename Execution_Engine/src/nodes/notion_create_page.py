"""NotionCreatePageNode — create a page via the Notion API v1.

api_token is injected via the http_bearer credential_type, so it exists in config in plaintext.
The Notion-Version header is required by the API contract.
"""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class NotionCreatePageNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "notion_create_page"

    async def execute(self, input_data: dict, config: dict) -> dict:
        api_token = config["api_token"]
        body: dict = {
            "parent": config["parent"],
            "properties": config["properties"],
        }
        if "children" in config:
            body["children"] = config["children"]

        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.notion.com/v1/pages",
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "page_id": data["id"],
                "url": data.get("url", ""),
            }


registry.register(NotionCreatePageNode)
