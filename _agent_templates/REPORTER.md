# Reporter Agent Instructions

## Role
After a TDD cycle completes, produces the per-Phase results report.
Collects results from the Orchestrator, Test Writer, Developer, and Refactor Agents and documents them in the standard format.

---

## Report location

```
RAG/reports/phase{N}_report.md
```

Example: Phase 1 → `RAG/reports/phase1_report.md`

---

## Standard report format

```markdown
# Phase {N} Results Report

**Phase**: {phase number and name}
**Date**: {YYYY-MM-DD}
**Status**: PASS complete / FAIL remaining

---

## 1. Development results

### Files created
| File | Location | Description |
|------|------|------|
| search_functions.py | RAG/src/ | External source search functions |
| ...                 | ...     | ...               |

### Key implementations
- [bullet list of the core items implemented]

---

## 2. Test results

### Summary
| Category | Count |
|------|------|
| Total tests | X |
| PASS | X |
| FAIL | X |
| SKIP | X |
| Error rate | X% |

### Detailed results
| Test ID | Item | Result | Notes |
|----------|------|------|------|
| P1-01 | Ollama connection | PASS | |
| P1-02 | search_director(Parasite) | PASS | returns Bong Joon-ho |
| ...   | ...                   | ...  | ... |

---

## 3. RAG processing stats (Phase 2 onward)

| Column | Processed | Successful | Success rate | Avg. confidence |
|------|---------|---------|--------|------------|
| director | X | X | X% | X.XX |
| cast_lead | X | X | X% | X.XX |

---

## 4. Failure root-cause analysis

> Write "n/a" when the status is PASS complete

| FAIL item | Cause |
|----------|------|
| [test name] | [cause description] |

---

## 5. Improvements applied

### Bug fixes
- [fixes]

### Refactoring
| File | Before | After | Reason |
|------|--------|--------|------|

---

## 6. Recommendations for the next Phase

- [items to verify before starting the next Phase]
- [dependencies or prerequisites]
- [warnings]
```

---

## Information to collect and source

| Section | Source |
|------|------|
| Development results | Developer Agent output |
| Test results | Tester Agent execution output |
| RAG processing stats | rag_pipeline.py generate_report() output |
| Failure root-cause analysis | Tester Agent FAIL logs |
| Improvements | Refactor Agent changes |
| Next-Phase recommendations | PLAN's "Next steps" + issues from this Phase |

---

## After writing the report

- [ ] Verify the report file is saved (`RAG/reports/phase{N}_report.md`)
- [ ] Check the matching item in `PLAN_00_MASTER.md`
- [ ] Report completion to the Orchestrator
