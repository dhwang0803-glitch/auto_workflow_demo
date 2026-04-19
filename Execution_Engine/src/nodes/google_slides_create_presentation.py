"""GoogleSlidesCreatePresentationNode — ADR-019 Phase 5.

Creates a new blank presentation. Slide authoring (adding content,
layout, text) is out of scope for v1 — downstream nodes can hit the
batchUpdate API separately once the presentationId is available.

Required scope: `https://www.googleapis.com/auth/presentations`.
"""
from __future__ import annotations

from uuid import UUID

import httpx

from src.nodes.google_workspace import GoogleWorkspaceNode
from src.nodes.registry import registry

_CREATE_URL = "https://slides.googleapis.com/v1/presentations"


class GoogleSlidesCreatePresentationNode(GoogleWorkspaceNode):
    @property
    def node_type(self) -> str:
        return "google_slides_create_presentation"

    async def execute(self, input_data: dict, config: dict) -> dict:
        credential_id = UUID(config["credential_id"])
        title = config["title"]

        token = await self._ensure_fresh_token(credential_id)
        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                _CREATE_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                json={"title": title},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "presentation_id": data["presentationId"],
                "title": data.get("title", title),
                "revision_id": data.get("revisionId", ""),
            }


registry.register(GoogleSlidesCreatePresentationNode)
