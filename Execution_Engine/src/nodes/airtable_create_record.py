"""AirtableCreateRecordNode — create a single record via the Airtable REST API.

The URL is composed at runtime from base_id + table. table accepts both a name and a tblXXXX id.
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class AirtableCreateRecordNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "airtable_create_record"

    async def execute(self, input_data: dict, config: dict) -> dict:
        api_token = config["api_token"]
        base_id = config["base_id"]
        # table may contain non-ASCII chars / spaces → path-segment encode (do not preserve slashes)
        table = quote(config["table"], safe="")
        url = f"https://api.airtable.com/v0/{base_id}/{table}"

        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_token}"},
                json={"fields": config["fields"]},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "record_id": data["id"],
                "created_time": data.get("createdTime", ""),
                "fields": data.get("fields", {}),
            }


registry.register(AirtableCreateRecordNode)
