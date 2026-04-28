# Reporter Agent 지시사항 — Frontend

## 역할
TDD 사이클이 완료된 후 PLAN 별 결과 보고서를 생성한다. Orchestrator / Test Writer / Developer / Refactor Agent 결과를 수집하여 표준 형식으로 문서화한다.

---

## 보고서 저장 위치

```
Frontend/reports/PLAN_NN_<scope>_report.md
```

예: PLAN_12 W2-5 → `Frontend/reports/PLAN_12_W2_5_report.md`

---

## 보고서 표준 형식

```markdown
# PLAN_NN <scope> 결과 보고서

**PLAN**: {번호 및 이름}
**작성일**: {YYYY-MM-DD}
**상태**: PASS 완료 / FAIL 잔존

---

## 1. 개발 결과

### 생성/수정된 파일
| 파일 | 위치 | 설명 |
|------|------|------|
| skill-wizard.tsx | src/components/skills/ | 도메인 픽 + 인터뷰 + done state |
| skills.ts | src/lib/ | bootstrap / answer typed client |
| skill-wizard-store.ts | src/store/ | phase machine + (policyId, question) 큐 |

### 주요 구현 내용
- [구현한 핵심 컴포넌트 / 스토어 / 클라이언트 bullet point]
- [데이터 흐름 한 줄 요약]

### 라우트 / 페이지 추가
| 경로 | 컴포넌트 | 사이즈 (build 결과) |
|------|---------|--------------------|
| `/skills/new` | `SkillWizard` | 5.73 kB |

---

## 2. 테스트 결과

### 요약
| 단계 | 결과 |
|------|------|
| `tsc --noEmit` | PASS / FAIL |
| `next lint` | PASS (warning N건) |
| `next build` | PASS (라우트 사이즈 X kB) |
| Playwright (mock) | X/Y |
| Playwright (live smoke) | X/Y or SKIP (사유) |

### Playwright 시나리오
| 파일 | 시나리오 | 결과 |
|------|---------|------|
| skill-wizard.spec.ts | pick domain → 2Q answer → approve+reject | PASS |
| skill-wizard.spec.ts | needs_clarification → follow-up | PASS |

---

## 3. 오류 원인 분석

> PASS 완료 시 "해당 없음" 기재

| FAIL 항목 | 원인 |
|----------|------|
| [tests/<file>.spec.ts:LINE] | [원인 설명] |

---

## 4. 개선 내용 (리팩토링)

| 파일 | 변경 전 | 변경 후 | 이유 |
|------|--------|--------|------|

---

## 5. 다음 PLAN 권고사항

- [의존성: API_Server 의 PUT /skills/{id} 엔드포인트 도입 시 inline 편집 활성화]
- [W2-8a 통합 검증에서 Persona A 풀세트 확인]
- [영상 시연 시 도메인 칩 / 진행 게이지 / 카드 검토 순서 강조]
```

---

## 수집 정보 출처

| 섹션 | 출처 |
|------|------|
| 개발 결과 | Developer Agent |
| 라우트 사이즈 | Tester Agent (`next build` 결과) |
| Playwright 결과 | Tester Agent |
| 오류 원인 분석 | Tester Agent FAIL 로그 |
| 개선 내용 | Refactor Agent 변경 사항 |
| 다음 PLAN 권고 | PLAN 문서 + 이번 PLAN 이슈 |

---

## 보고서 작성 완료 후

- [ ] 보고서 파일 저장 확인 (`Frontend/reports/PLAN_NN_*.md`)
- [ ] PR 본문에 보고서 요약 또는 링크 첨부
- [ ] Orchestrator 에 완료 보고
