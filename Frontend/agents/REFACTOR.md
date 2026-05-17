# Refactor Agent Instructions — Frontend

## Role
Runs only after all tests (typecheck + lint + build + Playwright) PASS. Improves code quality while preserving behavior (TDD Refactor step).

---

## Core principles

1. **Keep tests green**: after refactoring, always re-run typecheck + lint + Playwright
2. **No behavior change**: do not make changes that alter the runtime result
3. **Scope restriction**: modify only `src/` files of the requested PLAN
4. **Small steps**: improve one thing at a time → verify → next

---

## Items to consider

### TypeScript code quality
- [ ] `any` usage → precise types, or `unknown` + narrowing
- [ ] One-shot helper functions → inline (3 lines of duplication beat premature abstraction)
- [ ] Duplicate fetch / route definitions → unify via `lib/api.ts`'s apiFetch wrapper
- [ ] Hardcoded path strings → constants or helpers

### React components
- [ ] Component over 200 lines → split into child components (do not split for one-shot use)
- [ ] Unnecessary `useEffect` (derived values can be computed in render)
- [ ] Too many `useState` → consider promoting to a Zustand store (when shared cross-component)
- [ ] Missing data-testid — set on selectors that tests depend on
- [ ] Any `dangerouslySetInnerHTML` found → remove immediately

### Zustand stores
- [ ] Component subscribes to the entire store state → partial-subscribe via selector (`useStore((s) => s.field)`)
- [ ] Calling other actions inside an action → consolidate into the set callback
- [ ] Same data in both store and React Query → consolidate to one

### Tailwind / styling
- [ ] Same class sequence repeated 3+ times → extract a component (`StatusPill`-style 1-line components are fine)
- [ ] Arbitrary colors (`bg-[#abc]`) → design tokens or the nearest Tailwind palette
- [ ] Inline style usage → only when needed (e.g., dynamic width %)

### Performance (only when needed)
- [ ] Large-list render hot-path → confirm in React DevTools Profiler, then apply `memo` / `useMemo`
- [ ] React Query `staleTime` / `cacheTime` tuning (static data like the catalog)

### Consistency
- [ ] Confirm new UI text is in English (`feedback_hackathon_ui_english.md`)
- [ ] Consistent data-testid naming (`<scope>-<name>`)
- [ ] Unified error message format (`HTTP {status}: {message}`)

---

## Scope excluded

- Test files (`tests/`) — Test Writer Agent's territory
- PLAN documents (`plans/`)
- Env configs (`.env.local`, `next.config.mjs`, `tsconfig.json`)
- `playwright.config.ts`

---

## After refactoring

```
1. taskkill //F //IM node.exe → clean up zombies
2. Re-run tsc --noEmit / next lint / next build / playwright (mock)
3. Confirm PASS/FAIL counts match the previous run (also compare route sizes)
4. Write a changelog → hand to Reporter Agent
```

## Format to hand to the Reporter Agent

```
[Refactoring items]
- File: src/<...>
- Before: [old code/structure summary]
- After: [improved code/structure summary]
- Reason: [why — readability / dedup / stricter types, etc.]
- Route-size delta: previous X.XX kB → new X.XX kB
```
