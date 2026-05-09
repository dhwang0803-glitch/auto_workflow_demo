"""Unit tests for `_tool_parse.parse_action` (ADR-024 §3, JSON envelope)."""
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
        '{"action": "tool_call", "name": "extract", "args": {"chunk": "abc"}}'
    )
    assert isinstance(out, ToolCall)
    assert out.name == "extract"
    assert out.args == {"chunk": "abc"}


def test_parses_tool_call_with_thought_field() -> None:
    out = parse_action(
        '{"thought": "let me search first", "action": "tool_call", '
        '"name": "search", "args": {"query": "refunds"}}'
    )
    assert isinstance(out, ToolCall)
    assert out.name == "search"
    assert out.args == {"query": "refunds"}


def test_parses_tool_call_with_prose_preamble() -> None:
    raw = (
        "Let me first search for relevant context.\n\n"
        '{"action": "tool_call", "name": "search", "args": {"query": "refunds"}}'
    )
    out = parse_action(raw)
    assert isinstance(out, ToolCall)
    assert out.name == "search"


def test_parses_finish_with_payload() -> None:
    out = parse_action(
        '{"action": "finish", "result": {"drafts": [], "count": 0}}'
    )
    assert isinstance(out, Finish)
    assert out.result == {"drafts": [], "count": 0}


def test_parses_finish_without_result_as_none() -> None:
    out = parse_action('{"action": "finish"}')
    assert isinstance(out, Finish)
    assert out.result is None


def test_parses_no_arg_tool_call() -> None:
    out = parse_action('{"action": "tool_call", "name": "finalize", "args": {}}')
    assert isinstance(out, ToolCall)
    assert out.args == {}


def test_parses_no_arg_tool_call_with_args_omitted() -> None:
    out = parse_action('{"action": "tool_call", "name": "finalize"}')
    assert isinstance(out, ToolCall)
    assert out.args == {}


def test_last_envelope_wins_when_multiple_present() -> None:
    """If the model emits multiple JSON objects, the last is the action.

    Mirrors the prior `<finish>` over `<tool_call>` precedence — a model
    that sketched an example then committed to a different action gets
    the committed one honored, since it's the terminal block.
    """
    raw = (
        '{"action": "tool_call", "name": "extract", "args": {}}\n'
        '{"action": "finish", "result": {"done": true}}'
    )
    out = parse_action(raw)
    assert isinstance(out, Finish)
    assert out.result == {"done": True}


def test_parses_when_wrapped_in_code_fence() -> None:
    raw = (
        "Here is my decision:\n"
        "```json\n"
        '{"action": "tool_call", "name": "extract", "args": {"x": 1}}\n'
        "```"
    )
    out = parse_action(raw)
    assert isinstance(out, ToolCall)
    assert out.name == "extract"
    assert out.args == {"x": 1}


def test_raises_on_no_json_object() -> None:
    with pytest.raises(ToolParseError) as exc_info:
        parse_action("Just some text with no JSON envelope at all.")
    assert "no JSON action object" in str(exc_info.value)


def test_raises_on_unknown_action() -> None:
    with pytest.raises(ToolParseError) as exc_info:
        parse_action('{"action": "do_something", "name": "x"}')
    assert "unknown action" in str(exc_info.value)


def test_raises_on_missing_action() -> None:
    with pytest.raises(ToolParseError):
        parse_action('{"thought": "no action key here"}')


def test_raises_on_missing_name_for_tool_call() -> None:
    with pytest.raises(ToolParseError) as exc_info:
        parse_action('{"action": "tool_call", "args": {}}')
    assert "name" in str(exc_info.value)


def test_raises_on_non_object_args() -> None:
    with pytest.raises(ToolParseError) as exc_info:
        parse_action('{"action": "tool_call", "name": "x", "args": [1, 2, 3]}')
    assert "object" in str(exc_info.value)


def test_handles_multiline_json_body() -> None:
    raw = (
        "{\n"
        '  "thought": "Long reasoning across\\nmultiple lines.",\n'
        '  "action": "tool_call",\n'
        '  "name": "extract",\n'
        '  "args": {\n'
        '    "chunk": "long text",\n'
        '    "domain": "ecommerce",\n'
        '    "max_iter": 2\n'
        "  }\n"
        "}"
    )
    out = parse_action(raw)
    assert isinstance(out, ToolCall)
    assert out.args["chunk"] == "long text"
    assert out.args["domain"] == "ecommerce"
    assert out.args["max_iter"] == 2


def test_raw_text_preserved_on_parse_error() -> None:
    """ToolParseError carries the raw response for trace debugging."""
    raw = '{"action": "tool_call", "name": "x", "args": "not an object"}'
    with pytest.raises(ToolParseError) as exc_info:
        parse_action(raw)
    assert exc_info.value.raw == raw
