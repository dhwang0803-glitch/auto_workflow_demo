# PLAN_08 — AgentRepository (Database)

> **브랜치**: `Database` · **작성일**: 2026-04-16 · **상태**: Draft
>
> API_Server PLAN_06 (Agent 관리)이 소비할 `AgentRepository` ABC,
> Postgres 구현, InMemory fake 를 Database 계층에서 제공한다.
> `Agent` DTO 와 ORM 은 이미 존재 — 레포지토리만 추가.

## 범위

- `repositories/base.py` — `AgentRepository` ABC (`register`, `get`, `update_heartbeat`, `list_by_owner`)
- `repositories/agent_repository.py` 신규 — Postgres 구현
- `tests/fakes.py` — InMemory fake
- `tests/test_agent_repository.py` 신규 — 4 테스트

## 수용 기준

- [ ] 신규 4 테스트 통과
- [ ] 기존 테스트 회귀 없음
