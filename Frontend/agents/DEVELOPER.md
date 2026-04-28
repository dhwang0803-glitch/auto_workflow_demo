# Developer Agent 지시사항 — Frontend

## 역할
Test Writer Agent가 작성한 Playwright/단위 테스트를 통과하는 최소한의 코드를 구현한다 (TDD Green 단계). 과도한 설계나 불필요한 기능을 추가하지 않는다.

---

## 구현 원칙

1. **테스트 통과 최우선**: 현재 실패하는 테스트를 통과시키는 것만 구현한다
2. **최소 구현**: 테스트를 통과하는 가장 단순한 컴포넌트/스토어/클라이언트를 작성한다
3. **CLAUDE.md 준수**: `Frontend/CLAUDE.md` 파일 위치 규칙 (App Router / `src/lib` / `src/store` / `src/components`) 을 벗어나지 않는다
4. **함수 증식 금지**: 1회용 헬퍼/thin wrapper 만들지 않는다. 3줄 중복이 추상화보다 낫다

---

## 파일 위치

| 파일 종류 | 위치 |
|-----------|------|
| 라우트 / 페이지 | `src/app/<route>/page.tsx` (App Router — `pages/` 사용 X) |
| UI 컴포넌트 | `src/components/<domain>/*.tsx` |
| API 클라이언트 + 도메인 유틸 | `src/lib/*.ts` (NOT `src/services/`) |
| Zustand 스토어 | `src/store/*-store.ts` |
| Cross-cutting Provider | `src/providers/*.tsx` |
| Playwright 스펙 | `tests/*.spec.ts` |

**`Frontend/` 루트 또는 `src/` 루트에 직접 `.ts`/`.tsx` 파일 생성 금지.**

---

## 상태 / 캐시 분리

```typescript
// 클라이언트 비즈니스 상태 → Zustand
import { create } from "zustand";
export const useEditorStore = create<EditorState>()((set) => ({ ... }));

// 서버 캐시 → React Query
import { useQuery } from "@tanstack/react-query";
const { data } = useQuery({ queryKey: ["workflows"], queryFn: listWorkflows });
```

- **두 곳에 같은 데이터 두지 않는다** — 워크플로우 메타는 React Query, 편집 중인 dirty 그래프는 editor-store
- React Query 가 fetch/캐시 무효화 책임 — 직접 `useEffect` + `fetch` 작성 금지

---

## API 클라이언트 패턴

```typescript
// src/lib/api.ts 의 apiFetch 래퍼만 사용
import { apiFetch } from "./api";
export const listSkills = (status?: SkillStatus) =>
  apiFetch<SkillListResponse>(`/api/v1/skills${qs}`);
```

- `NEXT_PUBLIC_DEV_TOKEN` 자동 첨부 — 호출부에서 헤더 직접 설정 금지
- 에러는 `ApiError` (status + message) 로 throw — 호출부가 instanceof 로 분기

---

## SSE 패턴

`composer.ts` 가 `composeStream` 의 표준 구현. EventSource 가 헤더 미지원이라 fetch + ReadableStream 사용. 새 SSE 엔드포인트 추가 시 같은 패턴:

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

## 컴포넌트 작성 원칙

1. **`"use client";` 명시**: 인터랙션이 있는 컴포넌트는 첫 줄에. 서버 컴포넌트가 기본
2. **이벤트 핸들러는 `void` prefix** 로 promise 무시 명시: `onClick={() => void submitAnswer()}`
3. **JSX 텍스트의 apostrophe** 는 `&apos;` escape (next lint `react/no-unescaped-entities` 차단)
4. **Tailwind 유틸리티 클래스** 사용 — `*.module.css` 신규 작성 X
5. **data-testid 부여**: 테스트가 의존하는 노드에 `data-testid="<scope>-<name>"` 일관 명명

---

## React 렌더 최적화 (필요 시)

- Zustand selector 패턴: `useStore((s) => s.field)` — 컴포넌트별 부분 구독으로 불필요 재렌더 차단
- `useMemo` / `useCallback` 은 **렌더 프로파일에서 hot-path 확인 후** 적용. 무차별 wrapping 금지
- 큰 리스트는 가상화 검토 (현재 워크플로우/스킬 리스트는 작아서 X)

---

## UI 텍스트 언어

**모든 사용자 화면 텍스트는 영어로 작성** (`feedback_hackathon_ui_english.md`). Kaggle 심사위원 + LLM 응답이 영어이므로 일관성 유지. 새 컴포넌트 첫 줄부터 영어, 한국어 라벨 후행 정정 금지.

---

## 구현 완료 후 자가 점검

- [ ] 하드코딩된 API 키, 시크릿, 실제 IP 없음
- [ ] `localStorage` 에 토큰 저장 안 함 (메모리 / httpOnly 쿠키만)
- [ ] `NEXT_PUBLIC_*` 환경변수에 시크릿 넣지 않음 (클라이언트 번들 인라인됨)
- [ ] `dangerouslySetInnerHTML` 사용 안 함 (LLM 응답 raw 삽입 금지)
- [ ] 새 컴포넌트가 `"use client"` 명시됐고 testid 부여됨
- [ ] 1회용 헬퍼 / thin wrapper 만들지 않음
- [ ] UI 텍스트 모두 영어 (한국어 잔존 0)
- [ ] `tsc --noEmit` / `next lint` 모두 green
