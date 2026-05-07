"""Tests for the LangSmith no-op fallback in `app.agents.tracing`.

The contract this protects: importing the agent module under pytest must
not require a LangSmith API key, must not perform network I/O, and must
not change the behavior of decorated functions when tracing is off.

The env check happens at import time, so toggling the var during a test
run will NOT flip behavior — these tests cover the off-state only, which
is the one CI / local dev hits. The tracing-on path is exercised
end-to-end by PR-D's Modal smoke (`scripts/plan_13_reflective_smoke.py`)
where a real LangSmith run URL is asserted on.
"""
from __future__ import annotations

import inspect

import pytest

from app.agents.tracing import TRACING_ENABLED, traceable


def test_tracing_is_off_in_the_test_environment() -> None:
    """conftest.py doesn't set LANGCHAIN_TRACING_V2 and we don't want
    pytest to start phoning home. If this ever flips, an autouse env
    fixture is missing.
    """
    assert TRACING_ENABLED is False


def test_traceable_bare_decorator_returns_function_unchanged() -> None:
    @traceable
    def f(x: int) -> int:
        return x + 1

    assert f(2) == 3
    assert f.__name__ == "f"


def test_traceable_with_kwargs_returns_function_unchanged() -> None:
    @traceable(name="my-node", run_type="chain", tags=["agent"])
    def f(x: int) -> int:
        return x * 2

    assert f(3) == 6
    assert f.__name__ == "f"


@pytest.mark.asyncio
async def test_traceable_preserves_async_functions() -> None:
    @traceable(name="async-node")
    async def f(x: int) -> int:
        return x - 1

    assert inspect.iscoroutinefunction(f)
    assert await f(10) == 9
