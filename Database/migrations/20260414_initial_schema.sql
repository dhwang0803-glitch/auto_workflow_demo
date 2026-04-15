-- Migration: 20260414_initial_schema
-- PLAN_01 — Core schema bootstrap (users/workflows/nodes/executions).
-- Re-applies schemas/001_core.sql. Kept as a single file so `scripts/migrate.py`
-- can track it by filename; schema drift lives in subsequent YYYYMMDD_*.sql.

\i schemas/001_core.sql
