# Developer Agent Instructions — Frontend

## Role
Implements the minimum code that passes the Playwright / unit tests written by the Test Writer Agent (TDD Green step). Avoids over-design and adds no unnecessary features.

---

## Implementation principles

1. **Passing tests first**: implement only what is needed to pass the currently failing tests
2. **Minimum implementation**: write the simplest component / store / client that passes the tests
3. **Honor CLAUDE.md**: do not stray from the file-location rules (App Router / `src/lib` / `src/store` / `src/components`) in `Frontend/CLAUDE.md`
4. **No function sprawl**: do not create one-shot helpers or thin wrappers. 3 lines of duplication beats premature abstraction

---

## File locations

| File kind | Location |
|-----------|------|
| Routes / pages | `src/app/<route>/page.tsx` (App Router — do not use `pages/`) |
| UI components | `src/components/<domain>/*.tsx` |
| API client + domain utils | `src/lib/*.ts` (NOT `src/services/`) |
| Zustand store | `src/store/*-store.ts` |
| Cross-cutting Provider | `src/providers/*.tsx` |
| Playwright specs | `tests/*.spec.ts` |

**Do not create `.ts` / `.tsx` files directly at the `Frontend/` root or the `src/` root.**

---

## State / cache split

```typescript
// Client business state → Zustand
import { create } from "zustand";
export const useEditorStore = create<EditorState>()((set) => ({ ... }));

// Server cache → React Query
import { useQuery } from "@tanstack/react-query";
const { data } = useQuery({ queryKey: ["workflows"], queryFn: listWorkflows });
```

- **Do not place the same data in both** — workflow metadata lives in React Query; the dirty in-edit graph lives in editor-store
- React Query owns fetch / cache invalidation — do not write your own `useEffect` + `fetch`

---

## API client pattern

```typescript
// use only the apiFetch wrapper in src/lib/api.ts
import { apiFetch } from "./api";
export const listSkills = (status?: SkillStatus) =>
  apiFetch<SkillListResponse>(`/api/v1/skills${qs}`);
```

- `NEXT_PUBLIC_DEV_TOKEN` is attached automatically — do not set headers at the call site
- Errors throw `ApiError` (status + message) — call sites branch via `instanceof`

---

## SSE pattern

`composer.ts`'s `composeStream` is the canonical implementation. EventSource does not support headers, so it uses fetch + ReadableStream. When adding a new SSE endpoint, follow the same pattern:

```typescript
const reader = resp.body!.getReader();
let buffer = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  let idx;
  while ((idx = buffer.indexOf("\n\n")) !== -1) {
    dispatchFrame(buffer.slice(0, idx), handlers);
    buffer = buffer.slice(idx + 2);
  }
}
```

---

## Component conventions

1. **State `"use client";`**: on the first line of components that have interactivity. Server components are the default
2. **Event handlers use the `void` prefix** to ignore the promise explicitly: `onClick={() => void submitAnswer()}`
3. **JSX text apostrophes** must be `&apos;`-escaped (next lint `react/no-unescaped-entities` blocks otherwise)
4. **Use Tailwind utility classes** — do not author new `*.module.css`
5. **Set data-testid**: assign `data-testid="<scope>-<name>"` consistently to nodes that tests depend on

---

## React render optimization (only when needed)

- Zustand selector pattern: `useStore((s) => s.field)` — per-component partial subscription prevents unnecessary re-renders
- Apply `useMemo` / `useCallback` **only after confirming a hot path in the render profile**. No blanket wrapping
- Consider virtualization for large lists (current workflow / skill lists are small, so not yet)

---

## UI text language

**All user-facing text must be written in English** (`feedback_hackathon_ui_english.md`). Kaggle judges and LLM responses are in English, so consistency matters. Write new components in English from the first line — no late corrections from Korean labels.

---

## Post-implementation self-check

- [ ] No hardcoded API keys, secrets, or real IPs
- [ ] Tokens not stored in `localStorage` (memory / httpOnly cookie only)
- [ ] No secrets in `NEXT_PUBLIC_*` env vars (inlined into the client bundle)
- [ ] No `dangerouslySetInnerHTML` (raw LLM-output injection forbidden)
- [ ] New components state `"use client"` and have testids
- [ ] No one-shot helpers / thin wrappers
- [ ] All UI text in English (0 Korean remaining)
- [ ] `tsc --noEmit` / `next lint` both green
