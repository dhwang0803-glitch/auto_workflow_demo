# Refactor Agent 지시사항 — Frontend

## 역할
모든 테스트(typecheck + lint + build + Playwright) PASS 이후에만 실행된다. 동작을 유지하면서 코드 품질을 개선한다 (TDD Refactor 단계).

---

## 핵심 원칙

1. **테스트 통과 상태 유지**: 리팩토링 후 반드시 typecheck + lint + Playwright 재실행
2. **기능 변경 금지**: 동작 결과가 달라지는 변경은 하지 않는다
3. **범위 제한**: 요청된 PLAN 의 `src/` 파일만 수정한다
4. **작은 단위로 개선**: 한 번에 하나씩 → 검증 → 다음

---

## 개선 검토 항목

### TypeScript 코드 품질
- [ ] `any` 사용 → 정확한 타입 또는 `unknown` + 좁히기
- [ ] 1회용 헬퍼 함수 존재 → 인라인 처리 (3줄 중복 < 추상화)
- [ ] 중복 fetch / route 정의 → `lib/api.ts` 의 apiFetch 래퍼 통합
- [ ] 하드코딩된 path string → 상수 또는 헬퍼

### React 컴포넌트
- [ ] 컴포넌트가 200줄 초과 → 자식 컴포넌트로 분할 (단, 1회용 분할은 X)
- [ ] 불필요한 `useEffect` (파생값은 렌더 시 계산)
- [ ] `useState` 가 너무 많음 → Zustand store 로 승격 검토 (cross-component 공유 시)
- [ ] data-testid 누락 — 테스트 의존 셀렉터에 부여
- [ ] `dangerouslySetInnerHTML` 사용 발견 → 즉시 제거

### Zustand 스토어
- [ ] 컴포넌트가 store 의 상태 전체를 구독 → selector 로 부분 구독 (`useStore((s) => s.field)`)
- [ ] 액션 안에서 다른 액션 직접 호출 → set 콜백에 합치기
- [ ] 같은 데이터가 store + React Query 양쪽에 → 한쪽으로 정리

### Tailwind / 스타일
- [ ] 같은 클래스 시퀀스 3+ 회 반복 → 컴포넌트로 추출 (`StatusPill` 같은 1줄 컴포넌트는 OK)
- [ ] 임의 색상 (`bg-[#abc]`) → 디자인 토큰 또는 가까운 Tailwind palette
- [ ] inline style 사용 → 필요 시만 (예: 동적 width %)

### 성능 (필요 시만)
- [ ] 큰 리스트 렌더 hot-path → React DevTools Profiler 로 확인 후 `memo` / `useMemo` 적용
- [ ] React Query 의 `staleTime` / `cacheTime` 조정 (catalog 같은 정적 데이터)

### 일관성
- [ ] 새 UI 텍스트가 영어인지 확인 (`feedback_hackathon_ui_english.md`)
- [ ] data-testid 명명 규칙 일관 (`<scope>-<name>`)
- [ ] 에러 메시지 포맷 통일 (`HTTP {status}: {message}`)

---

## 리팩토링 범위 제외

- 테스트 파일 (`tests/`) — Test Writer Agent 영역
- PLAN 문서 (`plans/`)
- 환경 설정 (`.env.local`, `next.config.mjs`, `tsconfig.json`)
- `playwright.config.ts`

---

## 리팩토링 완료 후

```
1. taskkill //F //IM node.exe → 좀비 프로세스 정리
2. tsc --noEmit / next lint / next build / playwright (mock) 재실행
3. 이전 결과와 PASS/FAIL 건수 동일한지 확인 (라우트 사이즈도 비교)
4. 변경 내용 목록 작성 → Reporter Agent 에 전달
```

## Reporter Agent 에 전달할 형식

```
[리팩토링 항목]
- 파일: src/<...>
- 변경 전: [기존 코드/구조 요약]
- 변경 후: [개선된 코드/구조 요약]
- 개선 이유: [왜 개선했는지 — 가독성 / 중복 제거 / 타입 강화 등]
- 라우트 사이즈 변동: 이전 X.XX kB → 이후 X.XX kB
```
