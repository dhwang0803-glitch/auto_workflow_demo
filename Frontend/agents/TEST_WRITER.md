# Test Writer Agent Instructions — Frontend

## Role
Writes failing Playwright tests before implementation (TDD Red step).

---

## Test-writing principles

1. Write the test before any implementation exists
2. Each test verifies exactly one user scenario or state transition
3. **route mock first**: fake API responses with `page.route(url, fulfill)` — zero backend dependency
4. Select by data-testid (text-based selectors break under i18n / styling changes)
5. Split assertions so the failure message points at the cause (do not stuff multiple conditions into one expect)

---

## Test file location

```
Frontend/tests/<feature>.spec.ts
```

| File | Subject |
|------|----------|
| `smoke.spec.ts` | live API_Server integration (workflow create → execute → result). Requires a dev token |
| `ai-composer.spec.ts` | route mock 4 SSE scenarios (stream→draft→apply / clarify / refine / error) |
| `skill-wizard.spec.ts` | route mock 5 wizard scenarios (interview → approve+reject / follow-up / no-gaps / 502 / approve 500 retry) |

---

## Example — route mock + state transition

```typescript
import { test, expect } from "@playwright/test";

test("Skill wizard: pick domain → answer → done", async ({ page }) => {
  await page.route("**/api/v1/skills/bootstrap", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.domain).toBe("ecommerce");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: body.session_id,
        domain: "ecommerce",
        missing: [/* ... */],
      }),
    });
  });

  await page.goto("/skills/new");
  await page.getByTestId("domain-chip-ecommerce").click();
  await expect(page.getByTestId("wizard-progress")).toContainText("0 / 2");
});
```

---

## SSE response mock pattern

```typescript
const sse = (frames: Array<{ event: string; data: unknown }>): string =>
  frames
    .map((f) => `event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`)
    .join("");

await page.route("**/api/v1/ai/compose**", (route) => {
  const body = sse([
    { event: "session", data: { session_id: "..." } },
    { event: "rationale_delta", data: { token: "..." } },
    { event: "result", data: { /* full result */ } },
  ]);
  route.fulfill({ status: 200, contentType: "text/event-stream", body });
});
```

Even when the mock returns every frame at once, the client parser splits on `\n\n` and dispatches the same way — same path as real SSE.

---

## Required test categories

### PLAN_01 (Workflow Editor)
- Workflow create / node drag / save / execute / ResultDrawer state transitions
- DAG serialization round-trip (`toPayload()` output matches the backend schema)
- Fallback UI when the node catalog fetch fails

### PLAN_02 (AI Composer)
- Non-stream `composeJSON` 4 intents (clarify / draft / refine + error)
- SSE streaming tokens → typing bubble → final result promotion
- Apply draft → editor-store graph update
- In-band `event: error` frame → banner displayed

### PLAN_12 W2-5/W2-6 (Skill Wizard)
- Pick domain → bootstrap → flat queue → asking → answer → done
- needs_clarification → follow-up question added to queue → second answer → new draft
- approve / reject happy path (status pill transitions + controls disappear)
- approve API failure → action-error displayed + controls remain
- bootstrap 502 → error banner + return to Start over

---

## Result-collection format

```
Total tests: X
PASS: X
FAIL: X

FAIL list:
- [tests/<file>.spec.ts:LINE]: [failure message summary]
```

---

## Cautions

1. Run mock-based tests separately from `smoke.spec.ts` (which requires live API_Server). For PR verification run mocks only; do integration in a separate step like W2-8a
2. Use `expect(page.locator(...)).toHaveCount(N)` to count; missing `await` causes races
3. Do not mix Korean/English text — implementation + tests use the same language (currently English)
4. Keep `page.getByRole("button", { name: "..." })` names consistent and in English
