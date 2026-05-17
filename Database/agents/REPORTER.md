# Reporter Agent Instructions — Database

## Role
After a TDD cycle completes, produces the per-PLAN results report.

---

## Report location

```
Database/reports/PLAN_NN_report.md
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

## 3. Schema changes (if applicable)
| Migration file | Change |
|-----------------|----------|

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
| Schema changes | migrations/ directory |
| Refactoring | Refactor Agent |
