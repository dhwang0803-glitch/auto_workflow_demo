"""Test fixtures + environment hygiene shared across the AI_Agent suite."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_agent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default AGENT_BEARER_TOKEN to empty for every test.

    pydantic-settings reads `.env` when constructing `Settings()`, and a
    populated `.env` (e.g. after running `scripts/sync-env-secrets.py`
    locally) leaks the bearer token into tests that never opted into
    bearer-auth. Tests that DO want bearer auth wired override it via
    their own `monkeypatch.setenv` call (search for AGENT_BEARER_TOKEN
    in tests/* — the bearer-auth tests already follow this pattern).

    Memory `feedback_pydantic_settings_test.md`: when one behavior depends
    on N env fields, override every one of them. Doing it autouse here
    means tests don't have to remember the rule for the common case.
    """
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "")
