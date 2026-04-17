"""NotionQueryDatabaseNode — Notion Database Query API.

api_token 은 http_bearer credential_type 으로 주입되어 config 에 평문 존재.
Notion-Version 헤더는 API 계약상 필수.
"""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class NotionQueryDatabaseNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "notion_query_database"

    async def execute(self, input_data: dict, config: dict) -> dict:
        api_token = config["api_token"]
        database_id = config["database_id"]
        body: dict = {"page_size": config.get("page_size", 100)}
        if "filter" in config:
            body["filter"] = config["filter"]
        if "sorts" in config:
            body["sorts"] = config["sorts"]

        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"https://api.notion.com/v1/databases/{database_id}/query",
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            return {
                "results": results,
                "has_more": data.get("has_more", False),
                "next_cursor": data.get("next_cursor"),
                "count": len(results),
            }


registry.register(NotionQueryDatabaseNode)
