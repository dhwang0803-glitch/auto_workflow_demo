"""GmailSendNode — ADR-019 Phase 5.

Posts a single outgoing message via Gmail's `users.messages.send` API.
The RFC-2822 body is base64url-encoded and stuffed into the `raw` field
per the API contract — we don't use MIME-multipart / attachments yet.

Required scope: `https://www.googleapis.com/auth/gmail.send`.
"""
from __future__ import annotations

import base64
from email.message import EmailMessage
from uuid import UUID

import httpx

from src.nodes.google_workspace import GoogleWorkspaceNode
from src.nodes.registry import registry

_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailSendNode(GoogleWorkspaceNode):
    @property
    def node_type(self) -> str:
        return "gmail_send"

    async def execute(self, input_data: dict, config: dict) -> dict:
        credential_id = UUID(config["credential_id"])
        to = config["to"]
        subject = config["subject"]
        body = config.get("body", "")
        cc = config.get("cc")
        bcc = config.get("bcc")
        body_html = config.get("body_html")

        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc
        if body_html:
            # Set plaintext first, then tack on the HTML alternative so
            # clients that can render HTML prefer it but text-only clients
            # still get readable content.
            msg.set_content(body or "")
            msg.add_alternative(body_html, subtype="html")
        else:
            msg.set_content(body)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

        token = await self._ensure_fresh_token(credential_id)
        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                _SEND_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                json={"raw": raw},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "message_id": data["id"],
                "thread_id": data.get("threadId", ""),
                "label_ids": data.get("labelIds", []),
            }


registry.register(GmailSendNode)
