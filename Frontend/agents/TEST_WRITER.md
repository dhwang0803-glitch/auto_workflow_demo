# Test Writer Agent 지시사항 — Frontend

## 역할
구현 전에 실패하는 Playwright 테스트를 먼저 작성한다 (TDD Red 단계).

---

## 테스트 작성 원칙

1. 구현 코드가 없어도 테스트를 먼저 작성한다
2. 각 테스트는 하나의 사용자 시나리오 또는 상태 전이만 검증한다
3. **route mock 우선**: `page.route(url, fulfill)` 로 API 응답을 가짜로 — 백엔드 의존 0
4. data-testid 로 셀렉트 (텍스트 기반은 i18n / 스타일 변경에 깨짐)
5. 실패 메시지가 원인을 가리키도록 assertion 분리 (한 expect 에 여러 조건 병합 X)

---

## 테스트 파일 위치

```
Frontend/tests/<feature>.spec.ts
```

| 파일 | 검증 대상 |
|------|----------|
| `smoke.spec.ts` | live API_Server 통합 (워크플로우 생성→실행→결과). dev token 필요 |
| `ai-composer.spec.ts` | route mock 으로 SSE 4 시나리오 (stream→draft→apply / clarify / refine / error) |
| `skill-wizard.spec.ts` | route mock 으로 wizard 5 시나리오 (interview → approve+reject / follow-up / no-gaps / 502 / approve 500 retry) |

---

## 작성 예시 — route mock + state 전이

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

## SSE 응답 mock 패턴

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

mock 이 한 번에 모든 프레임을 반환해도 client parser 가 `\n\n` 분리해서 동일하게 dispatch — 실 SSE 와 같은 경로.

---

## 필수 테스트 카테고리

### PLAN_01 (Workflow Editor)
- 워크플로우 생성 / 노드 드래그 / 저장 / 실행 / ResultDrawer 상태 전이
- DAG 직렬화 라운드트립 (`toPayload()` 결과가 백엔드 schema 와 일치)
- 노드 카탈로그 fetch 실패 시 폴백 UI

### PLAN_02 (AI Composer)
- non-stream `composeJSON` 4 intent (clarify / draft / refine + error)
- SSE 스트리밍 토큰 → typing bubble → 최종 result 승격
- Apply draft → editor-store 에 그래프 반영
- in-band `event: error` 프레임 → 배너 표시

### PLAN_12 W2-5/W2-6 (Skill Wizard)
- 도메인 픽 → bootstrap → flat queue → asking → answer → done
- needs_clarification → follow-up question 큐 추가 → 두번째 답변 → 새 draft
- approve / reject 골든 패스 (status pill 전이 + 컨트롤 사라짐)
- approve API 실패 → action-error 표시 + 컨트롤 잔존
- bootstrap 502 → 에러 배너 + Start over 복귀

---

## 테스트 결과 수집 형식

```
전체 테스트: X건
PASS: X건
FAIL: X건

FAIL 목록:
- [tests/<file>.spec.ts:LINE]: [실패 메시지 요약]
```

---

## 주의사항

1. live API_Server 가 필요한 `smoke.spec.ts` 는 mock 기반과 분리해서 실행 (PR 검증 시 mock 만 돌리고 통합은 W2-8a 같은 별도 단계)
2. `expect(page.locator(...)).toHaveCount(N)` 로 갯수 검증, `await` 누락 시 race
3. 텍스트가 한국어/영어 혼재되지 않도록 — 구현 + 테스트 같은 언어 (현재 영어)
4. `page.getByRole("button", { name: "..." })` 의 name 도 영어 일관 유지
