"""AirtableListRecordsNode — Airtable REST API 레코드 목록 조회.

filterByFormula / maxRecords / view 는 query param 으로 전송.
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class AirtableListRecordsNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "airtable_list_records"

    async def execute(self, input_data: dict, config: dict) -> dict:
        api_token = config["api_token"]
        base_id = config["base_id"]
        table = quote(config["table"], safe="")
        url = f"https://api.airtable.com/v0/{base_id}/{table}"

        params: dict = {"maxRecords": config.get("max_records", 100)}
        if "filter_by_formula" in config:
            params["filterByFormula"] = config["filter_by_formula"]
        if "view" in config:
            params["view"] = config["view"]

        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_token}"},
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            records = data.get("records", [])
            return {
                "records": records,
                "offset": data.get("offset"),
                "count": len(records),
            }


registry.register(AirtableListRecordsNode)
