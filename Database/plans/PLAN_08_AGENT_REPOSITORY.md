# PLAN_08 — AgentRepository (Database)

> **Branch**: `Database` · **Drafted**: 2026-04-16 · **Status**: Draft
>
> Provides the `AgentRepository` ABC consumed by API_Server PLAN_06
> (Agent management), along with the Postgres implementation and the
> in-memory fake — all delivered from the Database layer.
> The `Agent` DTO and ORM already exist; only the repository is new.

## Scope

- `repositories/base.py` — `AgentRepository` ABC (`register`, `get`, `update_heartbeat`, `list_by_owner`)
- `repositories/agent_repository.py` (new) — Postgres implementation
- `tests/fakes.py` — In-memory fake
- `tests/test_agent_repository.py` (new) — 4 tests

## Acceptance criteria

- [ ] The 4 new tests pass
- [ ] No regression in existing tests
