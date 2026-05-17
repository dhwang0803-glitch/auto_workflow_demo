# Refactor Agent Instructions

## Role
Runs only after every test PASSes. Improves code quality while keeping all tests green (TDD Refactor step).

---

## Core principles

1. **Keep tests green**: after refactoring, always re-run the full test suite and confirm PASS
2. **No behavior change**: do not make changes that alter the runtime result
3. **Scope restriction**: modify only `src/` files for the requested Phase
4. **Small steps**: improve one thing at a time, verify tests, then move on

---

## Items to consider

### Python code quality
- [ ] Duplicate search logic → unify into a shared function
- [ ] Missing error handling (try-except, fallback strategy)
- [ ] Hardcoded values → constants or environment variables
- [ ] Clarity of log messages (which source failed)

### Performance
- [ ] API caching correctness (eliminate duplicate requests for the same asset_nm)
- [ ] Batch size (batch_size) tuning
- [ ] ThreadPoolExecutor max_workers tuning (mind the API rate limit)
- [ ] Remove unnecessary LLM calls (rating is fine as rule-based)

### Data quality
- [ ] Edge-case coverage in `validate_*` functions
- [ ] Appropriateness of `confidence_score` weights
- [ ] NULL handling consistency

---

## Scope restriction

Excluded from refactoring:
- Test files (`tests/` folder)
- PLAN documents (`plans/` folder)
- Skills documents (`skills/` folder)
- Environment configs (`.env`, `config/api_keys.env`)

---

## After refactoring

```
1. Re-run the full test suite
2. Confirm the PASS/FAIL counts match the previous run
3. Write a changelog → hand to Reporter Agent
```

## Format to hand to the Reporter Agent

```
[Refactoring items]
- File: [name]
- Before: [old code/structure summary]
- After: [improved code/structure summary]
- Reason: [why the change]
```
