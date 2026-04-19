"""GoogleDriveUploadFileNode — ADR-019 Phase 5.

Creates a new file on Drive via multipart upload (metadata + content in
one request). Keeps the content as UTF-8 text for now — binary/large
uploads would need resumable-upload support, which is deferred until a
workflow actually needs it.

Required scope: `https://www.googleapis.com/auth/drive.file`
(minimum-privilege: the app only sees files it created).
"""
from __future__ import annotations

import json
from uuid import UUID

import httpx

from src.nodes.google_workspace import GoogleWorkspaceNode
from src.nodes.registry import registry

_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


class GoogleDriveUploadFileNode(GoogleWorkspaceNode):
    @property
    def node_type(self) -> str:
        return "google_drive_upload_file"

    async def execute(self, input_data: dict, config: dict) -> dict:
        credential_id = UUID(config["credential_id"])
        name = config["name"]
        content = config.get("content", "")
        mime_type = config.get("mime_type", "text/plain")
        parent_folder_id = config.get("parent_folder_id")

        metadata: dict = {"name": name}
        if parent_folder_id:
            metadata["parents"] = [parent_folder_id]

        # Drive's "multipart/related" upload boundary framing. Any string
        # not colliding with the payload works; keep it short and static
        # so tests can assert on it if they want.
        boundary = "drive-upload-boundary-xxx"
        body_bytes = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8") + (
            content.encode("utf-8") if isinstance(content, str) else content
        ) + f"\r\n--{boundary}--".encode("utf-8")

        token = await self._ensure_fresh_token(credential_id)
        timeout = config.get("timeout_seconds", 60)
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                _UPLOAD_URL,
                params={"uploadType": "multipart"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                    "Accept": "application/json",
                },
                content=body_bytes,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "file_id": data["id"],
                "name": data.get("name", name),
                "mime_type": data.get("mimeType", mime_type),
            }


registry.register(GoogleDriveUploadFileNode)
