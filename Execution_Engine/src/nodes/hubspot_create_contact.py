"""HubSpotCreateContactNode — create a Contact via the HubSpot CRM API.

api_token is a HubSpot Private App token, injected via http_bearer.
The properties dict contains HubSpot standard fields (email/firstname/lastname) + custom fields.
"""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class HubSpotCreateContactNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "hubspot_create_contact"

    async def execute(self, input_data: dict, config: dict) -> dict:
        api_token = config["api_token"]
        body = {"properties": config["properties"]}

        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.hubapi.com/crm/v3/objects/contacts",
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "contact_id": data["id"],
                "created_at": data.get("createdAt", ""),
                "properties": data.get("properties", {}),
            }


registry.register(HubSpotCreateContactNode)
