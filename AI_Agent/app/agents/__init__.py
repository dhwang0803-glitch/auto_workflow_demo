"""Closed-loop self-evaluating agents (PLAN_13).

Currently scope-limited to `policy_extract` per PLAN_13 §3 — other LLM
call sites in `services/` (compose, gap_analyze, answers_to_skill) take
fixed-shape inputs where self-evaluation has weak utility.

The graph runtime is langgraph; tracing flows through LangSmith via the
`tracing.py` no-op fallback so unit tests stay hermetic.
"""
