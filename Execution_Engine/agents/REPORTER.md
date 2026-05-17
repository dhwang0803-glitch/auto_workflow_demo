# Reporter Agent Instructions — Execution_Engine

## Role
After a TDD cycle completes, produces the per-PLAN results report.

---

## Report location

```
Execution_Engine/reports/PLAN_NN_report.md
```

---

## Report format

```markdown
# PLAN_NN Results Report

**PLAN**: {number and name}
**Date**: {YYYY-MM-DD}
**Status**: PASS / FAIL

## 1. Development results
| File | Location | Description |
|------|------|------|

## 2. Test results
| Total | PASS | FAIL | Duration |
|------|------|------|----------|

## 3. Node/module status (if applicable)
| Node type | File | Tests |
|----------|------|--------|

## 4. Failure root-cause analysis
> "n/a" if PASS

## 5. Refactoring
| File | Before | After | Reason |
|------|--------|--------|------|

## 6. Next-PLAN recommendations
```

---

## Information sources

| Section | Source |
|------|------|
| Development results | Developer Agent |
| Test results | Tester Agent |
| Node status | NodeRegistry + src/nodes/ |
| Refactoring | Refactor Agent |
