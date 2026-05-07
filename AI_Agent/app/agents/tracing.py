"""LangSmith tracing helper with hermetic fallback.

PLAN_13 §5.1 wires `langsmith.traceable` onto LLM-touching call sites so
the LangSmith UI can show the full trace tree (node enter/exit + LLM
payload). But unit tests, local dev without an API key, and CI shouldn't
need network access or a key just to import the agent module.

The contract:

- When `LANGCHAIN_TRACING_V2` is truthy AT IMPORT TIME, `traceable` is
  the real decorator from `langsmith` and behaves exactly as documented
  there. Both `@traceable` and `@traceable(name="...", run_type="llm")`
  forms work.

- When `LANGCHAIN_TRACING_V2` is unset or falsy, `traceable` becomes a
  pass-through that preserves the wrapped function's signature and async
  nature. No network, no API key required, no ImportError surface — the
  agent module imports cleanly under pytest.

The env check happens once at import; flipping the env mid-process does
NOT toggle behavior. That's intentional: langsmith's own client reads
the env once at construction too, so a second-read divergence would
just be misleading.
"""
from __future__ import annotations

import os
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def _env_truthy(name: str) -> bool:
    val = os.environ.get(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


TRACING_ENABLED = _env_truthy("LANGCHAIN_TRACING_V2")


if TRACING_ENABLED:
    # Re-export the real decorator. Import is deferred behind the env
    # check so a missing langsmith install doesn't break local dev — but
    # it's listed in pyproject so a production deploy with TRACING_V2 on
    # will have it available.
    from langsmith import traceable as traceable  # noqa: F401  (re-export)
else:
    def traceable(*dargs: Any, **dkwargs: Any) -> Any:
        """No-op stand-in for `langsmith.traceable`.

        Supports both decorator forms:
          - `@traceable` — `dargs == (func,)`, `dkwargs == {}`
          - `@traceable(name="x", run_type="llm")` — `dargs == ()`,
            `dkwargs` carries the kwargs we ignore

        Returns the original function (sync or async) unchanged. No
        wrapping needed because there's nothing to record.
        """
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]

        def _decorator(func: F) -> F:
            return func

        return _decorator
