"""Unit tests for `_tool_parse.parse_action` (ADR-024 §3 wire format)."""
from __future__ import annotations

import pytest

from app.agents._tool_parse import (
    Finish,
    ToolCall,
    ToolParseError,
    parse_action,
)


def test_parses_minimal_tool_call() -> None:
    out = parse_action(
        '<tool_call name="extract">\n{"chunk": "abc"}\n</tool_call>'
    )
    assert isinstance(out, ToolCall)
    assert out.name == "extract"
    assert out.args == {"chunk": "abc"}


def test_parses_tool_call_with_preamble() -> None:
    raw = (
        "Let me first search for relevant context.\n"
        '<tool_call name="search">\n{"query": "refunds"}\n</tool_call>'
    )
    out = parse_action(raw)
    assert isinstance(out, ToolCall)
    assert out.name == "search"
    assert out.args == {"query": "refunds"}


def test_parses_finish_with_payload() -> None:
    out = parse_action('<finish>\n{"drafts": [], "count": 0}\n</finish>')
    assert isinstance(out, Finish)
    assert out.result == {"drafts": [], "count": 0}


def test_parses_empty_finish_as_none() -> None:
    out = parse_action("<finish></finish>")
    assert isinstance(out, Finish)
    assert out.result is None


def test_parses_no_arg_tool_call() -> None:
    out = parse_action('<tool_call name="finalize"></tool_call>')
    assert isinstance(out, ToolCall)
    assert out.args == {}


def test_finish_wins_when_both_blocks_present() -> None:
    """Spec disallows both, but if a model emits both, finish wins.

    The agent loop prefers to terminate over loop, since a stuck-but-
    finishing model is the safer failure mode.
    """
    raw = (
        '<tool_call name="extract">{}</tool_call>\n'
        '<finish>{"done": true}</finish>'
    )
    out = parse_action(raw)
    assert isinstance(out, Finish)


def test_strips_json_code_fence_inside_tool_call() -> None:
    raw = (
        '<tool_call name="extract">\n'
        "```json\n"
        '{"chunk": "abc"}\n'
        "```\n"
        "</tool_call>"
    )
    out = parse_action(raw)
    assert isinstance(out, ToolCall)
    assert out.args == {"chunk": "abc"}


def test_strips_plain_code_fence() -> None:
    raw = (
        "<finish>\n"
        "```\n"
        "{\"x\": 1}\n"
        "```\n"
        "</finish>"
    )
    out = parse_action(raw)
    assert isinstance(out, Finish)
    assert out.result == {"x": 1}


def test_raises_on_no_block() -> None:
    with pytest.raises(ToolParseError):
        parse_action("Just some text with no terminal block.")


def test_raises_on_invalid_json_args() -> None:
    with pytest.raises(ToolParseError) as exc_info:
        parse_action('<tool_call name="x">{not json}</tool_call>')
    assert "x" in str(exc_info.value)
    # raw text preserved for trace
    assert "not json" in exc_info.value.raw


def test_raises_on_non_object_args() -> None:
    with pytest.raises(ToolParseError) as exc_info:
        parse_action('<tool_call name="x">[1, 2, 3]</tool_call>')
    assert "JSON object" in str(exc_info.value)


def test_handles_multiline_json_body() -> None:
    raw = (
        '<tool_call name="extract">\n'
        "{\n"
        '  "chunk": "long text",\n'
        '  "domain": "ecommerce",\n'
        '  "max_iter": 2\n'
        "}\n"
        "</tool_call>"
    )
    out = parse_action(raw)
    assert isinstance(out, ToolCall)
    assert out.args["chunk"] == "long text"
    assert out.args["domain"] == "ecommerce"
    assert out.args["max_iter"] == 2
