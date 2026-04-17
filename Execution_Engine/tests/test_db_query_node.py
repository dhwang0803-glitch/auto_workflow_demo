"""PLAN_09 — DBQueryNode tests (AsyncMock 으로 asyncpg 패치)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from src.nodes.db_query import DBQueryNode


@pytest.fixture
def node():
    return DBQueryNode()


def _mock_connection(
    *,
    fetch_return=None,
    execute_return=None,
    fetch_raises: Exception | None = None,
) -> AsyncMock:
    conn = AsyncMock()
    if fetch_raises is not None:
        conn.fetch.side_effect = fetch_raises
    else:
        conn.fetch.return_value = fetch_return or []
    conn.execute.return_value = execute_return or "SELECT 0"
    conn.close = AsyncMock()
    return conn


def _row(**kwargs) -> MagicMock:
    """asyncpg.Record stand-in that responds to dict(r)."""
    r = MagicMock()
    r.__iter__ = lambda self: iter(kwargs.items())
    r.keys = lambda: list(kwargs.keys())
    r.__getitem__ = lambda self, k: kwargs[k]
    return r


async def test_select_returns_rows(node, monkeypatch):
    mock_conn = _mock_connection(
        fetch_return=[_row(id=1, name="alice"), _row(id=2, name="bob")]
    )
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=mock_conn))

    result = await node.execute(
        {},
        {
            "connection_url": "postgresql://u:p@h:5432/db",
            "query": "SELECT id, name FROM users",
        },
    )

    assert result["row_count"] == 2
    assert result["rows"] == [
        {"id": 1, "name": "alice"},
        {"id": 2, "name": "bob"},
    ]
    mock_conn.close.assert_awaited_once()


async def test_insert_returns_affected_count(node, monkeypatch):
    mock_conn = _mock_connection(execute_return="INSERT 0 3")
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=mock_conn))

    result = await node.execute(
        {},
        {
            "connection_url": "postgresql://u:p@h:5432/db",
            "query": "INSERT INTO events (kind) VALUES ($1), ($2), ($3)",
            "parameters": ["a", "b", "c"],
        },
    )

    assert result == {"rows": [], "row_count": 3}
    mock_conn.execute.assert_awaited_once()
    args = mock_conn.execute.await_args.args
    assert args[0].startswith("INSERT")
    assert args[1:] == ("a", "b", "c")


async def test_parameters_passed_through(node, monkeypatch):
    mock_conn = _mock_connection(fetch_return=[_row(id=42)])
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=mock_conn))

    await node.execute(
        {},
        {
            "connection_url": "postgresql://u:p@h:5432/db",
            "query": "SELECT id FROM users WHERE created_at > $1 AND tier = $2",
            "parameters": ["2026-01-01", "heavy"],
        },
    )

    args = mock_conn.fetch.await_args.args
    assert "$1" in args[0] and "$2" in args[0]
    assert args[1:] == ("2026-01-01", "heavy")


async def test_returning_clause_uses_fetch(node, monkeypatch):
    """INSERT ... RETURNING should go through fetch(), not execute(), so
    callers get the returned rows."""
    mock_conn = _mock_connection(fetch_return=[_row(id=7)])
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=mock_conn))

    result = await node.execute(
        {},
        {
            "connection_url": "postgresql://u:p@h:5432/db",
            "query": "INSERT INTO t (x) VALUES ($1) RETURNING id",
            "parameters": [1],
        },
    )

    mock_conn.fetch.assert_awaited_once()
    mock_conn.execute.assert_not_called()
    assert result["rows"] == [{"id": 7}]


async def test_connection_always_closed_on_failure(node, monkeypatch):
    mock_conn = _mock_connection(fetch_raises=RuntimeError("query boom"))
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=mock_conn))

    with pytest.raises(RuntimeError, match="query boom"):
        await node.execute(
            {},
            {
                "connection_url": "postgresql://u:p@h:5432/db",
                "query": "SELECT 1",
            },
        )
    mock_conn.close.assert_awaited_once()
