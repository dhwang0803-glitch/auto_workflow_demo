"""EmailSendNode — send over SMTP via aiosmtplib.

The filename `email_send.py` avoids shadowing the stdlib `email` package.
The credential injected via config is used once as a function-local variable and then leaves scope.
"""
from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class EmailSendNode(BaseNode):
    display_name = "Email Send (SMTP)"
    category = "email"
    description = "Send an email via SMTP with optional HTML alternative."
    config_schema = {
        "type": "object",
        "required": [
            "from", "to", "subject", "body",
            "smtp_host", "smtp_port", "smtp_user", "smtp_password",
        ],
        "properties": {
            "from": {"type": "string", "format": "email"},
            "to": {"type": "array", "items": {"type": "string", "format": "email"}},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "body_html": {"type": "string"},
            "smtp_host": {"type": "string"},
            "smtp_port": {"type": "integer"},
            "smtp_user": {"type": "string"},
            "smtp_password": {"type": "string", "format": "secret_ref"},
            "use_starttls": {"type": "boolean", "default": True},
            "timeout_seconds": {"type": "integer", "default": 30, "minimum": 1},
        },
    }

    @property
    def node_type(self) -> str:
        return "email_send"

    async def execute(self, input_data: dict, config: dict) -> dict:
        msg = EmailMessage()
        msg["From"] = config["from"]
        msg["To"] = ", ".join(config["to"])
        msg["Subject"] = config["subject"]
        msg.set_content(config["body"])
        if "body_html" in config:
            msg.add_alternative(config["body_html"], subtype="html")

        await aiosmtplib.send(
            msg,
            hostname=config["smtp_host"],
            port=config["smtp_port"],
            username=config["smtp_user"],
            password=config["smtp_password"],
            start_tls=config.get("use_starttls", True),
            timeout=config.get("timeout_seconds", 30),
        )
        return {"sent": True, "to": config["to"]}


registry.register(EmailSendNode)
