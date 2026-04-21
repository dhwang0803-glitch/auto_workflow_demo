"""Node metadata (display_name / category / description / config_schema) —
enables API_Server to expose a rich node catalog to the Frontend editor.

BaseNode exposes default empty values so unmigrated nodes keep working; the
three nodes migrated in this PR (http_request / email_send / anthropic_chat)
must have all four fields populated.
"""
from __future__ import annotations

import pytest

from src.nodes.anthropic_chat import AnthropicChatNode
from src.nodes.base import BaseNode
from src.nodes.email_send import EmailSendNode
from src.nodes.http_request import HttpRequestNode


def test_base_node_defaults_are_safe():
    assert BaseNode.display_name == ""
    assert BaseNode.category == "misc"
    assert BaseNode.description == ""
    assert BaseNode.config_schema == {}


@pytest.mark.parametrize(
    "node_cls,expected_category",
    [
        (HttpRequestNode, "network"),
        (EmailSendNode, "email"),
        (AnthropicChatNode, "ai"),
    ],
)
def test_migrated_node_has_full_metadata(node_cls, expected_category):
    assert node_cls.display_name
    assert node_cls.category == expected_category
    assert node_cls.description
    assert isinstance(node_cls.config_schema, dict)
    assert node_cls.config_schema.get("type") == "object"
    assert "properties" in node_cls.config_schema
    assert "required" in node_cls.config_schema


def test_http_request_schema_covers_execute_fields():
    props = HttpRequestNode.config_schema["properties"]
    # Every field the execute() method reads from config must appear.
    assert set(props) >= {"url", "method", "headers", "body", "timeout_seconds"}
    assert HttpRequestNode.config_schema["required"] == ["url"]


def test_email_send_password_marked_as_secret():
    schema = EmailSendNode.config_schema["properties"]["smtp_password"]
    assert schema["format"] == "secret_ref"


def test_anthropic_api_token_marked_as_secret():
    schema = AnthropicChatNode.config_schema["properties"]["api_token"]
    assert schema["format"] == "secret_ref"
