"""PLAN_12 — TransformNode tests."""
from __future__ import annotations

import pytest

from src.nodes.transform import TransformNode


@pytest.fixture
def node():
    return TransformNode()


async def test_simple_mapping(node):
    result = await node.execute(
        {"name": "alice"},
        {"mapping": {"display_name": "{input.name}"}},
    )
    assert result == {"display_name": "alice"}


async def test_nested_path_substitution(node):
    result = await node.execute(
        {"user": {"profile": {"name": "bob", "age": 30}}},
        {
            "mapping": {
                "n": "{input.user.profile.name}",
                "a": "{input.user.profile.age}",
            }
        },
    )
    assert result == {"n": "bob", "a": 30}


async def test_static_values_preserved(node):
    result = await node.execute(
        {"x": 1},
        {
            "mapping": {
                "source": "airtable",
                "count": 42,
                "active": True,
                "ref": "{input.x}",
            }
        },
    )
    assert result == {"source": "airtable", "count": 42, "active": True, "ref": 1}


async def test_missing_key_uses_default(node):
    result = await node.execute(
        {"present": "here"},
        {
            "mapping": {"missing": "{input.absent}", "present": "{input.present}"},
            "defaults": {"missing": "fallback"},
        },
    )
    assert result == {"missing": "fallback", "present": "here"}
