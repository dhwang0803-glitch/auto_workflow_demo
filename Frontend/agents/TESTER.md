# Tester Agent 지시사항 — Frontend

## 역할
Developer Agent가 구현을 마친 후, TypeScript 타입체크 + ESLint + Next.js production build + Playwright 테스트를 실행하고 결과를 수집한다.

---

## 실행 환경

- Node.js 20+ (LTS)
- pnpm 또는 npm (lockfile 은 pnpm-lock.yaml)
- Windows 11 (PowerShell 또는 Git Bash)
- Playwright 브라우저 (`npx playwright install` 1회)

---

## 프로세스 관리 규칙 (MANDATORY)

1. **이전 실행 프로세스 kill 후 재실행** — Playwright 의 dev server 가 좀비로 남으면 포트 충돌
   ```bash
   # Git Bash / PowerShell
   taskkill //F //IM node.exe 2>/dev/null
   ```
2. background 실행 금지 — foreground 에서 결과를 즉시 확인
3. `playwright test --reuse-existing-server` 옵션이 기본 (`playwright.config.ts`) — 이미 떠있는 dev server 재사용

---

## 검증 순서 (PR 오픈 전 필수, 모두 green)

```bash
# 1. 타입체크 (5초)
npx tsc --noEmit

# 2. 린트 (5초) — react/no-unescaped-entities 등 자주 걸림
npx next lint

# 3. 프로덕션 빌드 (~30초) — 라우트 사이즈 확인용
npx next build

# 4. Playwright (mock 기반, ~15초)
npx playwright test tests/ai-composer.spec.ts tests/skill-wizard.spec.ts
```

`tsc` / `lint` 실패는 즉시 수정. `build` 실패는 보통 Server/Client 컴포넌트 경계 문제 (예: `"use client"` 누락).

---

## Live 통합 테스트 (선택)

`smoke.spec.ts` 는 API_Server uvicorn :8000 + Postgres + dev token 필요:

```bash
# API_Server 띄우기 (별도 터미널)
cd ../API_Server
uvicorn app.main:app --reload --port 8000

# Frontend dev 띄우고 smoke 실행
cd ../Frontend
npx playwright test tests/smoke.spec.ts
```

W2-8a 같은 통합 검증 단계에서만 실행. 일반 PR 에선 skip — 환경 의존성 때문에 PR CI 에 부담.

---

## 결과 보고 형식

```
[Tester 실행 결과]
- 실행 환경: Node {버전}, Playwright {버전}
- 타입체크: PASS / FAIL (에러 N건)
- 린트: PASS / FAIL (warning N건 / error N건)
- 빌드: PASS / FAIL (라우트 사이즈 변동)
- Playwright (mock): X/Y 통과
- Playwright (live, smoke): X/Y 또는 SKIP (사유)

FAIL 항목:
- [tests/<file>.spec.ts:LINE] [에러 메시지 요약]
- [src/<file>:LINE] [tsc 에러 코드 + 메시지]

다음 액션:
- 모두 PASS → 커밋 진행
- FAIL 존재 → kill node.exe → 원인 수정 → 재실행
```

---

## 주의사항

1. dev server 가 stale 한 상태면 lint/build 실패가 가짜로 보일 수 있음 — 의심되면 `.next/` 삭제 후 재실행
2. `pnpm-lock.yaml` 변경 없이 새 의존성 install 시도 X — Frontend 는 lockfile 이 진실 공급원
3. Playwright 가 `webServer` 시동 시 `Cross origin request detected from 127.0.0.1` 경고 — 무해, 무시
4. live 통합 테스트 실패 (`ECONNREFUSED 127.0.0.1:8000`) 는 API_Server 미가동 — Frontend 측 회귀 아님
5. `npx next build` 결과의 라우트 사이즈는 보고서에 기록 (변경 추세 추적)
