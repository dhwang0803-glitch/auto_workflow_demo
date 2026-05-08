"""Unit tests for `agent_loop.run_agent` (ADR-024 §3 ReAct driver)."""
from __future__ import annotations

from typing import AsyncIterator

import pytest

from app.agents.agent_loop import AgentResult, run_agent
from app.agents.tool import Tool, render_tool_catalog


# --- ScriptedBackend ------------------------------------------------------
#
# We can't reuse `StubLLMBackend` here — it's keyword-dispatched on the
# system prompt, not turn-by-turn scriptable. The agent loop needs a
# backend whose Nth call returns the Nth canned response.


class ScriptedBackend:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> str:
        self.calls.append(
            {
                "system": system,
                "user_message": user_message,
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise AssertionError(
                "ScriptedBackend exhausted — agent looped beyond expected"
            )
        return self._responses.pop(0)

    def stream(  # pragma: no cover — not exercised in agent loop
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    async def ready(self) -> bool:  # pragma: no cover
        return True

    async def aclose(self) -> None:  # pragma: no cover
        return None


# --- helper tools ---------------------------------------------------------


def echo_tool() -> Tool:
    async def handler(args: dict) -> dict:
        return {"echoed": args}

    return Tool(
        name="echo",
        description="Return the args unchanged. For testing.",
        parameters={"value": "any — passed through"},
        handler=handler,
    )


def adder_tool() -> Tool:
    async def handler(args: dict) -> int:
        return int(args.get("a", 0)) + int(args.get("b", 0))

    return Tool(
        name="add",
        description="Add two numbers.",
        parameters={"a": "int", "b": "int"},
        handler=handler,
    )


def raising_tool() -> Tool:
    async def handler(args: dict) -> dict:
        raise RuntimeError("simulated tool failure")

    return Tool(
        name="boom",
        description="Always raises. For testing error handling.",
        parameters={},
        handler=handler,
    )


# --- tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_immediate_finish() -> None:
    """Agent that finishes on turn 1 with no tool calls."""
    backend = ScriptedBackend(['<finish>{"answer": 42}</finish>'])
    result = await run_agent(
        backend,
        system_goal="Return 42.",
        user_request="What is the answer?",
        tools=[],
    )
    assert result.terminated_reason == "finish"
    assert result.final == {"answer": 42}
    assert len(result.steps) == 1
    assert result.steps[0].finish == {"answer": 42}


@pytest.mark.asyncio
async def test_single_tool_then_finish() -> None:
    backend = ScriptedBackend(
        [
            '<tool_call name="add">{"a": 2, "b": 3}</tool_call>',
            '<finish>{"sum": 5}</finish>',
        ]
    )
    result = await run_agent(
        backend,
        system_goal="Compute the sum.",
        user_request="Add 2 and 3.",
        tools=[adder_tool()],
    )
    assert result.terminated_reason == "finish"
    assert result.final == {"sum": 5}
    assert len(result.steps) == 2
    assert result.steps[0].tool_call == ("add", {"a": 2, "b": 3})
    assert result.steps[0].observation == "5"
    # The second turn's user_message must include the prior observation.
    assert "<tool_result tool=\"add\">" in backend.calls[1]["user_message"]
    assert "5" in backend.calls[1]["user_message"]


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_obs() -> None:
    """Calling a tool that isn't registered surfaces an error obs.

    The model gets a chance to recover by picking a different tool.
    """
    backend = ScriptedBackend(
        [
            '<tool_call name="nonexistent">{}</tool_call>',
            '<tool_call name="echo">{"v": 1}</tool_call>',
            '<finish>"done"</finish>',
        ]
    )
    result = await run_agent(
        backend,
        system_goal="Test tool not found.",
        user_request="x",
        tools=[echo_tool()],
    )
    assert result.terminated_reason == "finish"
    # First step error, second succeeds, third finishes.
    assert "not registered" in (result.steps[0].error or "")
    assert result.steps[1].tool_call == ("echo", {"v": 1})


@pytest.mark.asyncio
async def test_tool_handler_exception_forwarded_as_obs() -> None:
    backend = ScriptedBackend(
        [
            '<tool_call name="boom">{}</tool_call>',
            '<finish>"recovered"</finish>',
        ]
    )
    result = await run_agent(
        backend,
        system_goal="Test handler exception.",
        user_request="x",
        tools=[raising_tool()],
    )
    assert result.terminated_reason == "finish"
    obs = result.steps[0].observation or ""
    assert "RuntimeError" in obs
    assert "simulated tool failure" in obs


@pytest.mark.asyncio
async def test_no_progress_on_repeated_call() -> None:
    """Same tool + same args twice in a row → no_progress termination."""
    backend = ScriptedBackend(
        [
            '<tool_call name="echo">{"x": 1}</tool_call>',
            '<tool_call name="echo">{"x": 1}</tool_call>',
        ]
    )
    result = await run_agent(
        backend,
        system_goal="Test no-progress brake.",
        user_request="x",
        tools=[echo_tool()],
    )
    assert result.terminated_reason == "no_progress"
    assert result.final is None
    # The brake fires on the second call, not the first.
    assert len(result.steps) == 2


@pytest.mark.asyncio
async def test_max_iter_exhausted() -> None:
    """Agent that never finishes should hit max_iter."""
    # Three distinct calls, max_iter=3 → exhausted after the third.
    backend = ScriptedBackend(
        [
            '<tool_call name="echo">{"x": 1}</tool_call>',
            '<tool_call name="echo">{"x": 2}</tool_call>',
            '<tool_call name="echo">{"x": 3}</tool_call>',
        ]
    )
    result = await run_agent(
        backend,
        system_goal="Test max_iter.",
        user_request="x",
        tools=[echo_tool()],
        max_iter=3,
    )
    assert result.terminated_reason == "max_iter_exhausted"
    assert len(result.steps) == 3


@pytest.mark.asyncio
async def test_parse_error_terminates() -> None:
    backend = ScriptedBackend(["just some prose, no terminal block"])
    result = await run_agent(
        backend,
        system_goal="Test parse error.",
        user_request="x",
        tools=[echo_tool()],
    )
    assert result.terminated_reason == "parse_error"
    assert result.final is None
    assert "no <tool_call> or <finish>" in (result.steps[0].error or "")


@pytest.mark.asyncio
async def test_system_prompt_carries_tool_catalog() -> None:
    backend = ScriptedBackend(['<finish>{}</finish>'])
    await run_agent(
        backend,
        system_goal="Test that tools are advertised.",
        user_request="x",
        tools=[echo_tool(), adder_tool()],
    )
    sys_prompt = backend.calls[0]["system"]
    assert "echo" in sys_prompt
    assert "add" in sys_prompt
    assert "Output format" in sys_prompt


def test_render_tool_catalog_empty() -> None:
    assert render_tool_catalog([]) == "(no tools available)"


def test_render_tool_catalog_includes_params() -> None:
    out = render_tool_catalog([adder_tool()])
    assert "### add" in out
    assert "Add two numbers" in out
    assert "`a` — int" in out
    assert "`b` — int" in out
