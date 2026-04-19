"""GoogleDocsAppendTextNode — ADR-019 Phase 5.

Appends text to the end of a Google Doc using the `documents.batchUpdate`
API with a single `insertText` request. Uses `endOfSegmentLocation` so
we don't have to first GET the document to discover its current length.

Required scope: `https://www.googleapis.com/auth/documents`.
"""
from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

import httpx

from src.nodes.google_workspace import GoogleWorkspaceNode
from src.nodes.registry import registry


class GoogleDocsAppendTextNode(GoogleWorkspaceNode):
    @property
    def node_type(self) -> str:
        return "google_docs_append_text"

    async def execute(self, input_data: dict, config: dict) -> dict:
        credential_id = UUID(config["credential_id"])
        document_id = config["document_id"]
        text = config["text"]

        token = await self._ensure_fresh_token(credential_id)
        url = (
            f"https://docs.googleapis.com/v1/documents/{quote(document_id)}:batchUpdate"
        )
        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                json={
                    "requests": [
                        {
                            "insertText": {
                                # endOfSegmentLocation with empty {} targets the
                                # body segment — no need to GET the doc first to
                                # find the current endIndex.
                                "endOfSegmentLocation": {},
                                "text": text,
                            }
                        }
                    ]
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "document_id": data.get("documentId", document_id),
                "replies_count": len(data.get("replies", [])),
            }


registry.register(GoogleDocsAppendTextNode)
