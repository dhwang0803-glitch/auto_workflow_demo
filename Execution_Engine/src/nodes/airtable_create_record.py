"""AirtableCreateRecordNode — Airtable REST API 레코드 1건 생성.

URL 은 base_id + table 로 런타임 합성. table 은 name 또는 tblXXXX id 둘 다 허용.
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
        # table 은 한글/공백 포함 가능 → path segment 인코딩 필요 (slash 보존 금지)
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
