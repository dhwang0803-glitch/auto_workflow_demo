"""PLAN_07 — EmailSendNode tests.

aiosmtplib.send 는 AsyncMock 으로 패치하여 실제 SMTP 연결 없이 호출 인자 검증.
"""
from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import AsyncMock

import aiosmtplib
import pytest

from src.nodes.email_send import EmailSendNode


@pytest.fixture
def node():
    return EmailSendNode()


@pytest.fixture
def base_config():
    return {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "user@example.com",
        "smtp_password": "secret",
        "from": "bot@example.com",
        "to": ["alice@example.com"],
        "subject": "hello",
        "body": "plain text body",
    }


async def test_email_send_success(node, base_config, monkeypatch):
    mock_send = AsyncMock(return_value=({}, "250 OK"))
    monkeypatch.setattr(aiosmtplib, "send", mock_send)

    result = await node.execute({}, base_config)

    assert result == {"sent": True, "to": ["alice@example.com"]}
    mock_send.assert_awaited_once()


async def test_email_send_passes_credentials(node, base_config, monkeypatch):
    mock_send = AsyncMock(return_value=({}, "250 OK"))
    monkeypatch.setattr(aiosmtplib, "send", mock_send)

    await node.execute({}, base_config)

    kwargs = mock_send.await_args.kwargs
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 587
    assert kwargs["username"] == "user@example.com"
    assert kwargs["password"] == "secret"
    assert kwargs["start_tls"] is True


async def test_email_send_with_html_body(node, base_config, monkeypatch):
    mock_send = AsyncMock(return_value=({}, "250 OK"))
    monkeypatch.setattr(aiosmtplib, "send", mock_send)

    await node.execute({}, {**base_config, "body_html": "<p>hi</p>"})

    msg = mock_send.await_args.args[0]
    assert isinstance(msg, EmailMessage)
    assert msg.is_multipart()
    payload_types = [part.get_content_type() for part in msg.iter_parts()]
    assert "text/plain" in payload_types
    assert "text/html" in payload_types


async def test_email_send_smtp_error_propagates(node, base_config, monkeypatch):
    mock_send = AsyncMock(side_effect=aiosmtplib.SMTPException("auth failed"))
    monkeypatch.setattr(aiosmtplib, "send", mock_send)

    with pytest.raises(aiosmtplib.SMTPException):
        await node.execute({}, base_config)
